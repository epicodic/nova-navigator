"""Tests for rm command."""

from __future__ import annotations

import pytest

from nova_navigator.terminal.vfs_shell.interpreter import VfsShellInterpreter
from tests._utils.mock_filesystem import MockFilesystem


@pytest.fixture
def fs() -> MockFilesystem:
    return MockFilesystem(
        {
            "/home/user/file.txt": b"content",
            "/home/user/dir/a.txt": b"a",
            "/home/user/dir/b.txt": b"b",
            "/home/user/dir/sub/c.txt": b"c",
        }
    )


@pytest.mark.asyncio
async def test_rm_file(fs: MockFilesystem) -> None:
    interp = VfsShellInterpreter(fs, fs.cwd(), cols=80, rows=24)
    output: list[str] = []
    exit_code = await interp.execute("rm file.txt", output.append, output.append)
    assert exit_code == 0
    assert not fs.exists("/home/user/file.txt")


@pytest.mark.asyncio
async def test_rm_directory_without_r_fails(fs: MockFilesystem) -> None:
    interp = VfsShellInterpreter(fs, fs.cwd(), cols=80, rows=24)
    output: list[str] = []
    exit_code = await interp.execute("rm dir", output.append, output.append)
    assert exit_code != 0
    assert fs.exists("/home/user/dir")


@pytest.mark.asyncio
async def test_rm_rf_directory(fs: MockFilesystem) -> None:
    interp = VfsShellInterpreter(fs, fs.cwd(), cols=80, rows=24)
    output: list[str] = []
    exit_code = await interp.execute("rm -rf dir", output.append, output.append)
    assert exit_code == 0
    assert not fs.exists("/home/user/dir")
    assert not fs.exists("/home/user/dir/a.txt")
    assert not fs.exists("/home/user/dir/sub/c.txt")


@pytest.mark.asyncio
async def test_rm_nonexistent_without_force(fs: MockFilesystem) -> None:
    interp = VfsShellInterpreter(fs, fs.cwd(), cols=80, rows=24)
    output: list[str] = []
    exit_code = await interp.execute("rm nonexistent", output.append, output.append)
    assert exit_code != 0


@pytest.mark.asyncio
async def test_rm_nonexistent_with_force(fs: MockFilesystem) -> None:
    interp = VfsShellInterpreter(fs, fs.cwd(), cols=80, rows=24)
    output: list[str] = []
    exit_code = await interp.execute("rm -f nonexistent", output.append, output.append)
    assert exit_code == 0
