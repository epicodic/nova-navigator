"""Tests for ls command."""

from __future__ import annotations

import pytest

from nova_navigator.terminal.vfs_shell.commands.ls import LsCommand
from nova_navigator.terminal.vfs_shell.interpreter import VfsShellInterpreter
from nova_navigator.vfs.types import Stat
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
async def test_ls_long_format_file_kind_is_dot(fs: MockFilesystem) -> None:
    interp = VfsShellInterpreter(fs, fs.cwd(), cols=80, rows=24)
    output: list[str] = []
    await interp.execute("ls -l", output.append, output.append)
    text = "".join(output)
    lines = [line for line in text.split("\r\n") if "setup.py" in line]
    assert len(lines) == 1
    assert lines[0].startswith(". ")


@pytest.mark.asyncio
async def test_ls_long_format_dir_kind_and_size(fs: MockFilesystem) -> None:
    interp = VfsShellInterpreter(fs, fs.cwd(), cols=80, rows=24)
    output: list[str] = []
    await interp.execute("ls -l", output.append, output.append)
    text = "".join(output)
    lines = [line for line in text.split("\r\n") if "subdir" in line]
    assert len(lines) == 1
    assert lines[0].startswith("d ")
    assert "       -" in lines[0]


def test_format_long_file_kind_is_dot() -> None:
    stat = Stat(size=42, modified=1234567890.0, is_directory=False)
    line = LsCommand()._format_long("file.txt", stat, human=False)
    assert line.startswith(". ")


def test_format_long_dir_kind_is_d() -> None:
    stat = Stat(size=0, modified=1234567890.0, is_directory=True)
    line = LsCommand()._format_long("mydir", stat, human=False)
    assert line.startswith("d ")


def test_format_long_dir_size_is_dash() -> None:
    stat = Stat(size=0, modified=1234567890.0, is_directory=True)
    line = LsCommand()._format_long("mydir", stat, human=False)
    assert "-" in line.split()[1]


def test_format_long_columns_aligned_with_and_without_mtime() -> None:
    with_mtime = Stat(size=10, modified=1234567890.0, is_directory=False)
    without_mtime = Stat(size=10, modified=-1.0, is_directory=False)
    line_with = LsCommand()._format_long("a.txt", with_mtime, human=False)
    line_without = LsCommand()._format_long("b.txt", without_mtime, human=False)
    # The name must start at the same column in both cases.
    assert line_with.index("a.txt") == line_without.index("b.txt")


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
