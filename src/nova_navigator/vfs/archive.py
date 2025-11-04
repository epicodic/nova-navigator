from __future__ import annotations

from ..archive import Archive, open_archive
from .filesystem import Filesystem, PathStats, VFSPath
from .local import LocalFilesystem


class ArchiveFilesystem(Filesystem):
    # the parent path where the archive is located in
    _archive_parent: VFSPath

    _archive: Archive

    def __init__(self, archive_parent: VFSPath, archive: Archive | VFSPath) -> None:
        self._archive_parent = archive_parent

        if isinstance(archive, Archive):
            self._archive = archive
        else:
            # TODO: temporary, later we need to support generic VFS paths, e.g. by downloading the archive first
            assert isinstance(archive.filesystem, LocalFilesystem)
            self._archive = open_archive(archive.path, mode="r")

    def cwd(self) -> VFSPath:
        return self.root()

    def root(self) -> VFSPath:
        return VFSPath("/", self)

    def home(self) -> VFSPath:
        return self.root()

    def iterdir(self, path: VFSPath) -> list[VFSPath]:
        path_list: list[VFSPath] = []
        for entry in self._archive.listdir(path.path):
            a = VFSPath(path.path / entry, self)
            path_list.append(a)
        return path_list

    def parent(self, path: VFSPath) -> VFSPath:
        if path.path.parent == path.path:
            return self._archive_parent

        return VFSPath(path.path.parent, self)

    def stat(self, path: VFSPath) -> PathStats:
        return self._archive.stats(path.path)

    def __eq__(self, value: object) -> bool:
        return (
            isinstance(value, ArchiveFilesystem)
            and self._archive == value._archive
            and self._archive_parent == value._archive_parent
        )

    def __hash__(self) -> int:
        return hash((self._archive, self._archive_parent))
