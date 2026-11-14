"""Self-tests of the stub server's forwarding, login log and hanging sftp (used by the route and timeout tests)."""

from __future__ import annotations

import socket
import threading
from collections.abc import Generator
from pathlib import Path

import paramiko
import pytest

from tests._utils.silent_server import SilentServer
from tests._utils.ssh_identity import write_identity
from tests._utils.stub_ssh_server import StubSSHServer, make_host_key

_LOGIN_WORD = "testpass"


class _AcceptAnyHostKey(paramiko.MissingHostKeyPolicy):
    def missing_host_key(self, client: paramiko.SSHClient, hostname: str, key: paramiko.PKey) -> None:
        return


def _client(
    server: StubSSHServer,
    username: str = "testuser",
    sock: paramiko.Channel | None = None,
    key_filename: str | None = None,
) -> paramiko.SSHClient:
    client = paramiko.SSHClient()
    client.set_missing_host_key_policy(_AcceptAnyHostKey())
    client.connect(
        server.host,
        port=server.port,
        username=username,
        password=_LOGIN_WORD if key_filename is None else None,
        key_filename=key_filename,
        sock=sock,
        allow_agent=False,
        look_for_keys=False,
    )
    return client


@pytest.fixture
def pair(tmp_path: Path) -> Generator[tuple[StubSSHServer, StubSSHServer], None, None]:
    (tmp_path / "t").mkdir()
    (tmp_path / "b").mkdir()
    target = StubSSHServer(root_dir=tmp_path / "t", host_key=make_host_key("rsa"))
    target.start()
    bastion = StubSSHServer(
        root_dir=tmp_path / "b",
        host_key=make_host_key("ecdsa"),
        forward_map={("target.internal", 22): (target.host, target.port)},
    )
    bastion.start()
    yield bastion, target
    bastion.stop()
    target.stop()


def test_direct_tcpip_reaches_the_mapped_server(pair: tuple[StubSSHServer, StubSSHServer]) -> None:
    bastion, target = pair
    jump = _client(bastion)
    transport = jump.get_transport()
    assert transport is not None
    channel = transport.open_channel("direct-tcpip", ("target.internal", 22), ("127.0.0.1", 0), timeout=5)
    inner = _client(target, username="inner", sock=channel)
    _, stdout, _ = inner.exec_command("echo via-bastion")
    assert stdout.read().strip() == b"via-bastion"
    assert bastion.forwarded == [("target.internal", 22)]
    assert [r.username for r in target.auth_log] == ["inner"]
    inner.close()
    jump.close()


def test_unmapped_destination_is_refused(pair: tuple[StubSSHServer, StubSSHServer]) -> None:
    bastion, _ = pair
    jump = _client(bastion)
    transport = jump.get_transport()
    assert transport is not None
    with pytest.raises(paramiko.ChannelException):
        transport.open_channel("direct-tcpip", ("nowhere.internal", 22), ("127.0.0.1", 0), timeout=5)
    assert bastion.forwarded == []
    jump.close()


def test_forwarding_is_prohibited_without_a_forward_map(tmp_path: Path) -> None:
    server = StubSSHServer(root_dir=tmp_path, host_key=make_host_key("rsa"))
    server.start()
    try:
        client = _client(server)
        transport = client.get_transport()
        assert transport is not None
        with pytest.raises(paramiko.ChannelException):
            transport.open_channel("direct-tcpip", ("127.0.0.1", 1), ("127.0.0.1", 0), timeout=5)
        client.close()
    finally:
        server.stop()


def test_auth_log_records_user_method_and_key(tmp_path: Path) -> None:
    server = StubSSHServer(root_dir=tmp_path, host_key=make_host_key("rsa"))
    server.start()
    try:
        identity, blob = write_identity(tmp_path / "id")
        _client(server, username="keyuser", key_filename=identity).close()
        _client(server, username="passuser").close()
        assert [(r.username, r.method, r.key_base64) for r in server.auth_log] == [
            ("keyuser", "publickey", blob),
            ("passuser", "password", None),
        ]
    finally:
        server.stop()


def test_hang_sftp_authenticates_but_never_answers(tmp_path: Path) -> None:
    server = StubSSHServer(root_dir=tmp_path, host_key=make_host_key("rsa"), hang_sftp=True)
    server.start()
    try:
        client = _client(server)
        worker = threading.Thread(target=client.open_sftp, daemon=True)
        worker.start()
        worker.join(1.0)
        assert worker.is_alive()
        client.close()
        worker.join(5.0)
        assert not worker.is_alive()
    finally:
        server.stop()


def test_silent_server_accepts_tcp_and_never_writes() -> None:
    server = SilentServer()
    server.start()
    try:
        with socket.create_connection(("127.0.0.1", server.port), timeout=2) as sock:
            sock.settimeout(0.5)
            with pytest.raises(TimeoutError):
                sock.recv(1)
    finally:
        server.stop()
