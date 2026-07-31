"""Tests for VFS glob expansion."""

from __future__ import annotations

import pytest

from nova_navigator.terminal.vfs_shell.glob import expand_globs
from nova_navigator.terminal.vfs_shell.tokenizer import Token
from tests._utils.mock_filesystem import MockFilesystem


@pytest.fixture
def fs() -> MockFilesystem:
    return MockFilesystem(
        {
            "/home/user/readme.md": b"",
            "/home/user/setup.py": b"",
            "/home/user/test.py": b"",
            "/home/user/data.csv": b"",
            "/home/user/sub/file.py": b"",
            "/home/user/.hidden": b"",
        }
    )


@pytest.mark.asyncio
async def test_star_glob(fs: MockFilesystem) -> None:
    tokens = [Token("*.py", False)]
    result = await expand_globs(tokens, fs, fs.cwd())
    assert sorted(result) == ["setup.py", "test.py"]


@pytest.mark.asyncio
async def test_question_mark_glob(fs: MockFilesystem) -> None:
    tokens = [Token("????.py", False)]
    result = await expand_globs(tokens, fs, fs.cwd())
    assert result == ["test.py"]


@pytest.mark.asyncio
async def test_quoted_token_not_expanded(fs: MockFilesystem) -> None:
    tokens = [Token("*.py", True)]
    result = await expand_globs(tokens, fs, fs.cwd())
    assert result == ["*.py"]


@pytest.mark.asyncio
async def test_no_match_keeps_literal(fs: MockFilesystem) -> None:
    tokens = [Token("*.xyz", False)]
    result = await expand_globs(tokens, fs, fs.cwd())
    assert result == ["*.xyz"]


@pytest.mark.asyncio
async def test_no_wildcards_passes_through(fs: MockFilesystem) -> None:
    tokens = [Token("readme.md", False)]
    result = await expand_globs(tokens, fs, fs.cwd())
    assert result == ["readme.md"]


@pytest.mark.asyncio
async def test_character_class(fs: MockFilesystem) -> None:
    tokens = [Token("[st]*.py", False)]
    result = await expand_globs(tokens, fs, fs.cwd())
    assert sorted(result) == ["setup.py", "test.py"]


@pytest.mark.asyncio
async def test_absolute_path_glob(fs: MockFilesystem) -> None:
    tokens = [Token("/home/user/*.py", False)]
    result = await expand_globs(tokens, fs, fs.cwd())
    assert sorted(result) == ["/home/user/setup.py", "/home/user/test.py"]


@pytest.mark.asyncio
async def test_relative_dir_glob(fs: MockFilesystem) -> None:
    tokens = [Token("sub/*.py", False)]
    result = await expand_globs(tokens, fs, fs.cwd())
    assert result == ["sub/file.py"]
