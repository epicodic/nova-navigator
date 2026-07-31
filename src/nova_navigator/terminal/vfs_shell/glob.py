"""Glob expansion for VFS shell, using fnmatch against VFS iterdir."""

from __future__ import annotations

import fnmatch
from pathlib import PurePosixPath

from nova_navigator.terminal.vfs_shell.tokenizer import Token
from nova_navigator.vfs.filesystem import Filesystem
from nova_navigator.vfs.vpath import VPath

_GLOB_CHARS = frozenset("*?[")


def _has_glob(s: str) -> bool:
    """Return True if s contains glob metacharacters."""
    return any(c in s for c in _GLOB_CHARS)


async def expand_globs(tokens: list[Token], filesystem: Filesystem, cwd: VPath) -> list[str]:
    """Expand glob patterns in unquoted tokens against the VFS.

    Quoted tokens pass through unchanged.
    Unquoted tokens without glob characters pass through unchanged.
    Unquoted tokens with glob characters are expanded via iterdir + fnmatch.
    If no matches are found, the literal pattern is kept (POSIX behavior).
    Multi-segment patterns (e.g. ``sub/*.py``) split on ``/`` and expand each level.
    """
    result: list[str] = []
    for token in tokens:
        if token.quoted or not _has_glob(token.value):
            result.append(token.value)
        else:
            expanded = await _expand_one(token.value, filesystem, cwd)
            if expanded:
                result.extend(sorted(expanded))
            else:
                result.append(token.value)
    return result


async def _expand_one(pattern: str, filesystem: Filesystem, cwd: VPath) -> list[str]:
    """Expand a single glob pattern against the filesystem."""
    path = PurePosixPath(pattern)
    parts = list(path.parts)

    if parts and parts[0] == "/":
        base = filesystem.root()
        parts = parts[1:]
        prefix = "/"
    else:
        base = cwd
        prefix = ""

    return await _expand_parts(parts, base, filesystem, prefix)


async def _expand_parts(
    parts: list[str],
    base: VPath,
    filesystem: Filesystem,
    prefix: str,
) -> list[str]:
    """Recursively expand path segments with glob patterns."""
    if not parts:
        return [prefix.rstrip("/") or "/"]

    segment = parts[0]
    remaining = parts[1:]

    if not _has_glob(segment):
        next_prefix = prefix + segment + ("/" if remaining else "")
        if remaining:
            next_path = filesystem.path(base.path / segment)
            return await _expand_parts(remaining, next_path, filesystem, next_prefix)
        return [prefix + segment]

    matches: list[str] = []
    try:
        async for entry in filesystem.iterdir(base):
            name = entry.name
            if fnmatch.fnmatch(name, segment):
                entry_prefix = prefix + name
                if remaining:
                    if entry.stat.is_directory:
                        sub = await _expand_parts(remaining, entry, filesystem, entry_prefix + "/")
                        matches.extend(sub)
                else:
                    matches.append(entry_prefix)
    except (FileNotFoundError, NotADirectoryError, OSError):
        pass

    return matches
