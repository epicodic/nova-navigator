"""File names, links and ``real_path`` on the real OpenSSH containers, checked byte for byte on the server."""

from __future__ import annotations

from collections.abc import Callable
from pathlib import PurePosixPath

import pytest

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
