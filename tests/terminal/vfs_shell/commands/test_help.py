"""Tests for help command."""

from __future__ import annotations

import pytest

from nova_navigator.terminal.vfs_shell.interpreter import VfsShellInterpreter
from tests._utils.mock_filesystem import MockFilesystem


@pytest.fixture
def fs() -> MockFilesystem:
    return MockFilesystem()


@pytest.mark.asyncio
async def test_help_lists_commands(fs: MockFilesystem) -> None:
    interp = VfsShellInterpreter(fs, fs.cwd(), cols=80, rows=24)
    output: list[str] = []
    exit_code = await interp.execute("help", output.append, output.append)
    assert exit_code == 0
    text = "".join(output)
    assert "ls" in text
    assert "cd" in text
    assert "rm" in text


@pytest.mark.asyncio
async def test_help_specific_command(fs: MockFilesystem) -> None:
    interp = VfsShellInterpreter(fs, fs.cwd(), cols=80, rows=24)
    output: list[str] = []
    exit_code = await interp.execute("help ls", output.append, output.append)
    assert exit_code == 0
    text = "".join(output)
    assert "ls" in text


@pytest.mark.asyncio
async def test_help_unknown_command(fs: MockFilesystem) -> None:
    interp = VfsShellInterpreter(fs, fs.cwd(), cols=80, rows=24)
    output: list[str] = []
    exit_code = await interp.execute("help nonexistent", output.append, output.append)
    assert exit_code != 0
