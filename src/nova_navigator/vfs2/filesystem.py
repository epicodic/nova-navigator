from __future__ import annotations

from abc import ABC, abstractmethod
from typing import IO, TYPE_CHECKING, Protocol

from .types import Stat

if TYPE_CHECKING:
    from .vpath import VPath


class StreamReaderLike(Protocol):
    def read(self, size: int) -> bytes: ...
    def close(self) -> None: ...


class StreamWriterLike(Protocol):
    def write(self, data: bytes) -> int: ...
    def close(self) -> None: ...


class Filesystem(ABC):
    """Base class representing a virtual file system."""

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
