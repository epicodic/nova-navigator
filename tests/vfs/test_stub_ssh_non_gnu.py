"""Self-tests of the stub server's non-GNU mode and raw-byte directory entry names (used by the SFTP listing tests)."""

from __future__ import annotations

import os
import shlex
from collections.abc import Generator
from pathlib import Path

import paramiko
import pytest

from tests._utils.stub_ssh_server import StubSSHServer

_LOGIN_WORD = "testpass"
_LIST_SCRIPT = "cd -- {directory} && find . -maxdepth 1 -mindepth 1 -exec stat --printf x -- {{}} +"


class _AcceptAnyHostKey(paramiko.MissingHostKeyPolicy):
    def missing_host_key(self, client: paramiko.SSHClient, hostname: str, key: paramiko.PKey) -> None:
        return


def _connect(server: StubSSHServer) -> paramiko.SSHClient:
    client = paramiko.SSHClient()
    client.set_missing_host_key_policy(_AcceptAnyHostKey())
    client.connect(server.host, port=server.port, username="testuser", password=_LOGIN_WORD, look_for_keys=False, allow_agent=False)
    return client


def _run(server: StubSSHServer, command: str) -> tuple[bytes, str, int]:
    client = _connect(server)
    try:
        _, stdout, stderr = client.exec_command(command)
        output = stdout.read()
        error = stderr.read().decode()
        return output, error, stdout.channel.recv_exit_status()
    finally:
        client.close()


def _list_command(directory: Path) -> str:
    return "sh -c " + shlex.quote(_LIST_SCRIPT.format(directory=shlex.quote(str(directory))))


def _write_bytes_named(raw_name: bytes, content: bytes) -> None:
    with open(raw_name, "wb") as handle:
        handle.write(content)


@pytest.fixture(scope="module")
def gnu_server(tmp_path_factory: pytest.TempPathFactory) -> Generator[StubSSHServer, None, None]:
    server = StubSSHServer(root_dir=tmp_path_factory.mktemp("stub_gnu_root"))
    server.start()
    yield server
    server.stop()


@pytest.fixture(scope="module", params=["illegal-option", "missing"])
def non_gnu_server(request: pytest.FixtureRequest, tmp_path_factory: pytest.TempPathFactory) -> Generator[StubSSHServer, None, None]:
    server = StubSSHServer(root_dir=tmp_path_factory.mktemp("stub_non_gnu_root"), non_gnu=request.param)
    server.start()
    yield server
    server.stop()


def test_plain_stub_runs_the_real_stat(gnu_server: StubSSHServer) -> None:
    assert _run(gnu_server, "stat --printf x -- /") == (b"x", "", 0)


def test_non_gnu_stub_fails_stat_with_empty_output(non_gnu_server: StubSSHServer) -> None:
    output, error, status = _run(non_gnu_server, "stat --printf x -- /")
    assert output == b""
    assert error.strip()
    assert status in {1, 127}


def test_non_gnu_stub_keeps_other_tools(non_gnu_server: StubSSHServer) -> None:
    assert _run(non_gnu_server, "printf ok") == (b"ok", "", 0)


def test_non_gnu_stub_lists_an_empty_directory_with_exit_zero(non_gnu_server: StubSSHServer, tmp_path: Path) -> None:
    output, _error, status = _run(non_gnu_server, _list_command(tmp_path))
    assert (output, status) == (b"", 0)


def test_non_gnu_stub_fails_the_listing_of_a_non_empty_directory(non_gnu_server: StubSSHServer, tmp_path: Path) -> None:
    (tmp_path / "f").write_text("x")
    output, _error, status = _run(non_gnu_server, _list_command(tmp_path))
    assert output == b""
    assert status != 0


def test_plain_stub_lists_a_non_empty_directory(gnu_server: StubSSHServer, tmp_path: Path) -> None:
    (tmp_path / "f").write_text("x")
    output, _error, status = _run(gnu_server, _list_command(tmp_path))
    assert (output, status) == (b"x", 0)


def test_stop_removes_the_shim_directory(tmp_path: Path) -> None:
    server = StubSSHServer(root_dir=tmp_path, non_gnu="illegal-option")
    server.start()
    shim_dir = server._shim_dir
    assert shim_dir is not None
    assert shim_dir.is_dir()
    server.stop()
    assert not shim_dir.exists()


def test_many_utf8_names_arrive_complete(gnu_server: StubSSHServer, tmp_path: Path) -> None:
    names = {f"file-{index:02d}" for index in range(40)}  # more than the 16 entries of one READDIR packet
    for name in names:
        (tmp_path / name).write_text("x")
    client = _connect(gnu_server)
    try:
        assert set(client.open_sftp().listdir(str(tmp_path))) == names
    finally:
        client.close()


def test_non_utf8_entry_name_travels_as_raw_bytes(gnu_server: StubSSHServer, tmp_path: Path) -> None:
    _write_bytes_named(os.fsencode(tmp_path) + b"/bad\xffname", b"x")
    client = _connect(gnu_server)
    try:
        # paramiko's own listing decodes names strictly: the error proves that the undecodable bytes reached the client unchanged.
        with pytest.raises(UnicodeDecodeError):
            client.open_sftp().listdir_attr(str(tmp_path))
    finally:
        client.close()


def test_refusing_stub_rejects_the_exec_request(tmp_path: Path) -> None:
    server = StubSSHServer(root_dir=tmp_path, refuse_exec=True)
    server.start()
    try:
        client = _connect(server)
        try:
            with pytest.raises(paramiko.SSHException):
                client.exec_command("printf ok")
        finally:
            client.close()
    finally:
        server.stop()


def test_refusing_stub_still_serves_sftp(tmp_path: Path) -> None:
    (tmp_path / "f").write_text("x")
    server = StubSSHServer(root_dir=tmp_path, refuse_exec=True)
    server.start()
    try:
        client = _connect(server)
        try:
            assert client.open_sftp().listdir(str(tmp_path)) == ["f"]
        finally:
            client.close()
    finally:
        server.stop()
