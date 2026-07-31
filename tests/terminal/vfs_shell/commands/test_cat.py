"""Tests for cat command."""

from __future__ import annotations

import pytest

from nova_navigator.terminal.vfs_shell.interpreter import VfsShellInterpreter
from tests._utils.mock_filesystem import MockFilesystem


@pytest.fixture
def fs() -> MockFilesystem:
    return MockFilesystem(
        {
            "/home/user/hello.txt": b"line1\nline2\nline3\n",
        }
    )


@pytest.mark.asyncio
async def test_cat_basic(fs: MockFilesystem) -> None:
    interp = VfsShellInterpreter(fs, fs.cwd(), cols=80, rows=24)
    output: list[str] = []
    exit_code = await interp.execute("cat hello.txt", output.append, output.append)
    assert exit_code == 0
    text = "".join(output)
    assert "line1" in text
    assert "line3" in text


@pytest.mark.asyncio
async def test_cat_line_numbers(fs: MockFilesystem) -> None:
    interp = VfsShellInterpreter(fs, fs.cwd(), cols=80, rows=24)
    output: list[str] = []
    exit_code = await interp.execute("cat -n hello.txt", output.append, output.append)
    assert exit_code == 0
    text = "".join(output)
    assert "1" in text  # line number


@pytest.mark.asyncio
async def test_cat_nonexistent(fs: MockFilesystem) -> None:
    interp = VfsShellInterpreter(fs, fs.cwd(), cols=80, rows=24)
    output: list[str] = []
    exit_code = await interp.execute("cat missing.txt", output.append, output.append)
    assert exit_code != 0
