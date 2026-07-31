"""Tests for command registry, alias expansion, and ShellContext.resolve()."""

from __future__ import annotations

import argparse

import pytest

from nova_navigator.terminal.vfs_shell.aliases import AliasStore
from nova_navigator.terminal.vfs_shell.command import Command, ShellArgumentParser, ShellContext
from nova_navigator.terminal.vfs_shell.interpreter import VfsShellInterpreter
from nova_navigator.terminal.vfs_shell.registry import CommandRegistry
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
