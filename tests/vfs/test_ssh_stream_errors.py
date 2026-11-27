"""Failures of SFTP streams: pending write errors, dropped connections and truncated downloads."""

from __future__ import annotations

import contextlib
import gc
import os
import sys
import threading
import time
from collections.abc import Callable, Generator
from pathlib import Path
from typing import Protocol, cast

import paramiko
import pytest
from paramiko.sftp import CMD_DATA, CMD_WRITE, SFTP_EOF

from nova_navigator.vfs.filesystem import StreamReaderLike, StreamWriterLike
from nova_navigator.vfs.filesystems.ssh import SSHFilesystem
from nova_navigator.vfs.vpath import VPath
from tests._utils.stub_ssh_server import StubSFTPHandle, StubSSHServer

_CHUNK = 64 * 1024
_LARGE = 4 * 1024 * 1024
_LOGIN = ("testuser", "testpass")
_LOST = "dropped|connection lost|closed"  # how the messages of a lost connection read


class _AcceptAnyHostKey(paramiko.MissingHostKeyPolicy):
    """Accept the stub server's throw-away host key."""

    def missing_host_key(self, client: paramiko.SSHClient, hostname: str, key: paramiko.PKey) -> None:
        return


@pytest.fixture(scope="module")
def server(tmp_path_factory: pytest.TempPathFactory) -> Generator[StubSSHServer, None, None]:
    server = StubSSHServer(root_dir=tmp_path_factory.mktemp("ssh_stream_errors_root"))
    server.start()
    yield server
    server.stop()


@pytest.fixture
def fs(server: StubSSHServer) -> Generator[SSHFilesystem, None, None]:
    client = paramiko.SSHClient()
    client.set_missing_host_key_policy(_AcceptAnyHostKey())
    filesystem = SSHFilesystem(hostname=server.host, port=server.port, username=_LOGIN[0], password=_LOGIN[1], ssh_client=client)
    yield filesystem
    filesystem.close()
    client.close()


def _finishes(job: Callable[[], None], timeout: float = 20.0) -> None:
    """Run *job* in a daemon thread and fail when it hangs or raises."""
    errors: list[BaseException | None] = []

    def guard() -> None:
        done = False
        try:
            job()
            done = True
        finally:
            if not done:
                errors.append(sys.exception())

    thread = threading.Thread(target=guard, daemon=True)
    thread.start()
    thread.join(timeout)
    assert not thread.is_alive(), "hangs"
    assert errors == []


def _drop_replies(fs: SSHFilesystem, monkeypatch: pytest.MonkeyPatch) -> None:
    """Make the channel behave as after a lost connection: nothing more arrives."""
    monkeypatch.setattr(fs._sftp_client.sock, "recv", lambda _size: b"")


def _drop_sends(fs: SSHFilesystem, monkeypatch: pytest.MonkeyPatch) -> None:
    """Make the channel behave as closed for sending: paramiko raises EOFError."""
    monkeypatch.setattr(fs._sftp_client.sock, "send", lambda _data: 0)


# ── closing a writer with several failed writes ───────────────────────────────


@pytest.mark.parametrize("pieces", [2, 5])
def test_close_with_several_write_errors_still_closes_the_handle(fs: SSHFilesystem, tmp_path: Path, monkeypatch: pytest.MonkeyPatch, pieces: int) -> None:
    closed = threading.Event()
    close = StubSFTPHandle.close

    def recording_close(self: StubSFTPHandle) -> None:
        close(self)
        closed.set()

    monkeypatch.setattr(StubSFTPHandle, "close", recording_close)
    monkeypatch.setattr(StubSFTPHandle, "write", lambda _self, _offset, _data: paramiko.sftp.SFTP_FAILURE)
    opened: list[paramiko.SFTPFile] = []
    open_file = fs._sftp_client.open

    def recording_open(name: bytes, mode: str) -> paramiko.SFTPFile:
        opened.append(open_file(name, mode))
        return opened[-1]

    monkeypatch.setattr(fs._sftp_client, "open", recording_open)

    def job() -> None:
        writer = fs.write(VPath(tmp_path / "up", fs))
        writer.write(b"x" * (pieces * 32 * 1024))
        with pytest.raises(OSError, match="Failure"):
            writer.close()

    _finishes(job)
    assert vars(opened[0])["_closed"]
    assert closed.wait(5), "the server still holds the handle"
    assert opened[0] not in vars(fs._sftp_client)["_expecting"].values()


# ── dropped connections ───────────────────────────────────────────────────────


def _open_reader(fs: SSHFilesystem, tmp_path: Path) -> StreamReaderLike:
    (tmp_path / "src").write_bytes(os.urandom(2 * _LARGE))
    reader = fs.read(VPath(tmp_path / "src", fs))
    reader.read(_CHUNK)
    return reader


@pytest.mark.parametrize("drop", [_drop_replies, _drop_sends], ids=["replies", "sends"])
def test_reading_after_a_dropped_connection_raises_oserror(fs: SSHFilesystem, tmp_path: Path, monkeypatch: pytest.MonkeyPatch, drop: Callable[[SSHFilesystem, pytest.MonkeyPatch], None]) -> None:
    def job() -> None:
        reader = _open_reader(fs, tmp_path)
        drop(fs, monkeypatch)
        with pytest.raises(OSError, match=_LOST):
            reader.read(-1)
        with pytest.raises(OSError, match="earlier read failed"):
            reader.read(_CHUNK)

    _finishes(job)


def test_closing_a_reader_after_a_dropped_connection_raises_oserror(fs: SSHFilesystem, tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    def job() -> None:
        reader = _open_reader(fs, tmp_path)
        _drop_replies(fs, monkeypatch)
        with pytest.raises(OSError, match=_LOST):
            reader.close()

    _finishes(job)


@pytest.mark.parametrize("drop", [_drop_replies, _drop_sends], ids=["replies", "sends"])
def test_writing_after_a_dropped_connection_raises_oserror_for_every_writer(
    fs: SSHFilesystem, tmp_path: Path, monkeypatch: pytest.MonkeyPatch, drop: Callable[[SSHFilesystem, pytest.MonkeyPatch], None]
) -> None:
    def job() -> None:
        first = fs.write(VPath(tmp_path / "one", fs))
        second = fs.write(VPath(tmp_path / "two", fs))
        first.write(b"x" * _CHUNK)
        drop(fs, monkeypatch)
        with pytest.raises(OSError, match=_LOST):
            first.write(b"x" * _LARGE)
        # The first writer is abandoned here; the second one must fail, not wait for its replies.
        with pytest.raises(OSError, match=_LOST):
            second.write(b"x" * _LARGE)
        # Every acknowledged byte is written; paramiko ignores a close request it cannot send.
        with contextlib.suppress(OSError):
            second.close()

    _finishes(job)


def test_closing_a_writer_after_a_dropped_connection_raises_oserror(fs: SSHFilesystem, tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    def job() -> None:
        writer = fs.write(VPath(tmp_path / "up", fs))
        writer.write(b"x" * _CHUNK)
        _drop_replies(fs, monkeypatch)
        with pytest.raises(OSError, match=_LOST):
            writer.close()

    _finishes(job)


def test_closing_an_atomic_writer_after_a_dropped_connection_raises_oserror(fs: SSHFilesystem, tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    def job() -> None:
        writer = fs.write_atomic(VPath(tmp_path / "up", fs))
        writer.write(b"x" * _CHUNK)
        _drop_replies(fs, monkeypatch)
        with pytest.raises(OSError, match=_LOST):
            writer.close()

    _finishes(job)


def _write_pieces(writer: StreamWriterLike) -> None:
    for _ in range(64):
        writer.write(b"x" * _CHUNK)


def _abandon_failed_writer(writer: StreamWriterLike) -> None:
    with pytest.raises(OSError, match="Failure"):
        _write_pieces(writer)


def test_an_abandoned_failed_writer_does_not_block_another_writer(fs: SSHFilesystem, tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    write = StubSFTPHandle.write

    def failing_write(self: StubSFTPHandle, offset: int, data: bytes) -> int:
        return paramiko.sftp.SFTP_FAILURE if data.startswith(b"x") else write(self, offset, data)

    monkeypatch.setattr(StubSFTPHandle, "write", failing_write)
    data = b"y" * _LARGE

    def job() -> None:
        _abandon_failed_writer(fs.write(VPath(tmp_path / "bad", fs)))
        writer = fs.write(VPath(tmp_path / "good", fs))
        writer.write(data)
        writer.close()

    _finishes(job)
    assert (tmp_path / "good").read_bytes() == data


def test_the_finalizer_of_an_abandoned_writer_does_not_read_the_channel(fs: SSHFilesystem, tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    read_response = vars(type(fs._sftp_client))["_read_response"]
    reads: list[object] = []

    def recording_read(waitfor: object = None) -> object:
        reads.append(waitfor)
        return read_response(fs._sftp_client, waitfor)

    def job() -> None:
        writer = fs.write(VPath(tmp_path / "up", fs))
        writer.write(b"x" * _CHUNK)
        monkeypatch.setattr(fs._sftp_client, "_read_response", recording_read)
        del writer  # abandoned with unanswered writes: the finalizer runs without the session lock
        gc.collect()
        assert reads == []
        assert fs.stat_uncached(VPath(tmp_path / "up", fs)) is not None

    _finishes(job)


def test_a_write_that_fails_while_sending_leaves_no_request_to_wait_for(fs: SSHFilesystem, tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    send = vars(fs._sftp_client)["_send_packet"]
    sent: list[int] = []

    def failing_send(t: int, packet: object) -> None:
        if t == CMD_WRITE:
            sent.append(t)
            if len(sent) == 2:
                raise paramiko.SSHException("Key-exchange timed out")
        send(t, packet)

    def job() -> None:
        writer = fs.write(VPath(tmp_path / "up", fs))
        monkeypatch.setattr(fs._sftp_client, "_send_packet", failing_send)
        with pytest.raises(OSError, match="Key-exchange"):
            writer.write(b"x" * _CHUNK)
        # collects the reply of the first request before the writer is used again
        assert fs.stat_uncached(VPath(tmp_path / "up", fs)) is not None
        writer.write(b"x" * _CHUNK)
        writer.close()

    _finishes(job)


# ── truncated downloads ───────────────────────────────────────────────────────


def _read_to_the_end(reader: StreamReaderLike) -> None:
    while reader.read(_CHUNK):
        pass


class _Responder(Protocol):
    def _response(self, request_number: int, t: int, *args: object) -> None: ...


def test_empty_data_before_the_end_of_the_file_raises(fs: SSHFilesystem, tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    (tmp_path / "src").write_bytes(os.urandom(_LARGE))
    read = StubSFTPHandle.read
    send_status = vars(paramiko.SFTPServer)["_send_status"]

    def empty_in_the_middle(self: StubSFTPHandle, offset: int, length: int) -> bytes | int:
        return b"" if offset >= _LARGE // 2 else read(self, offset, length)

    def empty_data_instead_of_eof(self: paramiko.SFTPServer, request_number: int, code: int, desc: str | None = None) -> None:
        if code == SFTP_EOF:
            cast("_Responder", self)._response(request_number, CMD_DATA, "")
        else:
            send_status(self, request_number, code, desc)

    monkeypatch.setattr(StubSFTPHandle, "read", empty_in_the_middle)
    monkeypatch.setattr(paramiko.SFTPServer, "_send_status", empty_data_instead_of_eof)

    def job() -> None:
        reader = fs.read(VPath(tmp_path / "src", fs))
        with pytest.raises(OSError, match="no data"):
            _read_to_the_end(reader)
        reader.close()

    _finishes(job)


@pytest.mark.parametrize("drop", [_drop_replies, _drop_sends], ids=["replies", "sends"])
def test_a_dropped_connection_past_the_recorded_size_is_not_the_end_of_the_file(
    fs: SSHFilesystem, tmp_path: Path, monkeypatch: pytest.MonkeyPatch, drop: Callable[[SSHFilesystem, pytest.MonkeyPatch], None]
) -> None:
    data = os.urandom(_CHUNK)
    (tmp_path / "src").write_bytes(data)

    def job() -> None:
        reader = fs.read(VPath(tmp_path / "src", fs))
        assert reader.read(_CHUNK) == data
        drop(fs, monkeypatch)
        with pytest.raises(OSError, match=_LOST):
            reader.read(_CHUNK)

    _finishes(job)


def test_every_read_after_a_failed_one_raises(fs: SSHFilesystem, tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    (tmp_path / "src").write_bytes(os.urandom(_LARGE))
    read = StubSFTPHandle.read
    failures = [True]

    def failing_read(self: StubSFTPHandle, offset: int, length: int) -> bytes | int:
        if offset >= _LARGE // 2 and failures:
            failures.clear()
            return paramiko.sftp.SFTP_FAILURE
        return read(self, offset, length)

    monkeypatch.setattr(StubSFTPHandle, "read", failing_read)

    def job() -> None:
        reader = fs.read(VPath(tmp_path / "src", fs))
        with pytest.raises(OSError, match="Failure"):
            reader.read(-1)  # the bytes taken before the failure are lost with it
        with pytest.raises(OSError, match="earlier read failed"):
            reader.read(_CHUNK)
        reader.close()

    _finishes(job)


def test_a_connection_dropped_by_the_server_fails_reader_and_writers(tmp_path: Path) -> None:
    server = StubSSHServer(root_dir=tmp_path)
    server.start()
    client = paramiko.SSHClient()
    client.set_missing_host_key_policy(_AcceptAnyHostKey())
    fs = SSHFilesystem(hostname=server.host, port=server.port, username=_LOGIN[0], password=_LOGIN[1], ssh_client=client)

    def job() -> None:
        reader = _open_reader(fs, tmp_path)
        writer = fs.write(VPath(tmp_path / "up", fs))
        writer.write(b"x" * _CHUNK)
        server.stop()
        transport = client.get_transport()
        assert transport is not None
        while transport.is_active():
            time.sleep(0.01)
        with pytest.raises(OSError, match=_LOST):
            reader.read(-1)
        with pytest.raises(OSError, match=_LOST):
            writer.write(b"x" * _LARGE)
        for stream in (writer, reader):
            with contextlib.suppress(OSError):
                stream.close()

    try:
        _finishes(job)
    finally:
        fs.close()
        client.close()
        server.stop()


# ── finalizers of abandoned streams ───────────────────────────────────────────


@pytest.mark.parametrize("mode", ["read", "write"])
def test_the_finalizer_of_an_abandoned_stream_takes_no_lock_and_sends_nothing(fs: SSHFilesystem, tmp_path: Path, monkeypatch: pytest.MonkeyPatch, mode: str) -> None:
    # Garbage collection may run it inside a send, or while the thread holds the client's own lock.
    (tmp_path / "file").write_bytes(b"data")
    path = VPath(tmp_path / "file", fs)
    send = vars(fs._sftp_client)["_send_packet"]
    sent: list[int] = []

    def recording_send(t: int, packet: object) -> None:
        sent.append(t)
        send(t, packet)

    def job() -> None:
        stream = fs.read(path) if mode == "read" else fs.write(path)
        monkeypatch.setattr(fs._sftp_client, "_send_packet", recording_send)
        with vars(fs._sftp_client)["_lock"]:
            del stream
            gc.collect()
        assert sent == []

    _finishes(job, timeout=5.0)


def test_the_handle_of_an_abandoned_stream_is_closed_with_the_next_open(fs: SSHFilesystem, tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    closed: list[str] = []
    close = StubSFTPHandle.close

    def recording_close(self: StubSFTPHandle) -> None:
        close(self)
        closed.append("closed")

    monkeypatch.setattr(StubSFTPHandle, "close", recording_close)
    (tmp_path / "file").write_bytes(b"data")

    def job() -> None:
        abandoned = fs.read(VPath(tmp_path / "file", fs))
        del abandoned
        gc.collect()
        reader = fs.read(VPath(tmp_path / "file", fs))
        assert reader.read(_CHUNK) == b"data"  # the close was sent before this read, so it is answered
        assert closed == ["closed"]
        reader.close()

    _finishes(job)
