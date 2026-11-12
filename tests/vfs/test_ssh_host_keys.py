"""Host-key verification and storage of ``SSHFilesystem`` against a stub SSH server."""

from __future__ import annotations

import os
from collections.abc import Generator
from pathlib import Path
from unittest.mock import MagicMock

import paramiko
import pytest
from cryptography.hazmat.primitives import serialization
from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PrivateKey

from nova_navigator.remotes.known_hosts import KnownHostsLocations, fingerprint, host_key_name
from nova_navigator.vfs.filesystems.ssh import (
    ChangedHostKeyError,
    ConfirmedKeyMismatchError,
    RevokedHostKeyError,
    SSHFilesystem,
    UnknownHostKeyError,
)
from tests._utils.known_hosts_files import KEYGEN, UNSUPPORTED_LINE, key_line, keygen_finds, make_key, mixed_known_hosts
from tests._utils.stub_ssh_server import StubSSHServer, make_host_key

_LOGIN_WORD = "testpass"


@pytest.fixture(autouse=True)
def isolated_home(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("HOME", str(tmp_path / "realhome-guard"))
    monkeypatch.delenv("SSH_AUTH_SOCK", raising=False)


@pytest.fixture
def server(tmp_path: Path) -> Generator[StubSSHServer, None, None]:
    (tmp_path / "root").mkdir()
    srv = StubSSHServer(root_dir=tmp_path / "root", host_key=make_host_key("rsa"))
    srv.start()
    yield srv
    srv.stop()


@pytest.fixture
def locations(tmp_path: Path) -> KnownHostsLocations:
    (tmp_path / "home").mkdir()
    return KnownHostsLocations.under(tmp_path / "home", tmp_path / "etc")


@pytest.fixture
def close_count(monkeypatch: pytest.MonkeyPatch) -> list[int]:
    """Count calls of ``SSHClient.close`` (the original still runs)."""
    calls: list[int] = []
    original = paramiko.SSHClient.close

    def counting(self: paramiko.SSHClient) -> None:
        calls.append(1)
        original(self)

    monkeypatch.setattr(paramiko.SSHClient, "close", counting)
    return calls


def _connect(
    server: StubSSHServer,
    locations: KnownHostsLocations,
    trusted_host_key: paramiko.PKey | None = None,
    key_filename: str | None = None,
    password: str | None = _LOGIN_WORD,
) -> SSHFilesystem:
    return SSHFilesystem(server.host, server.port, "testuser", key_filename, password, known_hosts=locations, trusted_host_key=trusted_host_key)


def _name(server: StubSSHServer) -> str:
    return host_key_name(server.host, server.port)


def _write(path: Path, data: bytes | str) -> bytes:
    path.parent.mkdir(parents=True, exist_ok=True)
    raw = data if isinstance(data, bytes) else data.encode()
    path.write_bytes(raw)
    return raw


def _identity(tmp_path: Path) -> str:
    """Write an OpenSSH-format Ed25519 identity (paramiko tries Ed25519 last, so the final error is the authentication failure)."""
    private = Ed25519PrivateKey.generate().private_bytes(serialization.Encoding.PEM, serialization.PrivateFormat.OpenSSH, serialization.NoEncryption())
    path = tmp_path / "id"
    path.write_bytes(private)
    return str(path)


def test_unknown_host_raises_unknown_with_presented_key(server: StubSSHServer, locations: KnownHostsLocations) -> None:
    with pytest.raises(UnknownHostKeyError) as info:
        _connect(server, locations)
    assert info.value.hostname == _name(server)
    assert info.value.key == server.host_key
    assert info.value.fingerprint == fingerprint(server.host_key)
    assert not locations.user_known_hosts.exists()


@pytest.mark.parametrize("hashed", [False, True])
def test_file_with_ignored_lines_and_valid_entry_connects_without_prompt(server: StubSSHServer, locations: KnownHostsLocations, hashed: bool) -> None:
    name = _name(server)
    valid = key_line(paramiko.HostKeys.hash_host(name) if hashed else name, server.host_key)
    before = _write(locations.user_known_hosts, mixed_known_hosts(name, server.host_key, make_key("ecdsa")).replace(key_line(name, server.host_key).encode(), valid.encode()))
    fs = _connect(server, locations)
    try:
        assert locations.user_known_hosts.read_bytes() == before
    finally:
        fs._ssh_client.close()


def test_host_covered_only_by_ignored_line_is_unknown(server: StubSSHServer, locations: KnownHostsLocations) -> None:
    name = _name(server)
    other = make_key("ecdsa")
    text = "\n".join([key_line(name, other, marker="@cert-authority"), UNSUPPORTED_LINE.replace("sk-host.example", name), UNSUPPORTED_LINE.replace("sk-host.example", "*")]) + "\n"
    _write(locations.user_known_hosts, text)
    with pytest.raises(UnknownHostKeyError):
        _connect(server, locations)


def test_confirmed_key_connects_and_is_stored_once(server: StubSSHServer, locations: KnownHostsLocations) -> None:
    name = _name(server)
    before = _write(locations.user_known_hosts, mixed_known_hosts("other.example", make_key("ecdsa"), make_key("ecdsa")))
    fs = _connect(server, locations, trusted_host_key=server.host_key)
    fs._ssh_client.close()
    assert fs.host_key_save_error is None
    after = locations.user_known_hosts.read_bytes()
    assert after.startswith(before)
    assert after[len(before) :] == f"{key_line(name, server.host_key)}\n".encode()
    second = _connect(server, locations)
    second._ssh_client.close()
    third = _connect(server, locations, trusted_host_key=server.host_key)
    third._ssh_client.close()
    assert locations.user_known_hosts.read_bytes() == after


@pytest.mark.parametrize("kind", ["ecdsa", "rsa"])
def test_confirmed_key_mismatch_stores_nothing(server: StubSSHServer, locations: KnownHostsLocations, kind: str) -> None:
    confirmed = make_key(kind)
    with pytest.raises(ConfirmedKeyMismatchError) as info:
        _connect(server, locations, trusted_host_key=confirmed)
    assert not isinstance(info.value, ChangedHostKeyError)
    assert info.value.key == server.host_key
    assert info.value.confirmed_key == confirmed
    assert not locations.user_known_hosts.exists()


def test_server_rotating_key_between_attempts_fails_the_confirmation(server: StubSSHServer, locations: KnownHostsLocations) -> None:
    with pytest.raises(UnknownHostKeyError) as first:
        _connect(server, locations)
    server.set_host_key(make_host_key("ecdsa"))
    with pytest.raises(ConfirmedKeyMismatchError):
        _connect(server, locations, trusted_host_key=first.value.key)
    assert not locations.user_known_hosts.exists()


@pytest.mark.parametrize("with_plain_entry", [False, True])
def test_revoked_key_is_refused(server: StubSSHServer, locations: KnownHostsLocations, with_plain_entry: bool) -> None:
    lines = [key_line("anyhost.example", server.host_key, marker="@revoked")]
    if with_plain_entry:
        lines.append(key_line(_name(server), make_host_key("rsa")))
    before = _write(locations.user_known_hosts, "\n".join(lines) + "\n")
    with pytest.raises(RevokedHostKeyError):
        _connect(server, locations)
    with pytest.raises(RevokedHostKeyError):
        _connect(server, locations, trusted_host_key=server.host_key)
    assert locations.user_known_hosts.read_bytes() == before


@pytest.mark.parametrize("stored_kind", ["rsa", "ecdsa"])
def test_changed_key_raises_changed_with_both_keys(server: StubSSHServer, locations: KnownHostsLocations, stored_kind: str) -> None:
    stored = make_host_key(stored_kind)
    before = _write(locations.user_known_hosts, key_line(_name(server), stored) + "\n")
    with pytest.raises(ChangedHostKeyError) as info:
        _connect(server, locations)
    assert info.value.hostname == _name(server)
    assert info.value.key == server.host_key
    assert info.value.expected_key == stored
    assert locations.user_known_hosts.read_bytes() == before


def test_failed_connect_closes_the_transport(server: StubSSHServer, locations: KnownHostsLocations, close_count: list[int]) -> None:
    with pytest.raises(UnknownHostKeyError):
        _connect(server, locations)
    assert close_count


def test_authentication_failure_after_verification_still_stores_the_confirmed_key(tmp_path: Path, locations: KnownHostsLocations) -> None:
    (tmp_path / "root").mkdir()
    srv = StubSSHServer(root_dir=tmp_path / "root", host_key=make_host_key("rsa"), reject_publickey=True)
    srv.start()
    try:
        with pytest.raises(paramiko.AuthenticationException):
            _connect(srv, locations, trusted_host_key=srv.host_key, key_filename=_identity(tmp_path), password=None)
        assert locations.user_known_hosts.read_text() == f"{key_line(_name(srv), srv.host_key)}\n"
        other = tmp_path / "other"
        (other / "home").mkdir(parents=True)
        other_locations = KnownHostsLocations.under(other / "home", other / "etc")
        with pytest.raises(ConfirmedKeyMismatchError):
            _connect(srv, other_locations, trusted_host_key=make_host_key("rsa"), key_filename=_identity(tmp_path), password=None)
        assert not other_locations.user_known_hosts.exists()
    finally:
        srv.stop()


def test_other_failure_after_connect_stores_nothing(server: StubSSHServer, locations: KnownHostsLocations) -> None:
    with pytest.raises(paramiko.SSHException) as info:
        _connect(server, locations, trusted_host_key=server.host_key, password=None)
    assert not isinstance(info.value, paramiko.AuthenticationException)
    assert not locations.user_known_hosts.exists()


def test_append_failure_connects_and_records_error(server: StubSSHServer, locations: KnownHostsLocations) -> None:
    ssh_dir = locations.user_known_hosts.parent
    ssh_dir.mkdir(parents=True)
    ssh_dir.chmod(0o500)
    try:
        if os.access(ssh_dir, os.W_OK):
            pytest.skip("directory permissions are not enforced (running as root)")
        fs = _connect(server, locations, trusted_host_key=server.host_key)
        fs._ssh_client.close()
        assert isinstance(fs.host_key_save_error, OSError)
        assert fs.host_key_save_path == locations.user_known_hosts
    finally:
        ssh_dir.chmod(0o700)


@pytest.mark.skipif(KEYGEN is None, reason="ssh-keygen not available")
def test_non_default_port_is_stored_as_bracketed_name(server: StubSSHServer, locations: KnownHostsLocations) -> None:
    fs = _connect(server, locations, trusted_host_key=server.host_key)
    fs._ssh_client.close()
    assert locations.user_known_hosts.read_text().startswith(f"[{server.host}]:{server.port} ")
    assert keygen_finds(locations.user_known_hosts, _name(server))


@pytest.mark.skipif(KEYGEN is None, reason="ssh-keygen not available")
def test_hash_known_hosts_yes_in_config_hashes_the_line(server: StubSSHServer, locations: KnownHostsLocations) -> None:
    _write(locations.user_config, "Host *\n  HashKnownHosts yes\n")
    fs = _connect(server, locations, trusted_host_key=server.host_key)
    fs._ssh_client.close()
    text = locations.user_known_hosts.read_text()
    assert text.startswith("|1|")
    assert server.host not in text
    assert keygen_finds(locations.user_known_hosts, _name(server))


class _AcceptAnyPolicy(paramiko.MissingHostKeyPolicy):
    def missing_host_key(self, client: paramiko.SSHClient, hostname: str, key: paramiko.PKey) -> None:
        return None


def test_injected_client_is_untouched(server: StubSSHServer, locations: KnownHostsLocations) -> None:
    client = paramiko.SSHClient()
    client.set_missing_host_key_policy(_AcceptAnyPolicy())
    fs = SSHFilesystem(server.host, server.port, "testuser", None, _LOGIN_WORD, ssh_client=client, known_hosts=locations)
    fs._ssh_client.close()
    assert not locations.user_known_hosts.exists()


def test_injected_client_bad_host_key_is_translated() -> None:
    presented, expected = make_key("ecdsa"), make_key("ecdsa")
    client = MagicMock(spec=paramiko.SSHClient)
    client.connect.side_effect = paramiko.BadHostKeyException("h", presented, expected)
    with pytest.raises(ChangedHostKeyError) as info:
        SSHFilesystem("h", ssh_client=client)
    assert info.value.key == presented
    assert info.value.expected_key == expected


# -- DEC-19: another window stored a different key of the same type --------------------------------


def test_conflicting_stored_key_at_append_time_raises_changed_and_closes(server: StubSSHServer, locations: KnownHostsLocations, close_count: list[int]) -> None:
    stored = make_host_key("rsa")
    before = _write(locations.user_known_hosts, key_line(_name(server), stored) + "\n")
    with pytest.raises(ChangedHostKeyError) as info:
        _connect(server, locations, trusted_host_key=server.host_key)
    assert info.value.hostname == _name(server)
    assert info.value.key == server.host_key
    assert info.value.expected_key == stored
    assert not isinstance(info.value, ConfirmedKeyMismatchError)
    assert locations.user_known_hosts.read_bytes() == before
    assert close_count


def test_conflicting_stored_key_after_authentication_failure_raises_changed_and_closes(tmp_path: Path, locations: KnownHostsLocations, close_count: list[int]) -> None:
    (tmp_path / "root").mkdir()
    srv = StubSSHServer(root_dir=tmp_path / "root", host_key=make_host_key("rsa"), reject_publickey=True)
    srv.start()
    try:
        stored = make_host_key("rsa")
        before = _write(locations.user_known_hosts, key_line(_name(srv), stored) + "\n")
        with pytest.raises(ChangedHostKeyError) as info:
            _connect(srv, locations, trusted_host_key=srv.host_key, key_filename=_identity(tmp_path), password=None)
        assert info.value.key == srv.host_key
        assert info.value.expected_key == stored
        assert locations.user_known_hosts.read_bytes() == before
        assert close_count
    finally:
        srv.stop()
