from pathlib import PurePath
from typing import override

from nova_navigator.vfs.path import PathStats

from ..archive import Archive, open_archive
from .local import LocalPath
from .path import VFSPath


class ArchivePath(VFSPath):
    """A class representing a path inside an archive file."""

    _path: PurePath
    _archive_parent: VFSPath
    _archive: Archive

    def __init__(self, path: PurePath | str, archive_parent: VFSPath, archive: Archive | VFSPath) -> None:
        self._path = PurePath(path)
        self._archive_parent = archive_parent

        if isinstance(archive, Archive):
            self._archive = archive
        else:
            # TODO: temporary, later we need to support generic VFS paths, e.g. by downloading the archive first
            assert isinstance(archive, LocalPath)
            self._archive = open_archive(archive.path, mode="r")

    def __eq__(self, other: object) -> bool:
        if not isinstance(other, ArchivePath):
            return False
        return self._path == other._path and self._archive == other._archive

    def __hash__(self) -> int:
        return hash((self._path, self._archive))

    def is_directory(self) -> bool:
        # stats = self._archive.stat(self._path)
        # return S_ISDIR(stats.st_mode)
        return True

    def is_archive_root(self) -> bool:
        return self._path == self._path.parent

    @property
    @override
    def stats(self) -> PathStats:
        return self._archive.stats(self._path)

    @override
    def iterdir(self) -> list[VFSPath]:
        path_list: list[VFSPath] = []
        for entry in self._archive.listdir(self._path):
            a = ArchivePath(
                path=self._path / entry,
                archive_parent=self._archive_parent,
                archive=self._archive,
            )
            path_list.append(a)
        return path_list
        # return [
        #     ArchivePath(
        #         self / entry,
        #         archive_root=self._archive_root,
        #         archive=self._archive,
        #     )
        #     for entry in self._archive.listdir(self)
        # ]

    @property
    @override
    def name(self) -> str:
        return self._path.name

    @property
    @override
    def parent(self) -> VFSPath:
        if self.is_archive_root():
            return self._archive_parent

        return ArchivePath(
            self._path.parent,
            archive_parent=self._archive_parent,
            archive=self._archive,
        )

    @property
    @override
    def compact_path_str(self) -> str:
        # if path is inside home directory, return relative to home
        return "archive:" + str(self._path)
