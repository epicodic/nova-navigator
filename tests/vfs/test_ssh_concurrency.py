"""One SSHFilesystem used from several threads at once, against the stub SSH server.

paramiko reads SFTP replies without a lock and drops a reply that belongs to another request, whose
owner then waits forever.  Every job therefore runs in a daemon thread with a timeout.
"""

from __future__ import annotations

import asyncio
import os
import sys
import threading
import time
from collections.abc import Callable, Generator
from pathlib import Path

import paramiko
import pytest
from paramiko.channel import ChannelFile, ChannelStderrFile, ChannelStdinFile

from nova_navigator.filemanager.tasks import copy_file, erase_files
from nova_navigator.scheduler import TaskCancelled, TaskContext, TaskStatus
from nova_navigator.vfs.filesystems.local import LocalFilesystem
from nova_navigator.vfs.filesystems.ssh import SSHFilesystem
from nova_navigator.vfs.vpath import VPath
from tests._utils.stub_ssh_server import NonGnuMode, StubSFTPHandle, StubSSHServer
from tests.filemanager.common import run_task

_LOGIN = ("testuser", "testpass")
_ROUNDS = 300
_CHUNK = 64 * 1024
# paramiko starts collecting the replies of a pipelined write only after 100 outstanding requests of 32 KiB.
_LARGE = 4 * 1024 * 1024


class _AcceptAnyHostKey(paramiko.MissingHostKeyPolicy):
    """Accept the stub server's throw-away host key."""

    def missing_host_key(self, client: paramiko.SSHClient, hostname: str, key: paramiko.PKey) -> None:
        return


@pytest.fixture(scope="module", params=[None, "illegal-option"], ids=["gnu", "non-gnu"])
def server(request: pytest.FixtureRequest, tmp_path_factory: pytest.TempPathFactory) -> Generator[StubSSHServer, None, None]:
    non_gnu: NonGnuMode | None = request.param
    server = StubSSHServer(root_dir=tmp_path_factory.mktemp("ssh_concurrency_root"), non_gnu=non_gnu)
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


def _run_all(*jobs: Callable[[], None], timeout: float = 20.0) -> None:
    errors: list[BaseException | None] = []

    def guard(job: Callable[[], None]) -> None:
        done = False
        try:
            job()
            done = True
        finally:
            if not done:
                errors.append(sys.exception())

    threads = [threading.Thread(target=guard, args=(job,), daemon=True) for job in jobs]
    for thread in threads:
        thread.start()
    for thread in threads:
        thread.join(timeout)
    assert not any(thread.is_alive() for thread in threads), "deadlock on the shared SFTP session"
    assert errors == []


def _stat_loop(fs: SSHFilesystem, path: Path, rounds: int = _ROUNDS) -> Callable[[], None]:
    def job() -> None:
        for _ in range(rounds):
            assert fs.stat_uncached(VPath(path, fs)).size == path.stat().st_size

    return job


def _upload(fs: SSHFilesystem, path: Path, data: bytes) -> None:
    writer = fs.write(VPath(path, fs))
    for start in range(0, len(data), _CHUNK):
        writer.write(data[start : start + _CHUNK])
    writer.close()


def test_concurrent_stat_uncached_does_not_hang(fs: SSHFilesystem, tmp_path: Path) -> None:
    for name in ("a", "b", "c"):
        (tmp_path / name).write_bytes(name.encode() * 7)

    _run_all(*(_stat_loop(fs, tmp_path / name) for name in ("a", "b", "c")))


def test_listing_and_reading_concurrently_do_not_hang(fs: SSHFilesystem, tmp_path: Path) -> None:
    listed = tmp_path / "listed"
    listed.mkdir()
    for index in range(20):
        (listed / f"f{index}").write_bytes(b"x")
        (listed / f"l{index}").symlink_to(f"f{index}")
    data = os.urandom(1024 * 1024)
    (tmp_path / "big").write_bytes(data)

    async def names() -> set[str]:
        return {vp.name async for vp in fs.iterdir(VPath(listed, fs))}

    def list_job() -> None:
        for _ in range(20):
            fs.refresh()
            assert len(asyncio.run(names())) == 40

    def read_job() -> None:
        for _ in range(5):
            reader = fs.read(VPath(tmp_path / "big", fs))
            received = bytearray()
            while chunk := reader.read(_CHUNK):
                received += chunk
            reader.close()
            assert bytes(received) == data

    _run_all(list_job, read_job, _stat_loop(fs, tmp_path / "big"))


def test_erasing_two_links_at_once_does_not_hang(fs: SSHFilesystem, tmp_path: Path) -> None:
    (tmp_path / "target").write_bytes(b"keep")
    rounds = [[tmp_path / f"link{index}-{side}" for side in ("a", "b")] for index in range(30)]
    for links in rounds:
        for link in links:
            link.symlink_to("target")

    def job() -> None:
        for links in rounds:
            asyncio.run(run_task(lambda ctx, links=links: erase_files(ctx, [VPath(link, fs) for link in links])))

    _run_all(job)
    assert sorted(entry.name for entry in tmp_path.iterdir()) == ["target"]


def test_upload_while_stat_does_not_hang(fs: SSHFilesystem, tmp_path: Path) -> None:
    (tmp_path / "small").write_bytes(b"small")
    data = os.urandom(_LARGE)
    done = threading.Event()

    def upload_job() -> None:
        try:
            _upload(fs, tmp_path / "up", data)
        finally:
            done.set()

    def stat_job() -> None:
        while not done.is_set():
            fs.stat_uncached(VPath(tmp_path / "small", fs))

    _run_all(upload_job, stat_job)
    assert (tmp_path / "up").read_bytes() == data


def test_copy_within_one_connection_does_not_hang(fs: SSHFilesystem, tmp_path: Path) -> None:
    data = os.urandom(_LARGE)
    (tmp_path / "src").write_bytes(data)

    async def copy(ctx: TaskContext) -> None:
        await copy_file(ctx, VPath(tmp_path / "src", fs), VPath(tmp_path / "dst", fs))

    def job() -> None:
        asyncio.run(run_task(copy))

    _run_all(job)
    assert (tmp_path / "dst").read_bytes() == data


def test_packets_stay_whole_while_a_download_sends_requests(fs: SSHFilesystem, tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    data = os.urandom(1024 * 1024)
    (tmp_path / "src").write_bytes(data)
    channel = fs._sftp_client.sock
    assert channel is not None
    send = channel.send

    def split_send(packet: bytes) -> int:
        sent = send(packet[:4096])
        time.sleep(0.002)  # lets the other sending thread run between the parts of a packet
        return sent

    monkeypatch.setattr(channel, "send", split_send)

    def job() -> None:
        reader = fs.read(VPath(tmp_path / "src", fs))  # its prefetch thread sends while the upload below does
        _upload(fs, tmp_path / "up", data)
        reader.close()

    _run_all(job)
    assert (tmp_path / "up").read_bytes() == data


def test_two_uploads_at_once_do_not_hang(fs: SSHFilesystem, tmp_path: Path) -> None:
    first, second = os.urandom(_LARGE), os.urandom(_LARGE)

    _run_all(lambda: _upload(fs, tmp_path / "one", first), lambda: _upload(fs, tmp_path / "two", second))

    assert (tmp_path / "one").read_bytes() == first
    assert (tmp_path / "two").read_bytes() == second


def test_write_error_collected_by_another_request_reaches_the_writer(fs: SSHFilesystem, tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    (tmp_path / "small").write_bytes(b"small")
    monkeypatch.setattr(StubSFTPHandle, "write", lambda _self, _offset, _data: paramiko.sftp.SFTP_FAILURE)

    def job() -> None:
        writer = fs.write(VPath(tmp_path / "up", fs))
        writer.write(b"x" * _CHUNK)
        assert fs.stat_uncached(VPath(tmp_path / "small", fs)).size == 5
        with pytest.raises(OSError, match="Failure"):
            writer.close()

    _run_all(job)


def test_cancel_during_a_download_leaves_the_session_usable(fs: SSHFilesystem, tmp_path: Path) -> None:
    data = os.urandom(_LARGE)
    (tmp_path / "src").write_bytes(data)
    cancel = threading.Event()
    status = TaskStatus(cancel_event=cancel, progress_callback=lambda current: cancel.set() if current.progress.step_completed else None)

    async def copy(ctx: TaskContext) -> None:
        await copy_file(ctx, VPath(tmp_path / "src", fs), VPath(tmp_path / "dst", LocalFilesystem.singleton()))

    def job() -> None:
        with pytest.raises(TaskCancelled):
            asyncio.run(run_task(copy, status=status))
        assert not (tmp_path / "dst").exists()
        assert fs.stat_uncached(VPath(tmp_path / "src", fs)).size == len(data)
        reader = fs.read(VPath(tmp_path / "src", fs))
        assert reader.read(_CHUNK) == data[:_CHUNK]
        reader.close()

    _run_all(job)


def test_listing_command_sends_its_eof_before_reading_the_output(fs: SSHFilesystem, monkeypatch: pytest.MonkeyPatch) -> None:
    # An EOF left to the garbage collector can follow the close of the channel; sshd then drops the whole connection.
    execute = fs._ssh_client.exec_command
    eof_sent_at_read: list[bool] = []

    def spy(command: str) -> tuple[ChannelStdinFile, ChannelFile, ChannelStderrFile]:
        stdin, stdout, stderr = execute(command)
        read = stdout.read

        def checked_read(size: int | None = None) -> bytes:
            eof_sent_at_read.append(bool(stdout.channel.eof_sent))
            return read(size)

        monkeypatch.setattr(stdout, "read", checked_read)
        return stdin, stdout, stderr

    monkeypatch.setattr(fs._ssh_client, "exec_command", spy)
    fs._run_command("true")

    assert eof_sent_at_read == [True]
