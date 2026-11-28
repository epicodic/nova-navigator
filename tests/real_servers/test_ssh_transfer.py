"""Copy, move and delete between the local disk and the real OpenSSH servers, checked on the server with tar."""

from __future__ import annotations

import hashlib
import io
import os
import tarfile
from collections.abc import Callable
from pathlib import Path

import pytest

from nova_navigator.filemanager.tasks import EraseFilesOptions, copy_files, erase_files, move_files
from nova_navigator.vfs.filesystems.local import LocalFilesystem
from nova_navigator.vfs.filesystems.ssh import SSHFilesystem
from nova_navigator.vfs.vpath import VPath
from tests.filemanager.common import run_task
from tests.real_servers.fakes import ConnectFactory
from tests.real_servers.ssh import SshServer

pytestmark = [pytest.mark.real_server, pytest.mark.integration, pytest.mark.ssh]

PAYLOAD = "payload"
HOSTILE_DIR = "dir with 'quotes' & $HOME"
HOSTILE_FILE = "it's $(id) ümläut.txt"
BLOB = os.urandom(1 << 20)
FILES: dict[str, bytes] = {
    "deep/nested/a.txt": b"alpha\n",
    f"{HOSTILE_DIR}/{HOSTILE_FILE}": b"hostile\n",
    "blob.bin": BLOB,
}
DIRS = ["empty", "deep/nested/empty2"]
LINKS = {"link_file": "blob.bin", "link_dir": "deep"}

Tree = dict[str, tuple[str, ...]]


def _tree_of_dir(root: Path) -> Tree:
    """Describe *root* on the local disk: files by digest, directories, links by target text."""
    tree: Tree = {}
    for dirpath, dirnames, filenames in os.walk(root):
        for name in dirnames + filenames:
            full = Path(dirpath) / name
            rel = full.relative_to(root).as_posix()
            if full.is_symlink():
                tree[rel] = ("link", os.readlink(full))
            elif full.is_dir():
                tree[rel] = ("dir",)
            else:
                tree[rel] = ("file", hashlib.sha256(full.read_bytes()).hexdigest())
    return tree


def _tree_of_tar(data: bytes) -> Tree:
    """Describe the payload inside a tar archive in the same form as :func:`_tree_of_dir`."""
    tree: Tree = {}
    with tarfile.open(fileobj=io.BytesIO(data)) as archive:
        for member in archive.getmembers():
            name = member.name.rstrip("/")
            if name in ("", PAYLOAD):
                continue
            rel = name.removeprefix(f"{PAYLOAD}/")
            if member.issym():
                tree[rel] = ("link", member.linkname)
            elif member.isdir():
                tree[rel] = ("dir",)
            else:
                reader = archive.extractfile(member)
                assert reader is not None
                tree[rel] = ("file", hashlib.sha256(reader.read()).hexdigest())
    return tree


def _payload_tar(with_links: bool = False) -> bytes:
    """The payload as a tar archive, for arranging files on a server in one command."""
    buffer = io.BytesIO()
    with tarfile.open(fileobj=buffer, mode="w") as archive:
        archive.addfile(_info(PAYLOAD, tarfile.DIRTYPE))
        for directory in DIRS:
            archive.addfile(_info(f"{PAYLOAD}/{directory}", tarfile.DIRTYPE))
        for rel, data in FILES.items():
            archive.addfile(_info(f"{PAYLOAD}/{rel}", tarfile.REGTYPE, len(data)), io.BytesIO(data))
        if with_links:
            for name, target in LINKS.items():
                info = _info(f"{PAYLOAD}/{name}", tarfile.SYMTYPE)
                info.linkname = target
                archive.addfile(info)
    return buffer.getvalue()


def _info(name: str, kind: bytes, size: int = 0) -> tarfile.TarInfo:
    info = tarfile.TarInfo(name)
    info.type = kind
    info.size = size
    info.mode = 0o755 if kind == tarfile.DIRTYPE else 0o644
    return info


def _write_local_payload(src: Path) -> None:
    """Create the payload below *src* on the local disk."""
    for directory in DIRS:
        (src / PAYLOAD / directory).mkdir(parents=True)
    for rel, data in FILES.items():
        target = src / PAYLOAD / rel
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_bytes(data)


def _arrange(server: SshServer, base: str, with_links: bool = False) -> None:
    """Put the payload below *base* on the server."""
    server.run('tar -x -C "$1"', base, input=_payload_tar(with_links))


def _remote_tree(server: SshServer, base: str) -> Tree:
    return _tree_of_tar(server.run('tar -c -C "$1" "$2"', base, PAYLOAD))


def _exists(server: SshServer, path: str) -> bool:
    return server.run('if [ -e "$1" ] || [ -L "$1" ]; then echo yes; else echo no; fi', path).strip() == b"yes"


def _local(path: Path) -> VPath:
    return LocalFilesystem().path(str(path))


@pytest.mark.asyncio
async def test_copy_local_to_remote(ssh_server: SshServer, connect: ConnectFactory, workdir: Callable[[SshServer], str], tmp_path: Path) -> None:
    fs: SSHFilesystem = await connect(ssh_server)
    base = workdir(ssh_server)
    _write_local_payload(tmp_path)
    await run_task(lambda ctx: copy_files(ctx, [_local(tmp_path / PAYLOAD)], VPath(base, fs)))
    assert _remote_tree(ssh_server, base) == _tree_of_dir(tmp_path / PAYLOAD)


@pytest.mark.asyncio
async def test_copy_remote_to_local(ssh_server: SshServer, connect: ConnectFactory, workdir: Callable[[SshServer], str], tmp_path: Path) -> None:
    fs: SSHFilesystem = await connect(ssh_server)
    base = workdir(ssh_server)
    _arrange(ssh_server, base)
    dst = tmp_path / "dst"
    dst.mkdir()
    await run_task(lambda ctx: copy_files(ctx, [VPath(f"{base}/{PAYLOAD}", fs)], _local(dst)))
    assert _tree_of_dir(dst / PAYLOAD) == _remote_tree(ssh_server, base)


@pytest.mark.asyncio
async def test_copy_between_the_two_servers(ssh_pair: dict[str, SshServer], connect: ConnectFactory, workdir: Callable[[SshServer], str]) -> None:
    source, target = ssh_pair["gnu"], ssh_pair["busybox"]
    src_fs: SSHFilesystem = await connect(source)
    dst_fs: SSHFilesystem = await connect(target)
    src_base = workdir(source)
    dst_base = workdir(target)
    _arrange(source, src_base)
    await run_task(lambda ctx: copy_files(ctx, [VPath(f"{src_base}/{PAYLOAD}", src_fs)], VPath(dst_base, dst_fs)))
    assert _remote_tree(target, dst_base) == _remote_tree(source, src_base)


@pytest.mark.asyncio
async def test_move_local_to_remote(ssh_server: SshServer, connect: ConnectFactory, workdir: Callable[[SshServer], str], tmp_path: Path) -> None:
    fs: SSHFilesystem = await connect(ssh_server)
    base = workdir(ssh_server)
    _write_local_payload(tmp_path)
    expected = _tree_of_dir(tmp_path / PAYLOAD)
    await run_task(lambda ctx: move_files(ctx, [_local(tmp_path / PAYLOAD)], VPath(base, fs)))
    assert _remote_tree(ssh_server, base) == expected
    assert not (tmp_path / PAYLOAD).exists()


@pytest.mark.asyncio
async def test_move_remote_to_local(ssh_server: SshServer, connect: ConnectFactory, workdir: Callable[[SshServer], str], tmp_path: Path) -> None:
    fs: SSHFilesystem = await connect(ssh_server)
    base = workdir(ssh_server)
    _arrange(ssh_server, base)
    expected = _remote_tree(ssh_server, base)
    dst = tmp_path / "dst"
    dst.mkdir()
    await run_task(lambda ctx: move_files(ctx, [VPath(f"{base}/{PAYLOAD}", fs)], _local(dst)))
    assert _tree_of_dir(dst / PAYLOAD) == expected
    assert not _exists(ssh_server, f"{base}/{PAYLOAD}")


@pytest.mark.asyncio
async def test_delete_files_and_trees(ssh_server: SshServer, connect: ConnectFactory, workdir: Callable[[SshServer], str]) -> None:
    fs: SSHFilesystem = await connect(ssh_server)
    base = workdir(ssh_server)
    _arrange(ssh_server, base)
    paths = [VPath(f"{base}/{PAYLOAD}/deep", fs), VPath(f"{base}/{PAYLOAD}/blob.bin", fs)]
    await run_task(lambda ctx: erase_files(ctx, paths, EraseFilesOptions(ask_before_erase=False)))
    assert not _exists(ssh_server, f"{base}/{PAYLOAD}/deep")
    assert not _exists(ssh_server, f"{base}/{PAYLOAD}/blob.bin")
    assert _exists(ssh_server, f"{base}/{PAYLOAD}/{HOSTILE_DIR}/{HOSTILE_FILE}")


@pytest.mark.asyncio
async def test_delete_several_paths_at_once(ssh_server: SshServer, connect: ConnectFactory, workdir: Callable[[SshServer], str]) -> None:
    fs: SSHFilesystem = await connect(ssh_server)
    base = workdir(ssh_server)
    _arrange(ssh_server, base, with_links=True)
    paths = [VPath(f"{base}/{PAYLOAD}/{name}", fs) for name in ("link_file", "link_dir", f"{HOSTILE_DIR}/{HOSTILE_FILE}")]
    await run_task(lambda ctx: erase_files(ctx, paths, EraseFilesOptions(ask_before_erase=False)))
    for name in ("link_file", "link_dir", f"{HOSTILE_DIR}/{HOSTILE_FILE}"):
        assert not _exists(ssh_server, f"{base}/{PAYLOAD}/{name}")
    assert _exists(ssh_server, f"{base}/{PAYLOAD}/blob.bin")
    assert _exists(ssh_server, f"{base}/{PAYLOAD}/deep/nested/a.txt")


@pytest.mark.asyncio
async def test_delete_link_keeps_target(ssh_server: SshServer, connect: ConnectFactory, workdir: Callable[[SshServer], str]) -> None:
    fs: SSHFilesystem = await connect(ssh_server)
    base = workdir(ssh_server)
    _arrange(ssh_server, base, with_links=True)
    expected = _remote_tree(ssh_server, base)
    await run_task(lambda ctx: erase_files(ctx, [VPath(f"{base}/{PAYLOAD}/link_dir", fs)], EraseFilesOptions(ask_before_erase=False)))
    assert not _exists(ssh_server, f"{base}/{PAYLOAD}/link_dir")
    remaining = {rel: value for rel, value in expected.items() if not rel.startswith("link_dir")}
    assert _remote_tree(ssh_server, base) == remaining
    assert _exists(ssh_server, f"{base}/{PAYLOAD}/deep/nested/a.txt")


@pytest.mark.asyncio
async def test_copy_preserves_links(ssh_server: SshServer, connect: ConnectFactory, workdir: Callable[[SshServer], str], tmp_path: Path) -> None:
    fs: SSHFilesystem = await connect(ssh_server)
    base = workdir(ssh_server)
    _arrange(ssh_server, base, with_links=True)
    expected = _remote_tree(ssh_server, base)
    dst = tmp_path / "dst"
    dst.mkdir()
    await run_task(lambda ctx: copy_files(ctx, [VPath(f"{base}/{PAYLOAD}", fs)], _local(dst)))
    assert _tree_of_dir(dst / PAYLOAD) == expected
    assert ("link", "blob.bin") in expected.values()
