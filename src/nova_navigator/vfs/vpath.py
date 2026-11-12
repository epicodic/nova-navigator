from __future__ import annotations

import asyncio
import mimetypes
import os
import threading
from collections.abc import AsyncIterator
from pathlib import PurePosixPath
from typing import TYPE_CHECKING

from .types import Stat

if TYPE_CHECKING:
    from .filesystem import Filesystem


class VPath:
    """A class representing a path in the virtual file system."""

    _filesystem: Filesystem
    _path: PurePosixPath
    _stat: Stat | None = None

    def __init__(self, path: os.PathLike[str] | str, filesystem: Filesystem) -> None:
        self._path = PurePosixPath(path)
        self._filesystem = filesystem

    def _ensure_stat(self) -> None:
        if self._stat is not None:
            return
        self._stat = self._filesystem.stat(self)

    @property
    def filesystem(self) -> Filesystem:
        return self._filesystem

    async def iterdir(
        self,
        cancel: threading.Event | None = None,
    ) -> AsyncIterator[VPath]:
        """Yield immediate children of this directory with stat pre-populated."""
        async for vp in self._filesystem.iterdir(self, cancel=cancel):
            yield vp

    async def walk(
        self,
        cancel: threading.Event | None = None,
    ) -> AsyncIterator[tuple[VPath, list[VPath], list[VPath], list[VPath]]]:
        """Like os.walk, but async. Yields (root, dirs, files, links) tuples.

        A child that is a symbolic link (``stat.is_symlink``) goes to *links*, whatever it
        points to (file, directory, broken, loop); it is never followed or descended into,
        callers decide per operation. Only real directories are recursed into.
        Each VPath in dirs, files and links has _stat pre-populated from iterdir.

        Raises:
            NotADirectoryError: if this path is not a directory or is itself a symbolic link.
        """
        root_stat = self.stat
        if not root_stat.is_directory or root_stat.is_symlink:
            raise NotADirectoryError(f"{self} is not a directory")

        dirs: list[VPath] = []
        files: list[VPath] = []
        links: list[VPath] = []
        async for child in self._filesystem.iterdir(self, cancel=cancel):
            child_stat = child.stat
            if child_stat.is_symlink:
                links.append(child)
            elif child_stat.is_directory:
                dirs.append(child)
            else:
                files.append(child)

        yield self, dirs, files, links

        for d in dirs:
            async for item in d.walk(cancel=cancel):
                yield item

    @property
    def stat(self) -> Stat:
        """Return the stat associated with this path."""
        self._ensure_stat()
        assert self._stat is not None
        return self._stat

    @property
    def cached_stat(self) -> Stat | None:
        """Return the already cached stat, or ``None`` if there is none. Never performs I/O."""
        return self._stat

    def with_stat(self, stat: Stat) -> VPath:
        """Return a new VPath at the same path and filesystem with *stat* cached; does no I/O."""
        view = VPath(self._path, self._filesystem)
        view._stat = stat
        return view

    @property
    def stat_or_none(self) -> Stat | None:
        """Return the stat for this path, or ``None`` if the path does not exist."""
        try:
            self._ensure_stat()
            return self._stat
        except FileNotFoundError:
            return None

    async def fresh_stat(self) -> Stat:
        """Fetch a fresh stat from the filesystem and update the cache."""
        self._stat = await asyncio.to_thread(self._filesystem.stat, self)
        return self._stat

    @property
    def path(self) -> PurePosixPath:
        return self._path

    @property
    def name(self) -> str:
        return self._path.name

    @property
    def parent(self) -> VPath:
        return self._filesystem.parent(self)

    @property
    def compact_path_str(self) -> str:
        # if path is inside home directory, return relative to home
        home = self._filesystem.home()
        try:
            rel_path = self._path.relative_to(home.path)
            return str("~" / rel_path)
        except ValueError:
            return str(self._path)

    @property
    def uri(self) -> str:
        """Full URI for this path, including scheme and authority where applicable.

        Returns ``ssh://user@host/path`` for SSH paths, ``azure://…`` for Azure,
        and a home-relative string (``~/…``) or absolute path for local paths.
        """
        return self._filesystem.uri_for_path(self._path)

    def guess_mimetype(self) -> str | None:
        """Return the MIME type guessed from the filename, or ``None`` if unknown."""
        mimetype, _ = mimetypes.guess_type(self._path.as_posix())
        return mimetype

    def joinpath(self, *other: os.PathLike[str] | str) -> VPath:
        """Return a new :class:`VPath` by appending path segments to this path."""
        return VPath(self._path.joinpath(*other), self._filesystem)

    def __str__(self) -> str:
        return str(self._path)

    def __repr__(self) -> str:
        return f"{self.__class__.__name__}({self.path.as_posix()!r}, {self._filesystem!r})"

    def __truediv__(self, other: str | os.PathLike[str]) -> VPath:
        """Return a new :class:`VPath` by appending *other* using the ``/`` operator."""
        return self.joinpath(other)

    def __eq__(self, other: object) -> bool:
        if not isinstance(other, VPath):
            return False
        return self._filesystem == other._filesystem and self._path == other._path

    def __hash__(self) -> int:
        return hash((self._filesystem, self._path))

    # PathLike protocol

    def __fspath__(self) -> str:
        return str(self)
