from __future__ import annotations

from ..archive import Archive, open_archive
from .filesystem import Filesystem, PathStats, VPath
from .local import LocalFilesystem


class ArchiveFilesystem(Filesystem):
    # the parent path where the archive is located in
    _archive_parent: VPath

    _archive: Archive

    def __init__(self, archive_parent: VPath, archive: Archive | VPath) -> None:
        self._archive_parent = archive_parent

        if isinstance(archive, Archive):
            self._archive = archive
        else:
            # TODO: temporary, later we need to support generic VFS paths, e.g. by downloading the archive first
            assert isinstance(archive.filesystem, LocalFilesystem)
            self._archive = open_archive(archive.path, mode="r")

    def cwd(self) -> VPath:
        return self.root()

    def root(self) -> VPath:
        return VPath("/", self)

    def home(self) -> VPath:
        return self.root()

    def iterdir(self, path: VPath) -> list[VPath]:
        path_list: list[VPath] = []
        for entry in self._archive.listdir(path.path):
            a = VPath(path.path / entry, self)
            path_list.append(a)
        return path_list

    def parent(self, path: VPath) -> VPath:
        if path.path.parent == path.path:
            return self._archive_parent

        return VPath(path.path.parent, self)

    def stat(self, path: VPath) -> PathStats:
        return self._archive.stats(path.path)

    def __eq__(self, value: object) -> bool:
        return (
            isinstance(value, ArchiveFilesystem)
            and self._archive == value._archive
            and self._archive_parent == value._archive_parent
        )

    def __hash__(self) -> int:
        return hash((self._archive, self._archive_parent))
