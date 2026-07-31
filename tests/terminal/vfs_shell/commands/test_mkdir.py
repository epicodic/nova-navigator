"""Tests for mkdir command."""

from __future__ import annotations

import pytest

from nova_navigator.terminal.vfs_shell.interpreter import VfsShellInterpreter
from tests._utils.mock_filesystem import MockFilesystem


@pytest.fixture
def fs() -> MockFilesystem:
    return MockFilesystem(
        {
            "/home/user/existing": None,
        }
    )


@pytest.mark.asyncio
async def test_mkdir_basic(fs: MockFilesystem) -> None:
    interp = VfsShellInterpreter(fs, fs.cwd(), cols=80, rows=24)
    output: list[str] = []
    exit_code = await interp.execute("mkdir newdir", output.append, output.append)
    assert exit_code == 0
    assert fs.exists("/home/user/newdir")


@pytest.mark.asyncio
async def test_mkdir_parents(fs: MockFilesystem) -> None:
    interp = VfsShellInterpreter(fs, fs.cwd(), cols=80, rows=24)
    output: list[str] = []
    exit_code = await interp.execute("mkdir -p a/b/c", output.append, output.append)
    assert exit_code == 0
    assert fs.exists("/home/user/a")
    assert fs.exists("/home/user/a/b")
    assert fs.exists("/home/user/a/b/c")


@pytest.mark.asyncio
async def test_mkdir_existing_fails(fs: MockFilesystem) -> None:
    interp = VfsShellInterpreter(fs, fs.cwd(), cols=80, rows=24)
    output: list[str] = []
    exit_code = await interp.execute("mkdir existing", output.append, output.append)
    assert exit_code != 0


@pytest.mark.asyncio
async def test_mkdir_p_existing_ok(fs: MockFilesystem) -> None:
    interp = VfsShellInterpreter(fs, fs.cwd(), cols=80, rows=24)
    output: list[str] = []
    exit_code = await interp.execute("mkdir -p existing", output.append, output.append)
    assert exit_code == 0
