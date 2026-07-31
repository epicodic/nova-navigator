"""Tests for the alias command."""

from __future__ import annotations

import pytest

from nova_navigator.terminal.vfs_shell.interpreter import VfsShellInterpreter
from tests._utils.mock_filesystem import MockFilesystem


@pytest.fixture
def fs() -> MockFilesystem:
    return MockFilesystem()


@pytest.mark.asyncio
async def test_alias_lists_defaults(fs: MockFilesystem) -> None:
    interp = VfsShellInterpreter(fs, fs.cwd(), cols=80, rows=24)
    output: list[str] = []
    exit_code = await interp.execute("alias", output.append, output.append)
    assert exit_code == 0
    text = "".join(output)
    assert "ll='ls -l'" in text
    assert "dir='ls'" in text


@pytest.mark.asyncio
async def test_alias_define_new(fs: MockFilesystem) -> None:
    interp = VfsShellInterpreter(fs, fs.cwd(), cols=80, rows=24)
    output: list[str] = []
    exit_code = await interp.execute("alias lh='ls -lh'", output.append, output.append)
    assert exit_code == 0

    output.clear()
    exit_code = await interp.execute("alias", output.append, output.append)
    text = "".join(output)
    assert "lh='ls -lh'" in text


@pytest.mark.asyncio
async def test_alias_remove(fs: MockFilesystem) -> None:
    interp = VfsShellInterpreter(fs, fs.cwd(), cols=80, rows=24)
    output: list[str] = []
    exit_code = await interp.execute("alias -r ll", output.append, output.append)
    assert exit_code == 0

    output.clear()
    exit_code = await interp.execute("alias", output.append, output.append)
    text = "".join(output)
    assert "ll=" not in text


@pytest.mark.asyncio
async def test_alias_remove_nonexistent(fs: MockFilesystem) -> None:
    interp = VfsShellInterpreter(fs, fs.cwd(), cols=80, rows=24)
    output: list[str] = []
    exit_code = await interp.execute("alias -r nope", output.append, output.append)
    assert exit_code == 1


@pytest.mark.asyncio
async def test_alias_expansion_with_args(fs: MockFilesystem) -> None:
    """Typing 'll /tmp' should expand to 'ls -l /tmp'."""
    interp = VfsShellInterpreter(fs, fs.cwd(), cols=80, rows=24)
    output: list[str] = []
    # ll is a default alias for 'ls -l', so 'll /' should list root in long form
    exit_code = await interp.execute("ll /", output.append, output.append)
    assert exit_code == 0
    text = "".join(output)
    assert "home" in text


@pytest.mark.asyncio
async def test_alias_simple_rename(fs: MockFilesystem) -> None:
    """'dir' is aliased to 'ls' — should work like 'ls'."""
    interp = VfsShellInterpreter(fs, fs.cwd(), cols=80, rows=24)
    output: list[str] = []
    exit_code = await interp.execute("dir", output.append, output.append)
    assert exit_code == 0
