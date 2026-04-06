from __future__ import annotations

import os
import stat as stat_mod
from io import BufferedReader, BufferedWriter
from stat import S_ISDIR, S_ISLNK
from typing import Any, override

from ..filesystem import Filesystem, Stat, StreamReaderLike, StreamWriterLike
from ..vpath import VPath


class LocalFilesystem(Filesystem):
    """Filesystem implementation for the local operating-system filesystem.

    A singleton (:meth:`singleton`) is provided for the common case where a
    single process-wide instance is sufficient.
    """

    _singleton: LocalFilesystem | None = None

    @staticmethod
    def singleton() -> LocalFilesystem:
        """Return the process-wide singleton :class:`LocalFilesystem` instance."""
        if LocalFilesystem._singleton is None:
            LocalFilesystem._singleton = LocalFilesystem()
        return LocalFilesystem._singleton

    def __eq__(self, value: object) -> bool:
        return isinstance(value, LocalFilesystem)

    def __hash__(self) -> int:
        return hash("LocalFilesystem")

    def __repr__(self) -> str:
        return f"{self.__class__.__name__}"

    @override
    def is_same_device(self, path1: VPath, path2: VPath) -> bool:
        self._assert_vpath(path1)
        if not isinstance(path2.filesystem, LocalFilesystem):
            return False
        # Use os.stat to get device IDs and compare them
        try:
            stat1 = os.stat(path1.path)
            stat2 = os.stat(path2.path)
            return stat1.st_dev == stat2.st_dev
        except (FileNotFoundError, OSError):
            return False

    @override
    def cwd(self) -> VPath:
        return VPath(os.getcwd(), self)

    @override
    def root(self) -> VPath:
        return VPath("/", self)

    @override
    def home(self) -> VPath:
        return VPath(os.path.expanduser("~"), self)

    @override
    def iterdir(self, path: VPath) -> list[VPath]:
        self._assert_vpath(path)
        return [path / name for name in os.listdir(path.path)]

    @override
    def parent(self, path: VPath) -> VPath:
        self._assert_vpath(path)
        return VPath(path.path.parent, self)

    @override
    def stat(self, path: VPath) -> Stat:
        self._assert_vpath(path)
        lstat = os.stat(path, follow_symlinks=False)

        try:
            stat = os.stat(path, follow_symlinks=True)
            is_broken_symlink = False
        except FileNotFoundError:
            stat = lstat
            is_broken_symlink = True

        if os.name != "nt":
            is_hidden = path.name.startswith(".")
        else:
            is_hidden = stat.st_file_attributes & 0x2  # = FILE_ATTRIBUTE_HIDDEN

        return Stat(
            size=stat.st_size,
            modified=stat.st_mtime,
            mode=stat_mod.S_IMODE(lstat.st_mode),
            is_hidden=is_hidden,
            is_directory=S_ISDIR(stat.st_mode),
            is_executable=stat.st_mode & 0o111 != 0,
            is_symlink=S_ISLNK(lstat.st_mode),
            is_broken_symlink=is_broken_symlink,
        )

    @override
    def read(self, path: VPath) -> StreamReaderLike:
        self._assert_vpath(path)

        class StreamReaderWrapper:
            def __init__(self, f: Any) -> None:
                assert isinstance(f, BufferedReader)
                self._f = f

            def read(self, size: int) -> bytes:
                return self._f.read(size)

            def close(self) -> None:
                self._f.close()

        return StreamReaderWrapper(open(path.path, mode="rb"))

    @override
    def write(self, path: VPath) -> StreamWriterLike:
        self._assert_vpath(path)

        class StreamWriterWrapper:
            def __init__(self, f: Any) -> None:
                assert isinstance(f, BufferedWriter)
                self._f = f

            def write(self, data: bytes) -> int:
                return self._f.write(data)

            def close(self) -> None:
                self._f.close()

        os.makedirs(path.path.parent, exist_ok=True)
        return StreamWriterWrapper(open(path.path, "wb"))

    @override
    def remove(self, path: VPath) -> None:
        self._assert_vpath(path)
        os.remove(path.path)

    @override
    def rename(self, src_path: VPath, dst_path: VPath) -> None:
        self._assert_vpath(src_path)
        self._assert_vpath(dst_path)
        os.rename(src_path.path, dst_path.path)

    @override
    def rmdir(self, path: VPath) -> None:
        self._assert_vpath(path)
        os.rmdir(path.path)

    @override
    def mkdir(self, path: VPath) -> None:
        self._assert_vpath(path)
        os.mkdir(path.path)

    @override
    def copy_stat(self, path: VPath, src_stat: Stat) -> None:
        self._assert_vpath(path)
        p = path.path
        if src_stat.modified >= 0:
            os.utime(p, (src_stat.modified, src_stat.modified), follow_symlinks=False)
        if src_stat.mode >= 0:
            os.chmod(p, src_stat.mode, follow_symlinks=False)

    @override
    def readlink(self, path: VPath) -> str:
        self._assert_vpath(path)
        return os.readlink(path.path)
