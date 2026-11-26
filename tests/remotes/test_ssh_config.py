"""``SshConfig``: precedence, merging, tolerance and ``Match exec`` safety."""

from __future__ import annotations

import logging
from pathlib import Path

import pytest

from nova_navigator.remotes.known_hosts import KnownHostsLocations
from nova_navigator.remotes.ssh_config import SshConfig


def _cfg(tmp_path: Path, name: str, text: str) -> Path:
    path = tmp_path / name
    path.write_text(text)
    return path


def test_user_config_value_beats_system_config(tmp_path: Path) -> None:
    user = _cfg(tmp_path, "u", "Host box\n  User alice\n  Port 2200\n")
    system = _cfg(tmp_path, "s", "Host box\n  User root\n  Port 2300\n  ProxyJump gw\n")
    options = SshConfig.load([user, system]).lookup("box")
    assert (options.user, options.port, options.proxy_jump) == ("alice", 2200, "gw")


def test_first_value_within_one_file_wins(tmp_path: Path) -> None:
    path = _cfg(tmp_path, "c", "Host box\n  User specific\nHost *\n  User general\n  Port 2222\n")
    options = SshConfig.load([path]).lookup("box")
    assert (options.user, options.port) == ("specific", 2222)


def test_hostname_defaults_to_requested_name_and_can_be_overridden(tmp_path: Path) -> None:
    config = SshConfig.load([_cfg(tmp_path, "c", "Host alias\n  HostName 10.0.0.5\n")])
    assert config.lookup("alias").hostname == "10.0.0.5"
    assert config.lookup("other").hostname == "other"


def test_no_entry_gives_empty_options(tmp_path: Path) -> None:
    options = SshConfig.load([tmp_path / "missing"]).lookup("h.example")
    assert (options.hostname, options.port, options.user, options.identity_files, options.proxy_jump) == ("h.example", None, None, (), None)
    assert options.proxy_command_set is False
    assert options.hash_known_hosts is False


def test_identity_files_accumulate_across_files_without_duplicates(tmp_path: Path) -> None:
    user = _cfg(tmp_path, "u", f"Host box\n  IdentityFile {tmp_path}/k1\n  IdentityFile {tmp_path}/k2\n")
    system = _cfg(tmp_path, "s", f"Host box\n  IdentityFile {tmp_path}/k2\n  IdentityFile {tmp_path}/k3\n")
    assert SshConfig.load([user, system]).lookup("box").identity_files == (f"{tmp_path}/k1", f"{tmp_path}/k2", f"{tmp_path}/k3")


@pytest.mark.parametrize("value", ["abc", "0", "70000", "-1", ""])
def test_unusable_port_is_treated_as_unset(tmp_path: Path, value: str) -> None:
    config = SshConfig.load([_cfg(tmp_path, "c", f"Host box\n  Port {value}\n")])
    assert config.lookup("box").port is None


def test_missing_file_is_skipped_silently(tmp_path: Path, caplog: pytest.LogCaptureFixture) -> None:
    caplog.set_level(logging.INFO)
    good = _cfg(tmp_path, "g", "Host box\n  User bob\n")
    assert SshConfig.load([tmp_path / "missing", good]).lookup("box").user == "bob"
    assert caplog.records == []


def test_unreadable_file_is_skipped_and_other_file_still_used(tmp_path: Path, caplog: pytest.LogCaptureFixture) -> None:
    caplog.set_level(logging.INFO)
    directory = tmp_path / "isdir"
    directory.mkdir()
    good = _cfg(tmp_path, "g", "Host box\n  User bob\n")
    assert SshConfig.load([directory, good]).lookup("box").user == "bob"
    assert any("Ignoring ssh_config" in r.getMessage() and "IsADirectoryError" in r.getMessage() for r in caplog.records)


def test_unparseable_file_is_logged_by_type_never_by_content(tmp_path: Path, caplog: pytest.LogCaptureFixture) -> None:
    caplog.set_level(logging.INFO)
    canary = "Hostname-canary-word"
    bad = _cfg(tmp_path, "b", f"Host\n{canary} = [\x00\n")
    good = _cfg(tmp_path, "g", "Host box\n  User bob\n")
    assert SshConfig.load([bad, good]).lookup("box").user == "bob"
    assert canary not in caplog.text


def test_proxy_jump_and_proxy_command_are_reported(tmp_path: Path) -> None:
    config = SshConfig.load([_cfg(tmp_path, "c", "Host a\n  ProxyJump bastion\nHost b\n  ProxyCommand nc %h %p\nHost c\n  ProxyCommand none\n")])
    assert (config.lookup("a").proxy_jump, config.lookup("a").proxy_command_set) == ("bastion", False)
    assert config.lookup("b").proxy_command_set is True
    assert config.lookup("c").proxy_command_set is False


def test_proxy_jump_tokens_are_expanded_by_paramiko(tmp_path: Path) -> None:
    config = SshConfig.load([_cfg(tmp_path, "c", "Host box\n  ProxyJump gw-%h\n")])
    assert config.lookup("box").proxy_jump == "gw-box"


@pytest.mark.parametrize(
    "line",
    [
        'Match exec "touch {marker}"',
        "Match exec touch\\ {marker}",
        'Match e"xec" "touch {marker}"',
        "Match exe''c " + '"touch {marker}"',
        'Match host box exec "touch {marker}"',
        'match  EXEC "touch {marker}"',
    ],
)
def test_match_exec_is_never_executed(tmp_path: Path, line: str) -> None:
    marker = tmp_path / "marker"
    path = _cfg(tmp_path, "c", line.format(marker=marker) + "\n  User pwned\nHost *\n  Port 2201\n")
    options = SshConfig.load([path]).lookup("box")
    assert not marker.exists()
    assert options.user is None
    assert options.port == 2201


def test_match_with_allowed_keywords_still_applies(tmp_path: Path) -> None:
    path = _cfg(tmp_path, "c", "Match host box\n  User matched\n")
    assert SshConfig.load([path]).lookup("box").user == "matched"
    assert SshConfig.load([path]).lookup("other").user is None


@pytest.mark.parametrize(("text", "expected"), [("Host *\n  HashKnownHosts yes\n", True), ("Host box\n  HashKnownHosts no\nHost *\n  HashKnownHosts yes\n", False), ("User x\n", False)])
def test_hash_known_hosts_comes_from_the_shared_config(tmp_path: Path, text: str, expected: bool) -> None:
    assert SshConfig.load([_cfg(tmp_path, "c", text)]).lookup("box").hash_known_hosts is expected


def test_locations_expose_config_paths_user_first() -> None:
    locations = KnownHostsLocations.under(Path("/h"), Path("/e"))
    assert locations.config_paths == (Path("/h/.ssh/config"), Path("/e/ssh_config"))
