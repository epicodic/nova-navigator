"""The login directory as the home of an SSHFilesystem (REQ-20)."""

from __future__ import annotations

import time
from collections.abc import Generator
from pathlib import Path
from unittest.mock import MagicMock

import paramiko
import pytest

from nova_navigator.remotes.known_hosts import KnownHostsLocations, host_key_name
from nova_navigator.remotes.ssh_route import SshHop
from nova_navigator.vfs.filesystems.ssh import SshConnectTimeoutError, SSHFilesystem, _login_directory
from nova_navigator.vfs.vpath import VPath
from tests._utils.known_hosts_files import key_line
from tests._utils.stub_ssh_server import StubSSHServer, make_host_key

_LOGIN_WORD = "testpass"
_LOGIN_DIR = Path("/srv/users/alice")


class _AcceptAnyHostKey(paramiko.MissingHostKeyPolicy):
    def missing_host_key(self, client: paramiko.SSHClient, hostname: str, key: paramiko.PKey) -> None:
        return


@pytest.fixture(autouse=True)
def isolated_home(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    """Keep the own-connect tests away from the real ``~/.ssh`` (as ``test_ssh_timeout.py`` does)."""
    monkeypatch.setenv("HOME", str(tmp_path / "realhome-guard"))
    monkeypatch.delenv("SSH_AUTH_SOCK", raising=False)


@pytest.fixture(scope="module")
def login_server() -> Generator[StubSSHServer, None, None]:
    server = StubSSHServer(root_dir=_LOGIN_DIR, host_key=make_host_key("rsa"))  # the directory need not exist: only canonicalize('.') reads it
    server.start()
    yield server
    server.stop()


def _injected(server: StubSSHServer) -> tuple[SSHFilesystem, paramiko.SSHClient]:
    client = paramiko.SSHClient()
    client.set_missing_host_key_policy(_AcceptAnyHostKey())
    return SSHFilesystem(hostname=server.host, port=server.port, username="testuser", password=_LOGIN_WORD, ssh_client=client), client


@pytest.fixture
def fs(login_server: StubSSHServer) -> Generator[SSHFilesystem, None, None]:
    filesystem, client = _injected(login_server)
    yield filesystem
    client.close()


def test_home_is_the_sftp_login_directory(fs: SSHFilesystem) -> None:
    home = fs.home()
    assert home.path == _LOGIN_DIR
    assert home.filesystem is fs


def test_path_below_home_is_abbreviated(fs: SSHFilesystem) -> None:
    assert VPath(_LOGIN_DIR / "docs" / "a.txt", fs).compact_path_str == "~/docs/a.txt"


def test_home_itself_is_a_tilde(fs: SSHFilesystem) -> None:
    assert VPath(_LOGIN_DIR, fs).compact_path_str == "~"


def test_path_under_slash_home_that_is_not_the_home_is_shown_in_full(fs: SSHFilesystem) -> None:
    assert VPath("/home/x/file", fs).compact_path_str == "/home/x/file"


def test_sibling_sharing_a_name_prefix_is_not_below_home(fs: SSHFilesystem) -> None:
    assert VPath("/srv/users/alice2/docs", fs).compact_path_str == "/srv/users/alice2/docs"


def test_home_is_looked_up_once_and_home_does_no_io(login_server: StubSSHServer, monkeypatch: pytest.MonkeyPatch) -> None:
    original = paramiko.SFTPClient.normalize
    calls: list[bytes | str] = []

    def counting(self: paramiko.SFTPClient, path: bytes | str) -> str:
        calls.append(path)
        return original(self, path)

    monkeypatch.setattr(paramiko.SFTPClient, "normalize", counting)
    filesystem, client = _injected(login_server)
    try:
        assert calls == ["."]
        filesystem.home()
        filesystem.home()
        assert VPath(_LOGIN_DIR / "x", filesystem).compact_path_str == "~/x"
        assert calls == ["."]
    finally:
        client.close()


def test_fallback_home_is_root_and_everything_is_below_it(login_server: StubSSHServer, monkeypatch: pytest.MonkeyPatch) -> None:
    def refuse(_self: paramiko.SFTPClient, _path: bytes | str) -> str:
        raise OSError("no realpath")

    monkeypatch.setattr(paramiko.SFTPClient, "normalize", refuse)
    filesystem, client = _injected(login_server)
    try:
        assert filesystem.home().path == Path("/")
        assert VPath("/etc/hosts", filesystem).compact_path_str == "~/etc/hosts"  # literal REQ-20 rule: home "/"
    finally:
        client.close()


@pytest.mark.parametrize(
    "failure",
    [OSError("boom"), paramiko.SSHException("closed"), paramiko.SFTPError("bad"), EOFError(), UnicodeDecodeError("utf-8", b"\xff", 0, 1, "invalid start byte")],
)
def test_login_directory_falls_back_to_root_when_normalize_fails(failure: Exception) -> None:
    sftp = MagicMock(spec=paramiko.SFTPClient)
    sftp.normalize.side_effect = failure
    assert _login_directory(sftp) == "/"


@pytest.mark.parametrize("reply", ["", "relative/dir", "~"])
def test_login_directory_rejects_a_non_absolute_reply(reply: str) -> None:
    sftp = MagicMock(spec=paramiko.SFTPClient)
    sftp.normalize.return_value = reply
    assert _login_directory(sftp) == "/"


def test_login_directory_rejects_a_non_text_reply() -> None:
    sftp = MagicMock(spec=paramiko.SFTPClient)  # normalize() returns a MagicMock
    assert _login_directory(sftp) == "/"


def test_login_directory_returns_the_absolute_reply() -> None:
    sftp = MagicMock(spec=paramiko.SFTPClient)
    sftp.normalize.return_value = "/srv/users/alice"
    assert _login_directory(sftp) == "/srv/users/alice"
    sftp.normalize.assert_called_once_with(".")


@pytest.fixture
def locations(tmp_path: Path) -> KnownHostsLocations:
    (tmp_path / "home" / ".ssh").mkdir(parents=True)
    return KnownHostsLocations.under(tmp_path / "home", tmp_path / "etc")


def test_own_connect_single_hop_uses_the_login_directory(login_server: StubSSHServer, locations: KnownHostsLocations) -> None:
    locations.user_known_hosts.write_text(key_line(host_key_name(login_server.host, login_server.port), login_server.host_key) + "\n")
    route = [SshHop("stub", login_server.host, login_server.port, "u", ())]
    filesystem = SSHFilesystem("stub", login_server.port, "u", None, _LOGIN_WORD, route=route, connect_timeout=10.0, known_hosts=locations)
    try:
        assert filesystem.home().path == _LOGIN_DIR
    finally:
        filesystem.close()


def test_login_directory_lookup_is_bounded_by_the_connect_timeout(login_server: StubSSHServer, locations: KnownHostsLocations, monkeypatch: pytest.MonkeyPatch) -> None:
    def slow(_self: paramiko.SFTPClient, _path: bytes | str) -> str:
        time.sleep(1.5)  # the 1 s watchdog fires and closes the client; the call still "succeeds"
        return "/late"

    monkeypatch.setattr(paramiko.SFTPClient, "normalize", slow)
    locations.user_known_hosts.write_text(key_line(host_key_name(login_server.host, login_server.port), login_server.host_key) + "\n")
    route = [SshHop("stub", login_server.host, login_server.port, "u", ())]
    with pytest.raises(SshConnectTimeoutError):
        SSHFilesystem("stub", login_server.port, "u", None, _LOGIN_WORD, route=route, connect_timeout=1.0, known_hosts=locations)
