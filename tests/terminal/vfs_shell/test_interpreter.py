"""Tests for command registry, alias expansion, and ShellContext.resolve()."""

from __future__ import annotations

import argparse
import asyncio
import tarfile
import zipfile
from pathlib import Path

import pytest

from nova_navigator.terminal.vfs_shell.aliases import AliasStore
from nova_navigator.terminal.vfs_shell.command import Command, ShellArgumentParser, ShellContext
from nova_navigator.terminal.vfs_shell.interpreter import VfsShellInterpreter
from nova_navigator.terminal.vfs_shell.registry import CommandRegistry
from nova_navigator.terminal.vfs_shell.virtual_pty_backend import VirtualPtyBackend
from nova_navigator.vfs.filesystem import FilesystemCapabilities
from nova_navigator.vfs.filesystems.archive import ArchiveFilesystem
from nova_navigator.vfs.filesystems.local import LocalFilesystem
from nova_navigator.vfs.vpath import VPath
from tests._utils.mock_filesystem import MockFilesystem


class _DummyCommand(Command):
    @property
    def name(self) -> str:
        return "dummy"

    def create_parser(self) -> ShellArgumentParser:
        p = ShellArgumentParser(prog="dummy", add_help=False)
        p.add_argument("args", nargs="*")
        return p

    async def execute(self, args: argparse.Namespace, ctx: ShellContext) -> int:
        return 0


class _DummyCommandNamed(Command):
    def __init__(self, cmd_name: str) -> None:
        self._name = cmd_name

    @property
    def name(self) -> str:
        return self._name

    def create_parser(self) -> ShellArgumentParser:
        return ShellArgumentParser(prog=self._name, add_help=False)

    async def execute(self, args: argparse.Namespace, ctx: ShellContext) -> int:
        return 0


class _ReadOnlyMockFilesystem(MockFilesystem):
    @property
    def capabilities(self) -> FilesystemCapabilities:
        return FilesystemCapabilities(read_only=True)

    @property
    def scheme(self) -> str:
        return "snapshot"


def _make_ctx() -> ShellContext:
    """Create a ShellContext with a MockFilesystem cwd at /home/user."""
    fs = MockFilesystem()
    return ShellContext(
        filesystem=fs,
        cwd=fs.cwd(),
        cols=80,
        rows=24,
        write_fn=lambda _: None,
        write_error_fn=lambda _: None,
        cancel_fn=lambda: False,
    )


def test_registry_lookup_by_name() -> None:
    reg = CommandRegistry()
    cmd = _DummyCommand()
    reg.register(cmd)
    assert reg.get("dummy") is cmd


def test_registry_unknown_returns_none() -> None:
    reg = CommandRegistry()
    assert reg.get("nonexistent") is None


def test_registry_all_commands() -> None:
    reg = CommandRegistry()
    cmd = _DummyCommand()
    reg.register(cmd)
    assert cmd in reg.all_commands()


def test_registry_all_commands_sorted() -> None:
    reg = CommandRegistry()
    cmd_z = _DummyCommandNamed("zebra")
    cmd_a = _DummyCommandNamed("alpha")
    reg.register(cmd_z)
    reg.register(cmd_a)
    names = [c.name for c in reg.all_commands()]
    assert names == ["alpha", "zebra"]


# ---------------------------------------------------------------------------
# AliasStore
# ---------------------------------------------------------------------------


def test_alias_store_get_and_set() -> None:
    store = AliasStore()
    assert store.get("ll") is None
    store.set("ll", "ls -l")
    assert store.get("ll") == "ls -l"


def test_alias_store_remove() -> None:
    store = AliasStore({"ll": "ls -l"})
    assert store.remove("ll") is True
    assert store.get("ll") is None
    assert store.remove("ll") is False


def test_alias_store_items_sorted() -> None:
    store = AliasStore({"ll": "ls -l", "dir": "ls", "la": "ls -la"})
    assert store.items() == [("dir", "ls"), ("la", "ls -la"), ("ll", "ls -l")]


def test_alias_store_names_sorted() -> None:
    store = AliasStore({"ll": "ls -l", "dir": "ls"})
    assert store.names() == ["dir", "ll"]


def test_resolve_absolute() -> None:
    ctx = _make_ctx()
    result = ctx.resolve("/etc/passwd")
    assert str(result.path) == "/etc/passwd"


def test_resolve_relative() -> None:
    ctx = _make_ctx()
    result = ctx.resolve("subdir")
    assert str(result.path) == "/home/user/subdir"


def test_resolve_tilde() -> None:
    ctx = _make_ctx()
    result = ctx.resolve("~")
    assert str(result.path) == "/home/user"


def test_resolve_tilde_subdir() -> None:
    ctx = _make_ctx()
    result = ctx.resolve("~/documents")
    assert str(result.path) == "/home/user/documents"


def test_resolve_dotdot() -> None:
    ctx = _make_ctx()
    result = ctx.resolve("..")
    assert str(result.path) == "/home"


def test_resolve_multi_dotdot() -> None:
    ctx = _make_ctx()
    result = ctx.resolve("../../etc")
    assert str(result.path) == "/etc"


def test_resolve_tilde_with_dotdot() -> None:
    ctx = _make_ctx()
    result = ctx.resolve("~/foo/../bar")
    assert str(result.path) == "/home/user/bar"


def test_resolve_dotdot_past_root() -> None:
    ctx = _make_ctx()
    result = ctx.resolve("/../../etc")
    assert str(result.path) == "/etc"


@pytest.fixture
def fs() -> MockFilesystem:
    return MockFilesystem(
        {
            "/home/user/file.txt": b"hello",
        }
    )


@pytest.mark.asyncio
async def test_unknown_command(fs: MockFilesystem) -> None:
    interp = VfsShellInterpreter(fs, fs.cwd(), cols=80, rows=24)
    output: list[str] = []
    exit_code = await interp.execute("nonexistent", output.append, output.append)
    assert exit_code != 0
    assert any("command not found" in line for line in output)


@pytest.mark.asyncio
async def test_empty_line(fs: MockFilesystem) -> None:
    interp = VfsShellInterpreter(fs, fs.cwd(), cols=80, rows=24)
    output: list[str] = []
    exit_code = await interp.execute("", output.append, output.append)
    assert exit_code == 0


@pytest.mark.asyncio
async def test_pwd_command(fs: MockFilesystem) -> None:
    interp = VfsShellInterpreter(fs, fs.cwd(), cols=80, rows=24)
    output: list[str] = []
    exit_code = await interp.execute("pwd", output.append, output.append)
    assert exit_code == 0
    assert any("/home/user" in line for line in output)


@pytest.fixture
def archive_fs(tmp_path: Path) -> tuple[ArchiveFilesystem, Path]:
    archive_path = tmp_path / "sample.zip"
    with zipfile.ZipFile(archive_path, "w") as archive:
        archive.writestr("file.txt", "original")
    local = LocalFilesystem()
    return ArchiveFilesystem(VPath(tmp_path, local), VPath(archive_path, local)), archive_path


@pytest.fixture
def tar_fifo_fs(tmp_path: Path) -> ArchiveFilesystem:
    archive_path = tmp_path / "special.tar"
    with tarfile.open(archive_path, "w") as archive:
        fifo = tarfile.TarInfo("fifo")
        fifo.type = tarfile.FIFOTYPE
        archive.addfile(fifo)
    local = LocalFilesystem()
    return ArchiveFilesystem(VPath(tmp_path, local), VPath(archive_path, local))


@pytest.mark.asyncio
@pytest.mark.parametrize(
    ("line", "command_name"),
    [
        ("mv file.txt moved.txt", "mv"),
        ("rm file.txt", "rm"),
        ("mkdir newdir", "mkdir"),
    ],
)
async def test_structural_mutating_commands_rejected_in_archive(archive_fs: tuple[ArchiveFilesystem, Path], line: str, command_name: str) -> None:
    """mv, rm, and mkdir change archive structure, which stays unsupported even for a writable archive."""
    fs, archive_path = archive_fs
    original_bytes = archive_path.read_bytes()
    interp = VfsShellInterpreter(fs, fs.cwd(), cols=80, rows=24)
    output: list[str] = []
    errors: list[str] = []

    exit_code = await interp.execute(line, output.append, errors.append)

    assert exit_code == 1
    assert len(errors) == 1
    assert errors[0].startswith(f"{command_name}: ")
    assert output == []
    assert archive_path.read_bytes() == original_bytes
    with zipfile.ZipFile(archive_path) as archive:
        assert archive.namelist() == ["file.txt"]
        assert archive.read("file.txt") == b"original"


@pytest.mark.asyncio
async def test_cp_writes_new_member_in_writable_archive(archive_fs: tuple[ArchiveFilesystem, Path]) -> None:
    """cp only needs write(), which a writable archive supports, so it adds a new member."""
    fs, archive_path = archive_fs
    interp = VfsShellInterpreter(fs, fs.cwd(), cols=80, rows=24)
    output: list[str] = []
    errors: list[str] = []

    exit_code = await interp.execute("cp file.txt copy.txt", output.append, errors.append)

    assert exit_code == 0
    assert errors == []
    with zipfile.ZipFile(archive_path) as archive:
        assert set(archive.namelist()) == {"file.txt", "copy.txt"}
        assert archive.read("file.txt") == b"original"
        assert archive.read("copy.txt") == b"original"


@pytest.mark.asyncio
async def test_archive_help_still_lists_mutating_commands(archive_fs: tuple[ArchiveFilesystem, Path]) -> None:
    fs, _ = archive_fs
    interp = VfsShellInterpreter(fs, fs.cwd(), cols=80, rows=24)
    output: list[str] = []
    errors: list[str] = []

    exit_code = await interp.execute("help", output.append, errors.append)

    assert exit_code == 0
    assert errors == []
    for name in ("cp", "mv", "rm", "mkdir"):
        assert f"  {name}\r\n" in output
    assert fs.capabilities.commands is False
    # A writable ZIP mount is no longer categorically read-only; mv/rm/mkdir are still
    # rejected individually because they change archive structure, not member content.
    assert fs.capabilities.read_only is False


@pytest.mark.asyncio
async def test_read_only_error_names_filesystem_scheme() -> None:
    fs = _ReadOnlyMockFilesystem({"/home/user/file.txt": b"original"})
    interp = VfsShellInterpreter(fs, fs.cwd(), cols=80, rows=24)
    output: list[str] = []
    errors: list[str] = []

    exit_code = await interp.execute("rm file.txt", output.append, errors.append)

    assert exit_code == 1
    assert errors == ["rm: snapshot is read-only\r\n"]
    assert output == []
    assert fs.stat(fs.cwd() / "file.txt").size == len(b"original")


@pytest.mark.asyncio
async def test_cat_tar_fifo_returns_command_error(tar_fifo_fs: ArchiveFilesystem) -> None:
    interp = VfsShellInterpreter(tar_fifo_fs, tar_fifo_fs.cwd(), cols=80, rows=24)
    output: list[str] = []
    errors: list[str] = []

    exit_code = await interp.execute("cat fifo", output.append, errors.append)

    assert exit_code == 1
    assert output == []
    assert errors == ["cat: Cannot read archive member '/fifo'\r\n"]


@pytest.mark.asyncio
async def test_backend_reprompts_after_tar_fifo_error(tar_fifo_fs: ArchiveFilesystem) -> None:
    backend = VirtualPtyBackend(tar_fifo_fs, tar_fifo_fs.cwd())
    messages: asyncio.Queue[list[object]] = asyncio.Queue()
    backend.attach_readers(asyncio.get_running_loop(), messages)
    backend.open("", 24, 80)
    try:
        await asyncio.sleep(0)
        while not messages.empty():
            messages.get_nowait()

        await backend._run_command("cat fifo")
        await asyncio.sleep(0)

        posted = []
        while not messages.empty():
            posted.append(messages.get_nowait())
        assert ["stdout", "cat: Cannot read archive member '/fifo'\r\n"] in posted
        assert ["stdout", "~$ "] in posted
    finally:
        backend.teardown()
