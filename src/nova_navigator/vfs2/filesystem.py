from __future__ import annotations

from abc import ABC, abstractmethod
from pathlib import PurePath
from typing import Protocol

from .types import Stat
from .vpath import VPath


class StreamReaderLike(Protocol):
    def read(self, size: int) -> bytes: ...
    def close(self) -> None: ...


class StreamWriterLike(Protocol):
    def write(self, data: bytes) -> int: ...
    def close(self) -> None: ...


class Filesystem(ABC):
    """Base class representing a virtual file system."""

    def _assert_vpath(self, path: VPath) -> None:
        if path.filesystem != self:
            raise ValueError(f"VPath {path} does not belong to filesystem {self}")

    def path(self, p: str | PurePath) -> VPath:
        return VPath(str(p), self)

    @abstractmethod
    def cwd(self) -> VPath:
        """Return the current working directory path."""

    @abstractmethod
    def root(self) -> VPath:
        """Return the root directory path."""

    @abstractmethod
    def home(self) -> VPath:
        """Return the home directory path."""

    @abstractmethod
    def iterdir(self, path: VPath) -> list[VPath]:
        """Return the list of files and directories in the given directory path."""

    @abstractmethod
    def stat(self, path: VPath) -> Stat:
        """Return the stats associated with the path."""

    @abstractmethod
    def parent(self, path: VPath) -> VPath:
        """Return the parent path of the given path."""

    @abstractmethod
    def read(self, path: VPath) -> StreamReaderLike:
        """Return a stream reader for the given path."""

    @abstractmethod
    def write(self, path: VPath) -> StreamWriterLike:
        """Return a stream writer for the given path."""

    @abstractmethod
    def remove(self, path: VPath) -> None:
        """Remove the file at the given path."""

    @abstractmethod
    def rmdir(self, path: VPath) -> None:
        """Remove the directory at the given path (must be empty)."""
