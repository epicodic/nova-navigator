"""Tests for the history command."""

from __future__ import annotations

import pytest

from nova_navigator.terminal.vfs_shell.interpreter import VfsShellInterpreter
from tests._utils.mock_filesystem import MockFilesystem


@pytest.fixture
def fs() -> MockFilesystem:
    return MockFilesystem()


@pytest.mark.asyncio
async def test_history_lists_entries(fs: MockFilesystem) -> None:
    interp = VfsShellInterpreter(fs, fs.cwd(), cols=80, rows=24)
    output: list[str] = []
    exit_code = await interp.execute("history", output.append, output.append, history=["ls -la", "cd /tmp"])
    assert exit_code == 0
    text = "".join(output)
    assert "ls -la" in text
    assert "cd /tmp" in text


@pytest.mark.asyncio
async def test_history_numbers_entries(fs: MockFilesystem) -> None:
    interp = VfsShellInterpreter(fs, fs.cwd(), cols=80, rows=24)
    output: list[str] = []
    await interp.execute("history", output.append, output.append, history=["ls", "pwd"])
    text = "".join(output)
    assert "1" in text
    assert "2" in text


@pytest.mark.asyncio
async def test_history_empty(fs: MockFilesystem) -> None:
    interp = VfsShellInterpreter(fs, fs.cwd(), cols=80, rows=24)
    output: list[str] = []
    exit_code = await interp.execute("history", output.append, output.append)
    assert exit_code == 0
    assert "".join(output) == ""
