from __future__ import annotations

from typing import override

from ...archive import Archive, open_archive
from ..filesystem import Filesystem, Stat, StreamReaderLike, StreamWriterLike
from ..vpath import VPath
from .local import LocalFilesystem


class ArchiveFilesystem(Filesystem):
    """Read-only filesystem backed by a tar or zip archive.

    Pass either a pre-opened :class:`~nova_navigator.archive.Archive` or a
    :class:`~nova_navigator.vfs.vpath.VPath` pointing to an archive file on
    the local filesystem.  *archive_parent* is the :class:`VPath` returned by
    :meth:`parent` when the caller asks for the parent of the archive root —
    i.e. the directory that contains the archive file itself.
    """

    _archive_parent: VPath
    _archive: Archive

    def __init__(self, archive_parent: VPath, archive: Archive | VPath) -> None:
        self._archive_parent = archive_parent
        if isinstance(archive, Archive):
            self._archive = archive
        else:
            assert isinstance(archive.filesystem, LocalFilesystem)
            self._archive = open_archive(archive.path, mode="r")

    @override
    def cwd(self) -> VPath:
        return self.root()

    @override
    def root(self) -> VPath:
        return VPath("/", self)

    @override
    def home(self) -> VPath:
        return self.root()

    @override
    def iterdir(self, path: VPath) -> list[VPath]:
        self._assert_vpath(path)
        return [VPath(path.path / entry, self) for entry in self._archive.listdir(path.path)]

    @override
    def parent(self, path: VPath) -> VPath:
        self._assert_vpath(path)
        if path.path.parent == path.path:
            return self._archive_parent
        return VPath(path.path.parent, self)

    @override
    def stat(self, path: VPath) -> Stat:
        self._assert_vpath(path)
        return self._archive.stats(path.path)

    @override
    def is_same_device(self, path1: VPath, path2: VPath) -> bool:
        self._assert_vpath(path1)
        return path1.filesystem == path2.filesystem

    @override
    def read(self, path: VPath) -> StreamReaderLike:
        raise NotImplementedError("ArchiveFilesystem does not support read()")

    @override
    def write(self, path: VPath) -> StreamWriterLike:
        raise NotImplementedError("ArchiveFilesystem is read-only")

    @override
    def remove(self, path: VPath) -> None:
        raise NotImplementedError("ArchiveFilesystem is read-only")

    @override
    def rename(self, src_path: VPath, dst_path: VPath) -> None:
        raise NotImplementedError("ArchiveFilesystem is read-only")

    @override
    def rmdir(self, path: VPath) -> None:
        raise NotImplementedError("ArchiveFilesystem is read-only")

    @override
    def mkdir(self, path: VPath) -> None:
        raise NotImplementedError("ArchiveFilesystem is read-only")

    @override
    def copy_stat(self, path: VPath, stat: Stat) -> None:
        raise NotImplementedError("ArchiveFilesystem is read-only")

    @override
    def refresh(self, path: VPath | None = None) -> None:
        pass  # no caching in ArchiveFilesystem

    def __eq__(self, value: object) -> bool:
        return (
            isinstance(value, ArchiveFilesystem)
            and self._archive == value._archive
            and self._archive_parent == value._archive_parent
        )

    def __hash__(self) -> int:
        return hash((self._archive, self._archive_parent))
