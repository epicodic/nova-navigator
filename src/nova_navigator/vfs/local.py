from __future__ import annotations

import os
from stat import S_ISDIR, S_ISLNK

from .filesystem import Filesystem, PathStats, VFSPath


class LocalFilesystem(Filesystem):
    def cwd(self) -> VFSPath:
        return VFSPath(os.getcwd(), self)

    def root(self) -> VFSPath:
        return VFSPath("/", self)

    def home(self) -> VFSPath:
        return VFSPath(os.path.expanduser("~"), self)

    def iterdir(self, path: VFSPath) -> list[VFSPath]:
        return [path / name for name in os.listdir(path.path)]

    def parent(self, path: VFSPath) -> VFSPath:
        return VFSPath(path.path.parent, self)

    def stat(self, path: VFSPath) -> PathStats:
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

        return PathStats(
            size=stat.st_size,
            modified=stat.st_mtime,
            is_hidden=is_hidden,
            is_directory=S_ISDIR(stat.st_mode),
            is_executable=stat.st_mode & 0o111 != 0,
            is_symlink=S_ISLNK(lstat.st_mode),
            is_broken_symlink=is_broken_symlink,
        )

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
