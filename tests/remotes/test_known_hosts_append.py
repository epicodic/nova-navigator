from __future__ import annotations

import os
import stat
import threading
from pathlib import Path

import paramiko
import pytest

from nova_navigator.remotes.known_hosts import (
    AppendResult,
    HostKeyConflictError,
    append_host_key,
    host_key_name,
    read_known_hosts,
)
from tests._utils.known_hosts_files import KEYGEN, key_line, keygen_finds, make_key, mixed_known_hosts

needs_keygen = pytest.mark.skipif(KEYGEN is None, reason="ssh-keygen is not installed")


def test_mixed_file_prefix_is_byte_identical_and_one_line_added(tmp_path: Path) -> None:
    existing, new = make_key(), make_key("rsa")
    path = tmp_path / "known_hosts"
    before = mixed_known_hosts("kept.example", existing, make_key())
    path.write_bytes(before)
    assert append_host_key(path, "new.example", new, hashed=False) is AppendResult.APPENDED
    after = path.read_bytes()
    assert after.startswith(before)
    assert after[len(before) :] == f"new.example {new.get_name()} {new.get_base64()}\n".encode()


def test_file_without_final_newline_gets_a_separator_and_keeps_old_bytes(tmp_path: Path) -> None:
    old, new = make_key(), make_key()
    path = tmp_path / "known_hosts"
    before = key_line("old.example", old).encode()  # no trailing newline
    path.write_bytes(before)
    append_host_key(path, "new.example", new, hashed=False)
    after = path.read_bytes()
    assert after.startswith(before)
    assert after[len(before) :] == b"\n" + f"new.example {new.get_name()} {new.get_base64()}\n".encode()
    assert len(read_known_hosts([path]).entries) == 2


def test_missing_file_and_directory_are_created_with_modes(tmp_path: Path) -> None:
    key = make_key()
    path = tmp_path / ".ssh" / "known_hosts"
    append_host_key(path, "h.example", key, hashed=False)
    assert path.read_text() == f"h.example {key.get_name()} {key.get_base64()}\n"
    assert stat.S_IMODE(path.stat().st_mode) == 0o600 & ~_umask()
    assert stat.S_IMODE(path.parent.stat().st_mode) == 0o700 & ~_umask()


def _umask() -> int:
    current = os.umask(0)
    os.umask(current)
    return current


def test_existing_directory_and_file_modes_are_untouched(tmp_path: Path) -> None:
    ssh_dir = tmp_path / ".ssh"
    ssh_dir.mkdir(mode=0o755)
    ssh_dir.chmod(0o755)
    path = ssh_dir / "known_hosts"
    path.write_text("")
    path.chmod(0o644)
    append_host_key(path, "h.example", make_key(), hashed=False)
    assert stat.S_IMODE(ssh_dir.stat().st_mode) == 0o755
    assert stat.S_IMODE(path.stat().st_mode) == 0o644


def test_identical_key_is_not_appended_twice(tmp_path: Path) -> None:
    key = make_key()
    path = tmp_path / "known_hosts"
    assert append_host_key(path, "h.example", key, hashed=False) is AppendResult.APPENDED
    size = path.stat().st_size
    assert append_host_key(path, "h.example", key, hashed=False) is AppendResult.ALREADY_PRESENT
    assert path.stat().st_size == size


def test_hashed_line_matches_via_reader_and_hides_name(tmp_path: Path) -> None:
    key = make_key()
    path = tmp_path / "known_hosts"
    append_host_key(path, "secret.example", key, hashed=True)
    text = path.read_text()
    assert text.startswith("|1|")
    assert "secret.example" not in text
    assert len(read_known_hosts([path]).keys_for("secret.example")) == 1


@needs_keygen
@pytest.mark.parametrize(("host", "port", "hashed"), [("h.example", 22, False), ("h.example", 2222, False), ("h.example", 22, True), ("h.example", 2222, True)])
def test_ssh_keygen_finds_appended_line(tmp_path: Path, host: str, port: int, hashed: bool) -> None:
    name = host_key_name(host, port)
    path = tmp_path / "known_hosts"
    append_host_key(path, name, make_key(), hashed=hashed)
    assert keygen_finds(path, name)
    if port != 22:
        assert not keygen_finds(path, host)  # a [host]:port line must not match the bare host


def test_oserror_surfaces_for_unwritable_directory(tmp_path: Path) -> None:
    locked = tmp_path / "locked"
    locked.mkdir()
    locked.chmod(0o500)
    try:
        if os.access(locked, os.W_OK):
            pytest.skip("directory permissions are not enforced (running as root)")
        with pytest.raises(PermissionError):
            append_host_key(locked / "known_hosts", "h.example", make_key(), hashed=False)
    finally:
        locked.chmod(0o700)


def test_parallel_appends_leave_no_torn_or_lost_lines(tmp_path: Path) -> None:
    path = tmp_path / "known_hosts"
    keys = {f"h{i}.example": make_key() for i in range(12)}
    threads = [threading.Thread(target=append_host_key, args=(path, name, key), kwargs={"hashed": False}) for name, key in keys.items()]
    for thread in threads:
        thread.start()
    for thread in threads:
        thread.join()
    data = read_known_hosts([path])
    assert data.issues == []
    assert {n for e in data.entries for n in e.names} == set(keys)
    assert len(path.read_text().splitlines()) == len(keys)


def test_conflicting_key_of_same_type_raises_and_leaves_file_unchanged(tmp_path: Path) -> None:
    stored, other = make_key("ecdsa"), make_key("ecdsa")
    path = tmp_path / "known_hosts"
    path.write_bytes((key_line("h.example", stored) + "\n").encode())
    before = path.read_bytes()
    with pytest.raises(HostKeyConflictError) as info:
        append_host_key(path, "h.example", other, hashed=False)
    assert info.value.name == "h.example"
    assert info.value.stored_key == stored
    assert not isinstance(info.value, OSError)
    assert path.read_bytes() == before


def test_key_of_a_different_type_for_the_same_host_is_appended(tmp_path: Path) -> None:
    stored, other = make_key("ecdsa"), make_key("rsa")
    path = tmp_path / "known_hosts"
    path.write_bytes((key_line("h.example", stored) + "\n").encode())
    assert append_host_key(path, "h.example", other, hashed=False) is AppendResult.APPENDED
    assert len(read_known_hosts([path]).keys_for("h.example")) == 2


def test_identical_key_is_still_already_present_with_other_keys_stored(tmp_path: Path) -> None:
    first, second = make_key("ecdsa"), make_key("rsa")
    path = tmp_path / "known_hosts"
    path.write_bytes((key_line("h.example", first) + "\n" + key_line("h.example", second) + "\n").encode())
    assert append_host_key(path, "h.example", first, hashed=False) is AppendResult.ALREADY_PRESENT


def test_conflict_is_detected_through_a_hashed_entry(tmp_path: Path) -> None:
    stored, other = make_key("ecdsa"), make_key("ecdsa")
    path = tmp_path / "known_hosts"
    path.write_bytes((key_line(paramiko.HostKeys.hash_host("h.example"), stored) + "\n").encode())
    before = path.read_bytes()
    with pytest.raises(HostKeyConflictError) as info:
        append_host_key(path, "h.example", other, hashed=True)
    assert info.value.stored_key == stored
    assert path.read_bytes() == before
