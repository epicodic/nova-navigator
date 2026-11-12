from __future__ import annotations

import base64
import hashlib
from pathlib import Path

import paramiko
import pytest

from nova_navigator.remotes.known_hosts import (
    KnownHosts,
    KnownHostsLocations,
    fingerprint,
    host_key_name,
    key_type_name,
    read_known_hosts,
)
from tests._utils.known_hosts_files import UNSUPPORTED_LINE, key_line, make_key


def _read(tmp_path: Path, text: str | bytes) -> tuple[Path, KnownHosts]:
    path = tmp_path / "known_hosts"
    path.write_bytes(text.encode() if isinstance(text, str) else text)
    return path, read_known_hosts([path])


def test_plain_entry_is_found_for_its_name(tmp_path: Path) -> None:
    key = make_key()
    _, data = _read(tmp_path, key_line("a.example", key) + "\n")
    assert [k.get_base64() for k in data.keys_for("a.example")] == [key.get_base64()]
    assert data.keys_for("b.example") == []


@pytest.mark.parametrize(
    ("line_names", "lookup"),
    [("a.example,10.0.0.1", "10.0.0.1"), ("[a.example]:2222", "[a.example]:2222")],
)
def test_names_and_bracketed_port_names_match(tmp_path: Path, line_names: str, lookup: str) -> None:
    key = make_key()
    _, data = _read(tmp_path, key_line(line_names, key) + "\n")
    assert len(data.keys_for(lookup)) == 1


def test_bracketed_name_does_not_match_bare_host(tmp_path: Path) -> None:
    key = make_key()
    _, data = _read(tmp_path, key_line("[a.example]:2222", key) + "\n")
    assert data.keys_for("a.example") == []


def test_hashed_entry_matches_only_its_host(tmp_path: Path) -> None:
    key = make_key()
    token = paramiko.HostKeys.hash_host("[a.example]:2222")
    _, data = _read(tmp_path, key_line(token, key) + "\n")
    assert len(data.keys_for("[a.example]:2222")) == 1
    assert data.keys_for("a.example") == []


def test_tabs_multiple_spaces_trailing_comment_and_crlf_are_accepted(tmp_path: Path) -> None:
    key = make_key()
    line = f"a.example\t{key.get_name()}   {key.get_base64()}  # note\r\n"
    _, data = _read(tmp_path, line)
    assert len(data.keys_for("a.example")) == 1
    assert data.issues == []


@pytest.mark.parametrize(
    ("line", "kind"),
    [
        ("@cert-authority *.x.example {name} {b64}", "cert-authority"),
        ("{name_only} {name} !!!bad!!!", "malformed"),
        ("only-two-fields ssh-rsa", "malformed"),
        ("@weird a.example {name} {b64}", "unknown-marker"),
    ],
)
def test_ignored_lines_produce_issue_and_no_entry(tmp_path: Path, line: str, kind: str) -> None:
    key = make_key()
    text = line.format(name=key.get_name(), b64=key.get_base64(), name_only="a.example") + "\n"
    path, data = _read(tmp_path, text)
    assert data.entries == []
    assert [(i.path, i.lineno, i.kind) for i in data.issues] == [(path, 1, kind)]


def test_unsupported_key_type_is_ignored(tmp_path: Path) -> None:
    _, data = _read(tmp_path, UNSUPPORTED_LINE + "\n")
    assert data.entries == []
    assert [i.kind for i in data.issues] == ["unsupported-key-type"]


def test_truncated_blob_and_type_mismatch_are_malformed_not_fatal(tmp_path: Path) -> None:
    key = make_key("rsa")
    other = make_key("ecdsa")
    text = f"a.example {key.get_name()} {key.get_base64()[:20]}\nb.example ssh-rsa {other.get_base64()}\n" + key_line("c.example", key) + "\n"
    _, data = _read(tmp_path, text)
    assert [i.kind for i in data.issues] == ["malformed", "malformed"]
    assert len(data.keys_for("c.example")) == 1


def test_wildcard_and_negated_patterns_never_match(tmp_path: Path) -> None:
    key = make_key()
    _, data = _read(tmp_path, key_line("*.example.com,!bad.example.com", key) + "\n")
    assert data.keys_for("a.example.com") == []
    assert data.keys_for("*.example.com") != []  # literal token only (documented limitation)


def test_revoked_key_is_collected_and_dropped_from_lookup(tmp_path: Path) -> None:
    key = make_key()
    text = key_line("a.example", key) + "\n" + key_line("whatever.example", key, marker="@revoked") + "\n"
    _, data = _read(tmp_path, text)
    assert data.is_revoked(key)
    assert data.keys_for("a.example") == []


def test_revoked_marker_with_comment_and_other_type_is_not_confused(tmp_path: Path) -> None:
    revoked, good = make_key("rsa"), make_key("ecdsa")
    text = key_line("x.example", revoked, marker="@revoked", comment="bad") + "\n" + key_line("a.example", good) + "\n"
    _, data = _read(tmp_path, text)
    assert data.is_revoked(revoked)
    assert not data.is_revoked(good)
    assert len(data.keys_for("a.example")) == 1


def test_missing_file_is_silent(tmp_path: Path) -> None:
    data = read_known_hosts([tmp_path / "nope"])
    assert data.entries == []
    assert data.issues == []


def test_directory_in_place_of_file_is_one_unreadable_issue(tmp_path: Path) -> None:
    (tmp_path / "kh").mkdir()
    data = read_known_hosts([tmp_path / "kh"])
    assert [i.kind for i in data.issues] == ["unreadable"]


def test_non_utf8_bytes_in_a_comment_do_not_break_parsing(tmp_path: Path) -> None:
    key = make_key()
    _, data = _read(tmp_path, b"# caf\xe9 \xff\n" + (key_line("a.example", key) + "\n").encode())
    assert len(data.keys_for("a.example")) == 1


def test_user_file_wins_over_system_file(tmp_path: Path) -> None:
    user_key, system_key = make_key(), make_key()
    user, system = tmp_path / "u", tmp_path / "s"
    user.write_text(key_line("a.example", user_key) + "\n")
    system.write_text(key_line("a.example", system_key) + "\n")
    host_keys = paramiko.HostKeys()
    read_known_hosts([user, system]).apply_to(host_keys, "a.example")
    found = host_keys.lookup("a.example")
    assert found is not None
    assert found.get(user_key.get_name()) == user_key


def test_apply_to_adds_only_requested_name(tmp_path: Path) -> None:
    key = make_key()
    _, data = _read(tmp_path, key_line("a.example", key) + "\n" + key_line("b.example", make_key()) + "\n")
    host_keys = paramiko.HostKeys()
    data.apply_to(host_keys, "a.example")
    assert host_keys.lookup("b.example") is None
    assert host_keys.lookup("a.example") is not None


def test_host_key_name_brackets_non_default_port() -> None:
    assert host_key_name("h.example", 22) == "h.example"
    assert host_key_name("h.example", 2222) == "[h.example]:2222"


def test_fingerprint_and_type_name_formats() -> None:
    key = make_key()
    expected = "SHA256:" + base64.b64encode(hashlib.sha256(key.asbytes()).digest()).decode().rstrip("=")
    assert fingerprint(key) == expected
    assert key_type_name(make_key("rsa")) == "RSA"
    assert key_type_name(key) == "ECDSA-256"


def test_locations_default_honours_home(isolated_home: Path) -> None:
    locations = KnownHostsLocations.default()
    assert locations.user_known_hosts == isolated_home / ".ssh" / "known_hosts"
    assert locations.user_config == isolated_home / ".ssh" / "config"
    assert locations.system_known_hosts == Path("/etc/ssh/ssh_known_hosts")
    assert locations.system_config == Path("/etc/ssh/ssh_config")


def test_locations_under_uses_given_roots(tmp_path: Path) -> None:
    locations = KnownHostsLocations.under(tmp_path / "h", tmp_path / "e")
    assert locations.user_known_hosts == tmp_path / "h" / ".ssh" / "known_hosts"
    assert locations.system_config == tmp_path / "e" / "ssh_config"
