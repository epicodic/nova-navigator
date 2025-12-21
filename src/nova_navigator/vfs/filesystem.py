from __future__ import annotations

import mimetypes
import os
from abc import abstractmethod
from pathlib import PurePosixPath

from ..path_stats import PathStats


class Filesystem:
    @abstractmethod
    def cwd(self) -> VPath:
        pass

    @abstractmethod
    def root(self) -> VPath:
        pass

    @abstractmethod
    def home(self) -> VPath:
        pass

    @abstractmethod
    def iterdir(self, path: VPath) -> list[VPath]:
        pass

    @abstractmethod
    def stat(self, path: VPath) -> PathStats:
        """Return the stats associated with the path."""

    @abstractmethod
    def parent(self, path: VPath) -> VPath:
        pass


class VPath:
    """A class representing a path in the virtual file system."""

    _filesystem: Filesystem
    _path: PurePosixPath
    _stat: PathStats | None = None

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

    def iterdir(self) -> list[VPath]:
        """Return an iterator of the files and directories in the directory."""
        return self._filesystem.iterdir(self)

    @property
    def stats(self) -> PathStats:
        """Return the stats associated with this path."""
        self._ensure_stat()
        assert self._stat is not None
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

    def guess_mimetype(self) -> str | None:
        mimetype, _ = mimetypes.guess_type(self._path.as_posix())
        return mimetype

    def joinpath(self, *other: os.PathLike[str] | str) -> VPath:
        return VPath(self._path.joinpath(*other), self._filesystem)

    def __str__(self) -> str:
        return str(self._path)

    def __truediv__(self, other: str | os.PathLike[str]) -> VPath:
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
