"""Tests for cp command."""

from __future__ import annotations

import pytest

from nova_navigator.terminal.vfs_shell.interpreter import VfsShellInterpreter
from tests._utils.mock_filesystem import MockFilesystem


@pytest.fixture
def fs() -> MockFilesystem:
    return MockFilesystem(
        {
            "/home/user/src.txt": b"source content",
            "/home/user/dir/a.txt": b"a content",
            "/home/user/dest": None,
        }
    )


@pytest.mark.asyncio
async def test_cp_file(fs: MockFilesystem) -> None:
    interp = VfsShellInterpreter(fs, fs.cwd(), cols=80, rows=24)
    output: list[str] = []
    exit_code = await interp.execute("cp src.txt copy.txt", output.append, output.append)
    assert exit_code == 0
    assert fs.exists("/home/user/copy.txt")


@pytest.mark.asyncio
async def test_cp_recursive_directory(fs: MockFilesystem) -> None:
    interp = VfsShellInterpreter(fs, fs.cwd(), cols=80, rows=24)
    output: list[str] = []
    exit_code = await interp.execute("cp -r dir dest/dir_copy", output.append, output.append)
    assert exit_code == 0
    assert fs.exists("/home/user/dest/dir_copy")
    assert fs.exists("/home/user/dest/dir_copy/a.txt")


@pytest.mark.asyncio
async def test_cp_directory_without_r_fails(fs: MockFilesystem) -> None:
    interp = VfsShellInterpreter(fs, fs.cwd(), cols=80, rows=24)
    output: list[str] = []
    exit_code = await interp.execute("cp dir dest/copy", output.append, output.append)
    assert exit_code != 0
