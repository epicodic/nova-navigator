"""File names, links and ``real_path`` on the real OpenSSH containers, checked byte for byte on the server."""

from __future__ import annotations

import asyncio
import sys
import threading
import time
from collections.abc import Callable
from pathlib import PurePosixPath

import pytest

from nova_navigator.vfs.filesystems.ssh import SSHFilesystem
from nova_navigator.vfs.vpath import VPath
from tests.real_servers.fakes import ConnectFactory
from tests.real_servers.ssh import SshServer

pytestmark = [pytest.mark.real_server, pytest.mark.integration, pytest.mark.ssh]

HOSTILE_NAMES = [
    "a b", " lead", "trail ", "q'uote", 'dq"uote', "back\\slash", "$HOME", "$(id)", "`id`", "semi;colon",
    "amp&amp", "pipe|pipe", "star*", "quest?", "[brackets]", "{braces}", "new\nline", "tab\there",
    "-dash", "--help", "#hash", "~tilde", "percent%s", "ümläut", "<angle>", "!bang",
]  # fmt: skip


def _touch(server: SshServer, directory: str, names: list[str]) -> None:
    """Create *names* in *directory* on the server; the names travel on stdin, NUL separated."""
    server.run('cd "$1" && xargs -0 touch --', directory, input=b"\0".join(name.encode() for name in names) + b"\0")


@pytest.mark.asyncio
async def test_listing_of_hostile_names(ssh_server: SshServer, connect: ConnectFactory, workdir: Callable[[SshServer], str]) -> None:
    fs = await connect(ssh_server)
    directory = workdir(ssh_server)
    _touch(ssh_server, directory, HOSTILE_NAMES)
    names = [vp.name async for vp in VPath(directory, fs).iterdir()]
    assert sorted(names) == sorted(HOSTILE_NAMES)


@pytest.mark.asyncio
@pytest.mark.parametrize("dirname", HOSTILE_NAMES)
async def test_listing_inside_a_hostile_directory(ssh_server: SshServer, connect: ConnectFactory, workdir: Callable[[SshServer], str], dirname: str) -> None:
    fs = await connect(ssh_server)
    directory = f"{workdir(ssh_server)}/{dirname}"
    ssh_server.run('mkdir -- "$1"', directory)
    _touch(ssh_server, directory, ["inner"])
    names = [vp.name async for vp in VPath(directory, fs).iterdir()]
    assert names == ["inner"]


@pytest.mark.asyncio
async def test_symlink_create_and_readlink(ssh_server: SshServer, connect: ConnectFactory, workdir: Callable[[SshServer], str]) -> None:
    fs = await connect(ssh_server)
    directory = workdir(ssh_server)
    target = '../some dir/it\'s "quoted" $HOME $(id) ümläut'
    fs.symlink(VPath(f"{directory}/link", fs), target)
    assert fs.readlink(VPath(f"{directory}/link", fs)) == target
    assert ssh_server.run('readlink -- "$1"', f"{directory}/link").decode().rstrip("\n") == target


@pytest.mark.asyncio
async def test_listing_reports_links(ssh_server: SshServer, connect: ConnectFactory, workdir: Callable[[SshServer], str]) -> None:
    fs = await connect(ssh_server)
    directory = workdir(ssh_server)
    ssh_server.run('cd "$1" && touch file && mkdir dir && ln -s file to_file && ln -s missing broken && ln -s dir to_dir', directory)
    entries = {vp.name: vp.stat async for vp in VPath(directory, fs).iterdir()}
    assert (entries["file"].is_symlink, entries["file"].is_directory) == (False, False)
    assert (entries["dir"].is_symlink, entries["dir"].is_directory) == (False, True)
    assert (entries["to_file"].is_symlink, entries["to_file"].is_broken_symlink) == (True, False)
    assert (entries["broken"].is_symlink, entries["broken"].is_broken_symlink) == (True, True)
    assert (entries["to_dir"].is_symlink, entries["to_dir"].is_directory) == (True, True)


@pytest.mark.asyncio
async def test_real_path(ssh_server: SshServer, connect: ConnectFactory, workdir: Callable[[SshServer], str]) -> None:
    fs = await connect(ssh_server)
    directory = workdir(ssh_server)
    ssh_server.run('mkdir "$1/real" && ln -s real "$1/lnk"', directory)
    resolved = fs.real_path(VPath(f"{directory}/lnk", fs))
    assert resolved is not None
    assert resolved.path == PurePosixPath(f"{directory}/real")


@pytest.mark.asyncio
async def test_real_path_of_broken_link_raises(ssh_server: SshServer, connect: ConnectFactory, workdir: Callable[[SshServer], str]) -> None:
    fs = await connect(ssh_server)
    directory = workdir(ssh_server)
    ssh_server.run('ln -s "$2" "$1"', f"{directory}/broken", f"{directory}/missing")
    with pytest.raises(FileNotFoundError):
        fs.real_path(VPath(f"{directory}/broken", fs))


def _run_all(*jobs: Callable[[], None], timeout: float = 45.0) -> None:
    """Run *jobs* in daemon threads, so a hung SFTP request fails the test instead of the run."""
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


def _read_all(fs: SSHFilesystem, path: str) -> bytes:
    reader = fs.read(VPath(path, fs))
    received = bytearray()
    while chunk := reader.read(65536):
        received += chunk
    reader.close()
    return bytes(received)


@pytest.mark.asyncio
async def test_concurrent_operations_on_one_connection(ssh_server: SshServer, connect: ConnectFactory, workdir: Callable[[SshServer], str]) -> None:
    fs = await connect(ssh_server)
    directory = workdir(ssh_server)
    ssh_server.run('cd "$1" && head -c 6291456 /dev/urandom > big && touch a b c && ln -s a link', directory)
    expected = ssh_server.run('cat -- "$1"', f"{directory}/big")
    upload = bytes(range(256)) * 24576

    def stat_job() -> None:
        for _ in range(200):
            assert fs.stat_uncached(VPath(f"{directory}/link", fs)).is_symlink

    async def names() -> set[str]:
        return {vp.name async for vp in VPath(directory, fs).iterdir()}

    def list_job() -> None:
        for _ in range(20):
            fs.refresh()
            assert {"a", "b", "c", "big", "link"} <= asyncio.run(names())

    def read_job() -> None:
        assert _read_all(fs, f"{directory}/big") == expected

    def write_job() -> None:
        writer = fs.write(VPath(f"{directory}/up", fs))
        for start in range(0, len(upload), 65536):
            writer.write(upload[start : start + 65536])
        writer.close()

    _run_all(stat_job, list_job, read_job, write_job)
    assert ssh_server.run('cat -- "$1"', f"{directory}/up") == upload


@pytest.mark.asyncio
async def test_copy_within_one_connection(ssh_server: SshServer, connect: ConnectFactory, workdir: Callable[[SshServer], str]) -> None:
    fs = await connect(ssh_server)
    directory = workdir(ssh_server)
    # Larger than the channel windows, so the server stops taking writes while its download is not consumed.
    ssh_server.run('head -c 33554432 /dev/urandom > "$1"', f"{directory}/src")

    def copy_job() -> None:
        reader = fs.read(VPath(f"{directory}/src", fs))
        writer = fs.write(VPath(f"{directory}/dst", fs))
        while chunk := reader.read(65536):
            writer.write(chunk)
        writer.close()
        reader.close()

    _run_all(copy_job)
    ssh_server.run('cmp -- "$1" "$2"', f"{directory}/src", f"{directory}/dst")
