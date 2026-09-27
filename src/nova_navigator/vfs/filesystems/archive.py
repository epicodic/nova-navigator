from __future__ import annotations

import errno
import threading
from collections.abc import AsyncIterator
from pathlib import Path
from typing import override
from weakref import WeakValueDictionary

from ...archive import Archive, open_archive
from ..filesystem import Filesystem, FilesystemCapabilities, Stat, StreamReaderLike, StreamWriterLike
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

    def __init__(
        self,
        archive_parent: VPath,
        archive: Archive | VPath,
        *,
        source: VPath | None = None,
    ) -> None:
        self._archive_parent = archive_parent
        self._closed = False
        self._member_paths: WeakValueDictionary[int, VPath] = WeakValueDictionary()
        if isinstance(archive, Archive):
            self._archive = archive
        else:
            assert isinstance(archive.filesystem, LocalFilesystem)
            self._archive = open_archive(archive.path, mode="r")
        self._identity = self._archive
        self._local_path = Path(self._archive.archive_path)
        self._source = source if source is not None else VPath(self._local_path, LocalFilesystem.singleton())

    @property
    def source(self) -> VPath:
        """Original archive source, independent of member paths and local staging."""
        return self._source

    @property
    def local_path(self) -> Path:
        """Local archive bytes backing this mount."""
        return self._local_path

    def close(self) -> None:
        """Close the underlying archive reader."""
        if self._closed:
            return
        self._archive.close()
        self._closed = True

    def reload(self) -> None:
        """Reopen replaced archive bytes and invalidate cached member metadata."""
        if self._closed:
            raise ValueError("Archive mount is closed")
        self._archive.close()
        for member in self._member_paths.values():
            member._stat = None
        self._archive = open_archive(self._local_path, mode="r")

    @override
    def cwd(self) -> VPath:
        return self.root()

    @override
    def root(self) -> VPath:
        return VPath("/", self)

    @override
    def home(self) -> VPath:
        return self.root()

    @property
    @override
    def capabilities(self) -> FilesystemCapabilities:
        return FilesystemCapabilities(
            streaming_iterdir=False,
            watch=False,
            symlinks=False,
            permissions=False,
            read_only=True,
        )

    @property
    @override
    def scheme(self) -> str:
        return "archive"

    @override
    async def iterdir(
        self,
        path: VPath,
        *,
        cancel: threading.Event | None = None,
    ) -> AsyncIterator[VPath]:
        self._assert_vpath(path)
        for entry in self._archive.listdir(path.path):
            if cancel is not None and cancel.is_set():
                return
            vp = VPath(path.path / entry, self)
            vp._stat = self.stat(vp)
            yield vp

    @override
    def parent(self, path: VPath) -> VPath:
        self._assert_vpath(path)
        if path.path.parent == path.path:
            return self._archive_parent
        return VPath(path.path.parent, self)

    @override
    def stat(self, path: VPath) -> Stat:
        self._assert_vpath(path)
        self._member_paths[id(path)] = path
        return self._archive.stats(path.path)

    @override
    def is_same_device(self, path1: VPath, path2: VPath) -> bool:
        self._assert_vpath(path1)
        return path1.filesystem == path2.filesystem

    @override
    def read(self, path: VPath) -> StreamReaderLike:
        self._assert_vpath(path)
        return self._archive.read(path.path)

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

    @override
    def readlink(self, path: VPath) -> str:
        self._assert_vpath(path)
        raise OSError(errno.EINVAL, "Not a symbolic link", str(path.path))

    def __eq__(self, value: object) -> bool:
        return isinstance(value, ArchiveFilesystem) and self._identity == value._identity and self._archive_parent == value._archive_parent

    def __hash__(self) -> int:
        return hash((self._identity, self._archive_parent))
