from _thread import LockType
from abc import abstractmethod
from pathlib import PurePath
from typing import IO, Literal

from ..vfs.filesystem import StreamReaderLike
from ..vfs.types import Stat


class _ArchiveReader:
    def __init__(self, stream: IO[bytes], lock: LockType | None = None) -> None:
        self._stream = stream
        self._lock = lock

    def read(self, size: int) -> bytes:
        if self._lock is None:
            return self._stream.read(size)
        with self._lock:
            return self._stream.read(size)

    def close(self) -> None:
        if self._lock is None:
            self._stream.close()
        else:
            with self._lock:
                self._stream.close()


class Archive:
    """A class providing an abstraction for archive files."""

    Mode = Literal["r", "w", "a"]

    _archive_path: PurePath
    _mode: Mode

    def __init__(self, archive_path: PurePath, mode: Mode) -> None:
        self._archive_path = archive_path
        self._mode = mode

    @abstractmethod
    def listdir(self, path: PurePath) -> list[PurePath]:
        """List the contents of a directory inside the archive."""
        raise NotImplementedError

    @abstractmethod
    def stats(self, path: PurePath) -> Stat:
        """Get the stats of a file/directory inside the archive."""
        raise NotImplementedError

    @abstractmethod
    def read(self, path: PurePath) -> StreamReaderLike:
        """Return a binary stream for a file inside the archive."""
        raise NotImplementedError
