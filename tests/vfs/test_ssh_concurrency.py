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
from paramiko.sftp import CMD_READ

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
    deadline = time.monotonic() + timeout  # one deadline for all, below the per-test limit
    for thread in threads:
        thread.join(max(0.0, deadline - time.monotonic()))
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
        reader = fs.read(VPath(tmp_path / "src", fs))
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


def _download(fs: SSHFilesystem, path: Path, chunk_size: int = _CHUNK) -> bytes:
    reader = fs.read(VPath(path, fs))
    received = bytearray()
    while chunk := reader.read(chunk_size):
        received += chunk
    reader.close()
    return bytes(received)


def test_paused_download_requests_only_a_bounded_amount_ahead(fs: SSHFilesystem, tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    # Requesting a whole large file at once fills the server's input window; the next request then never gets out.
    data = os.urandom(16 * 1024 * 1024)
    (tmp_path / "src").write_bytes(data)
    requested: list[int] = []
    send = vars(fs._sftp_client)["_send_packet"]

    def counting_send(t: int, packet: paramiko.Message) -> None:
        if t == CMD_READ:
            requested.append(t)
        send(t, packet)

    monkeypatch.setattr(fs._sftp_client, "_send_packet", counting_send)

    def job() -> None:
        reader = fs.read(VPath(tmp_path / "src", fs))
        first = reader.read(_CHUNK)
        time.sleep(0.5)  # paused: nothing may go on requesting
        assert fs.stat_uncached(VPath(tmp_path / "src", fs)).size == len(data)
        assert len(requested) * 32 * 1024 - len(first) <= 4 * 1024 * 1024
        rest = bytearray(first)
        while chunk := reader.read(_CHUNK):
            rest += chunk
        reader.close()
        assert bytes(rest) == data

    _run_all(job)


def test_closing_a_paused_download_leaves_no_thread_and_a_usable_session(fs: SSHFilesystem, tmp_path: Path) -> None:
    data = os.urandom(_LARGE)
    (tmp_path / "src").write_bytes(data)
    before = set(threading.enumerate())

    def job() -> None:
        reader = fs.read(VPath(tmp_path / "src", fs))
        assert reader.read(_CHUNK) == data[:_CHUNK]
        reader.close()
        assert _download(fs, tmp_path / "src") == data

    _run_all(job)
    assert [thread.name for thread in set(threading.enumerate()) - before if "prefetch" in thread.name] == []


@pytest.mark.parametrize("size", [0, 1, 32767, 32768, 32769, 128 * 32768 - 1, 128 * 32768, 128 * 32768 + 1, 200 * 32768 + 7])
def test_download_is_exact_at_request_and_window_boundaries(fs: SSHFilesystem, tmp_path: Path, size: int) -> None:
    data = os.urandom(size)
    (tmp_path / "src").write_bytes(data)

    def job() -> None:
        assert _download(fs, tmp_path / "src") == data
        assert _download(fs, tmp_path / "src", 1000 * 1000) == data
        reader = fs.read(VPath(tmp_path / "src", fs))
        assert reader.read(-1) == data
        assert reader.read(_CHUNK) == b""
        reader.close()

    _run_all(job)


@pytest.mark.parametrize("new_size", [100, _LARGE + _LARGE // 2 + 5, 2 * _LARGE + 100_000])
def test_download_of_a_file_that_changes_size(fs: SSHFilesystem, tmp_path: Path, new_size: int) -> None:
    size = 2 * _LARGE  # more than is requested ahead
    data = os.urandom(max(new_size, size))
    (tmp_path / "src").write_bytes(data[:size])

    def job() -> None:
        reader = fs.read(VPath(tmp_path / "src", fs))
        received = bytearray(reader.read(_CHUNK))
        assert fs.stat_uncached(VPath(tmp_path / "src", fs)).size == size  # collects the replies already under way
        with (tmp_path / "src").open("r+b") as file:
            file.truncate(new_size)
            file.seek(min(new_size, size))
            file.write(data[size:new_size])
        while chunk := reader.read(_CHUNK):
            received += chunk
        reader.close()
        # What was already on its way when the file shrank may still arrive, but never altered or out of place.
        assert bytes(received) == data[: len(received)]
        if new_size >= size:
            assert len(received) == new_size
        else:
            assert new_size <= len(received) < size

    _run_all(job)


def test_download_with_a_server_that_answers_short(fs: SSHFilesystem, tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    data = os.urandom(512 * 1024 + 3)
    (tmp_path / "src").write_bytes(data)
    read = StubSFTPHandle.read
    monkeypatch.setattr(StubSFTPHandle, "read", lambda self, offset, length: read(self, offset, min(length, 10_000)))

    def job() -> None:
        assert _download(fs, tmp_path / "src") == data

    _run_all(job)


def test_download_error_in_the_middle_reaches_the_reader(fs: SSHFilesystem, tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    data = os.urandom(_LARGE)
    (tmp_path / "src").write_bytes(data)
    read = StubSFTPHandle.read

    def failing_read(self: StubSFTPHandle, offset: int, length: int) -> bytes | int:
        return paramiko.sftp.SFTP_FAILURE if offset >= _LARGE // 2 else read(self, offset, length)

    monkeypatch.setattr(StubSFTPHandle, "read", failing_read)

    def job() -> None:
        with pytest.raises(OSError, match="Failure"):
            _download(fs, tmp_path / "src")
        assert fs.stat_uncached(VPath(tmp_path / "src", fs)).size == len(data)

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


def test_many_copies_within_one_connection_do_not_hang(fs: SSHFilesystem, tmp_path: Path) -> None:
    # Together the uploads must not leave more unanswered than the server's input window holds.
    data = os.urandom(_LARGE)
    (tmp_path / "src").write_bytes(data)

    def copy(target: Path) -> Callable[[], None]:
        def job() -> None:
            reader = fs.read(VPath(tmp_path / "src", fs))
            writer = fs.write(VPath(target, fs))
            while chunk := reader.read(_CHUNK):
                writer.write(chunk)
            writer.close()
            reader.close()

        return job

    targets = [tmp_path / f"dst{index}" for index in range(8)]
    _run_all(*(copy(target) for target in targets), timeout=60.0)
    assert all(target.read_bytes() == data for target in targets)
