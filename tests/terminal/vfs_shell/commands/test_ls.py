"""Tests for ls command."""

from __future__ import annotations

import pytest

from nova_navigator.terminal.vfs_shell.interpreter import VfsShellInterpreter
from tests._utils.mock_filesystem import MockFilesystem


@pytest.fixture
def fs() -> MockFilesystem:
    return MockFilesystem(
        {
            "/home/user/readme.md": b"hello world",
            "/home/user/setup.py": b"x" * 100,
            "/home/user/.hidden": b"secret",
            "/home/user/subdir/file.txt": b"content",
        }
    )


@pytest.mark.asyncio
async def test_ls_basic(fs: MockFilesystem) -> None:
    interp = VfsShellInterpreter(fs, fs.cwd(), cols=80, rows=24)
    output: list[str] = []
    exit_code = await interp.execute("ls", output.append, output.append)
    assert exit_code == 0
    text = "".join(output)
    assert "readme.md" in text
    assert "setup.py" in text
    assert "subdir" in text
    # Hidden files not shown by default
    assert ".hidden" not in text


@pytest.mark.asyncio
async def test_ls_all_shows_hidden(fs: MockFilesystem) -> None:
    interp = VfsShellInterpreter(fs, fs.cwd(), cols=80, rows=24)
    output: list[str] = []
    exit_code = await interp.execute("ls -a", output.append, output.append)
    assert exit_code == 0
    text = "".join(output)
    assert ".hidden" in text


@pytest.mark.asyncio
async def test_ls_long_format(fs: MockFilesystem) -> None:
    interp = VfsShellInterpreter(fs, fs.cwd(), cols=80, rows=24)
    output: list[str] = []
    exit_code = await interp.execute("ls -l", output.append, output.append)
    assert exit_code == 0
    text = "".join(output)
    # Long format includes size
    assert "100" in text  # size of setup.py


@pytest.mark.asyncio
async def test_ls_path_argument(fs: MockFilesystem) -> None:
    interp = VfsShellInterpreter(fs, fs.cwd(), cols=80, rows=24)
    output: list[str] = []
    exit_code = await interp.execute("ls subdir", output.append, output.append)
    assert exit_code == 0
    text = "".join(output)
    assert "file.txt" in text


@pytest.mark.asyncio
async def test_ls_nonexistent(fs: MockFilesystem) -> None:
    interp = VfsShellInterpreter(fs, fs.cwd(), cols=80, rows=24)
    output: list[str] = []
    exit_code = await interp.execute("ls /nonexistent", output.append, output.append)
    assert exit_code != 0
