from __future__ import annotations

import os
from io import FileIO, TextIOWrapper
from pathlib import PurePath
from stat import S_ISDIR, S_ISLNK
from typing import Any, override

from ..filesystem import Filesystem, Stat, StreamReaderLike, StreamWriterLike
from ..vpath import VPath


class LocalFilesystem(Filesystem):
    _singleton: LocalFilesystem | None = None

    @staticmethod
    def singleton() -> LocalFilesystem:
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
                assert isinstance(f, FileIO)
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
                assert isinstance(f, FileIO)
                self._f = f

            def write(self, data: bytes) -> int:
                return self._f.write(data)

            def close(self) -> None:
                self._f.close()

        return StreamWriterWrapper(open(path.path, "wb"))

    @override
    def remove(self, path: VPath) -> None:
        self._assert_vpath(path)
        os.remove(path.path)

    @override
    def rmdir(self, path: VPath) -> None:
        self._assert_vpath(path)
        os.rmdir(path.path)
