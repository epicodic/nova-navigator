"""Editing a remote file through a local copy: the save reaches the real OpenSSH servers and keeps links (S4)."""

from __future__ import annotations

import hashlib
from collections.abc import AsyncIterator, Callable
from contextlib import asynccontextmanager

import pytest

from nova_navigator.local_copies.manager import CopyStatus, LocalCopyManager
from nova_navigator.vfs.filesystems.ssh import SSHFilesystem
from nova_navigator.vfs.vpath import VPath
from tests._utils.local_copy_helpers import ScriptedRunner
from tests.real_servers.fakes import ConnectFactory
from tests.real_servers.ssh import SshServer

pytestmark = [pytest.mark.real_server, pytest.mark.integration, pytest.mark.ssh]


@asynccontextmanager
async def _manager(tmp_path_factory: pytest.TempPathFactory) -> AsyncIterator[LocalCopyManager]:
    root = tmp_path_factory.mktemp("copies")
    manager = LocalCopyManager(root, ScriptedRunner(), poll_interval=0.05, settle_time=0.1)
    try:
        yield manager
    finally:
        await manager.shutdown()


def _sha256(server: SshServer, path: str) -> str:
    return server.run('sha256sum -- "$1"', path).decode().split()[0]


@pytest.mark.asyncio
async def test_edit_and_save_back(ssh_server: SshServer, connect: ConnectFactory, workdir: Callable[[SshServer], str], tmp_path_factory: pytest.TempPathFactory) -> None:
    fs: SSHFilesystem = await connect(ssh_server)
    remote = f"{workdir(ssh_server)}/doc.txt"
    ssh_server.run('printf old > "$1"', remote)
    async with _manager(tmp_path_factory) as manager:
        entry = await manager.open(VPath(remote, fs))
        assert entry is not None
        entry.copy.path.write_bytes(b"new content\n" * 1000)
        await manager.sync_now(entry)
        assert entry.status is CopyStatus.SYNCED, entry.error
    assert _sha256(ssh_server, remote) == hashlib.sha256(b"new content\n" * 1000).hexdigest()


@pytest.mark.asyncio
async def test_save_through_a_symlink_keeps_the_link(ssh_server: SshServer, connect: ConnectFactory, workdir: Callable[[SshServer], str], tmp_path_factory: pytest.TempPathFactory) -> None:
    fs: SSHFilesystem = await connect(ssh_server)
    base = workdir(ssh_server)
    target = f"{base}/real/doc.txt"
    link = f"{base}/links/doc.txt"
    ssh_server.run('mkdir -p "$1" "$2" && printf old > "$3" && ln -s "$3" "$4"', f"{base}/real", f"{base}/links", target, link)
    async with _manager(tmp_path_factory) as manager:
        entry = await manager.open(VPath(link, fs))
        assert entry is not None
        entry.copy.path.write_bytes(b"first\n")
        await manager.sync_now(entry)
        assert entry.status is CopyStatus.SYNCED, entry.error
        assert ssh_server.run('readlink -- "$1"', link).decode().rstrip("\n") == target
        assert _sha256(ssh_server, target) == hashlib.sha256(b"first\n").hexdigest()

        entry.copy.path.write_bytes(b"second\n")
        await manager.sync_now(entry)
        assert entry.status is CopyStatus.SYNCED, entry.error
    assert ssh_server.run('readlink -- "$1"', link).decode().rstrip("\n") == target
    assert _sha256(ssh_server, target) == hashlib.sha256(b"second\n").hexdigest()
