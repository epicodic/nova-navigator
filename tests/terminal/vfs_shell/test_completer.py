"""Tests for tab completion."""

from __future__ import annotations

import pytest

from nova_navigator.terminal.vfs_shell.aliases import DEFAULT_ALIASES, AliasStore
from nova_navigator.terminal.vfs_shell.commands import register_all
from nova_navigator.terminal.vfs_shell.completer import TabCompleter
from nova_navigator.terminal.vfs_shell.registry import CommandRegistry
from tests._utils.mock_filesystem import MockFilesystem


@pytest.fixture
def fs() -> MockFilesystem:
    return MockFilesystem(
        {
            "/home/user/readme.md": b"hello",
            "/home/user/readme.txt": b"hi",
            "/home/user/setup.py": b"x",
            "/home/user/.hidden": b"secret",
            "/home/user/subdir/file.txt": b"content",
        }
    )


@pytest.fixture
def completer(fs: MockFilesystem) -> TabCompleter:
    registry = CommandRegistry()
    alias_store = AliasStore(DEFAULT_ALIASES)
    register_all(registry, alias_store)
    return TabCompleter(registry, fs, fs.cwd, alias_store=alias_store)


@pytest.mark.asyncio
async def test_complete_command_prefix(completer: TabCompleter) -> None:
    candidates = await completer.complete("l", 1)
    assert "ls" in candidates


@pytest.mark.asyncio
async def test_complete_command_empty(completer: TabCompleter) -> None:
    candidates = await completer.complete("", 0)
    # All commands should appear
    assert "ls" in candidates
    assert "cd" in candidates
    assert "cat" in candidates


@pytest.mark.asyncio
async def test_complete_path(completer: TabCompleter) -> None:
    candidates = await completer.complete("cat read", 8)
    assert "readme.md" in candidates
    assert "readme.txt" in candidates


@pytest.mark.asyncio
async def test_complete_path_hidden_excluded(completer: TabCompleter) -> None:
    candidates = await completer.complete("cat ", 4)
    assert ".hidden" not in candidates


@pytest.mark.asyncio
async def test_complete_path_hidden_included_with_dot(completer: TabCompleter) -> None:
    candidates = await completer.complete("cat .", 5)
    assert ".hidden" in candidates


@pytest.mark.asyncio
async def test_complete_directory_gets_trailing_slash(completer: TabCompleter) -> None:
    candidates = await completer.complete("ls sub", 6)
    assert "subdir/" in candidates


@pytest.mark.asyncio
async def test_word_boundaries(completer: TabCompleter) -> None:
    start, end = completer.word_boundaries("cat read", 8)
    assert start == 4
    assert end == 8
