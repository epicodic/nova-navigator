"""Read-only archive holding the decompressed content of a single ``.gz``, ``.bz2`` or ``.xz`` file."""

import bz2
import errno
import gzip
import io
import lzma
import zlib
from collections.abc import Callable
from pathlib import Path, PurePath
from typing import IO, override

from ..vfs.filesystem import StreamReaderLike
from ..vfs.types import Stat
from .archive import Archive, _ArchiveReader

_CHUNK_SIZE = 1024 * 1024
_FALLBACK_ENTRY_NAME = "data"


def _open_gzip(path: Path) -> IO[bytes]:
    return io.BufferedReader(gzip.GzipFile(path, "rb"))


def _open_bzip2(path: Path) -> IO[bytes]:
    return bz2.open(path, "rb")


def _open_xz(path: Path) -> IO[bytes]:
    return lzma.open(path, "rb")


# suffix -> (format name for messages, opener)
_FORMATS: dict[str, tuple[str, Callable[[Path], IO[bytes]]]] = {
    ".gz": ("gzip", _open_gzip),
    ".bz2": ("bzip2", _open_bzip2),
    ".xz": ("xz", _open_xz),
}


class CompressedFileArchive(Archive):
    """Read-only archive with one entry: the decompressed content of a ``.gz``, ``.bz2`` or ``.xz`` file.

    The entry is named like the compressed file without the suffix (``data`` if nothing remains).
    The constructor streams the whole file once to prove its integrity and to measure the uncompressed size;
    reads afterwards decompress incrementally from a fresh handle each.
    """

    def __init__(self, archive_path: PurePath, mode: Archive.Mode) -> None:
        if mode != "r":
            raise PermissionError(errno.EROFS, "Compressed files are read-only", str(archive_path))
        super().__init__(archive_path, mode)
        name = archive_path.name
        suffix = next(s for s in _FORMATS if name.endswith(s))
        self._format_name, self._opener = _FORMATS[suffix]
        self._entry_name = name.removesuffix(suffix) or _FALLBACK_ENTRY_NAME
        self._local_path = Path(archive_path)
        self._modified = self._local_path.stat().st_mtime
        self._size = self._measure()

    def _measure(self) -> int:
        """Stream the whole file once, returning the uncompressed size; raise ``OSError`` if it is corrupt or empty."""
        total = 0
        try:
            if self._local_path.stat().st_size == 0:
                raise EOFError("empty file")
            with self._opener(self._local_path) as stream:
                while chunk := stream.read(_CHUNK_SIZE):
                    total += len(chunk)
        except (gzip.BadGzipFile, EOFError, lzma.LZMAError, zlib.error, OSError) as error:
            if isinstance(error, (FileNotFoundError, PermissionError)):
                raise
            raise OSError(errno.EINVAL, f"Corrupt or empty {self._format_name} file", str(self._archive_path)) from error
        return total

    @staticmethod
    def _is_root(path: PurePath) -> bool:
        return path == path.parent

    def _is_entry(self, path: PurePath) -> bool:
        return path.as_posix().lstrip("/") == self._entry_name

    def _not_found(self, path: PurePath) -> FileNotFoundError:
        return FileNotFoundError(f"Path '{path}', {path.name} not found in archive '{self._archive_path}'")

    @override
    def listdir(self, path: PurePath) -> list[PurePath]:
        if self._is_root(path):
            return [PurePath(self._entry_name)]
        return []

    @override
    def stats(self, path: PurePath) -> Stat:
        if self._is_root(path):
            return Stat(
                size=0,
                modified=0,
                is_hidden=False,
                is_directory=True,
                is_executable=False,
                is_symlink=False,
            )
        if not self._is_entry(path):
            raise self._not_found(path)
        return Stat(
            size=self._size,
            modified=self._modified,
            is_hidden=self._entry_name.startswith("."),
            is_directory=False,
            is_executable=False,
            is_symlink=False,
        )

    @override
    def read(self, path: PurePath) -> StreamReaderLike:
        if self._is_root(path):
            raise IsADirectoryError(path)
        if not self._is_entry(path):
            raise self._not_found(path)
        return _ArchiveReader(self._opener(self._local_path))

    @override
    def close(self) -> None:
        """Nothing to release: every read opens its own handle."""
