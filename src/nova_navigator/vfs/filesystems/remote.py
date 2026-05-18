"""RemoteFilesystem — wraps any Filesystem under a ``remote://name`` URI."""

from __future__ import annotations

import threading
from collections.abc import AsyncIterator
from pathlib import PurePath
from typing import override

from nova_navigator.vfs.filesystem import Filesystem, FilesystemCapabilities, StreamReaderLike, StreamWriterLike
from nova_navigator.vfs.types import Stat
from nova_navigator.vfs.vpath import VPath


class RemoteFilesystem(Filesystem):
    """Wraps a concrete Filesystem and presents it under a ``remote://name`` URI.

    All filesystem operations are delegated to the inner filesystem with VPath
    re-binding so that paths stay consistent across the wrapper boundary.
    ``__getattr__`` provides a safety-net fallback for any attributes not
    explicitly overridden (non-abstract methods added in the future, etc.).
    """

    def __init__(self, name: str, inner: Filesystem) -> None:
        self._remote_name = name
        self._inner = inner

    def __getattr__(self, attr: str) -> object:
        return getattr(self._inner, attr)

    def __repr__(self) -> str:
        return f"RemoteFilesystem({self._remote_name!r}, {self._inner!r})"

    # ------------------------------------------------------------------
    # Path re-binding helpers
    # ------------------------------------------------------------------

    def _to_inner(self, path: VPath) -> VPath:
        """Return a copy of *path* bound to the inner filesystem."""
        inner_path = VPath(path.path, self._inner)
        inner_path._stat = path._stat
        return inner_path

    def _from_inner(self, path: VPath) -> VPath:
        """Return a copy of *path* bound to this filesystem."""
        outer_path = VPath(path.path, self)
        outer_path._stat = path._stat
        return outer_path

    # ------------------------------------------------------------------
    # URI override
    # ------------------------------------------------------------------

    @override
    def uri_for_path(self, path: PurePath) -> str:
        return f"remote://{self._remote_name}{path}"

    # ------------------------------------------------------------------
    # Capabilities (non-abstract, must explicitly delegate)
    # ------------------------------------------------------------------

    @property
    @override
    def capabilities(self) -> FilesystemCapabilities:
        return self._inner.capabilities

    # ------------------------------------------------------------------
    # Abstract method implementations — all delegate to inner
    # ------------------------------------------------------------------

    @override
    def is_same_device(self, path1: VPath, path2: VPath) -> bool:
        return self._inner.is_same_device(self._to_inner(path1), self._to_inner(path2))

    @override
    def cwd(self) -> VPath:
        return self._from_inner(self._inner.cwd())

    @override
    def root(self) -> VPath:
        return self._from_inner(self._inner.root())

    @override
    def home(self) -> VPath:
        return self._from_inner(self._inner.home())

    @override
    async def iterdir(
        self,
        path: VPath,
        *,
        cancel: threading.Event | None = None,
    ) -> AsyncIterator[VPath]:
        async for p in self._inner.iterdir(self._to_inner(path), cancel=cancel):
            yield self._from_inner(p)

    @override
    def stat(self, path: VPath) -> Stat:
        return self._inner.stat(self._to_inner(path))

    @override
    def parent(self, path: VPath) -> VPath:
        return self._from_inner(self._inner.parent(self._to_inner(path)))

    @override
    def read(self, path: VPath) -> StreamReaderLike:
        return self._inner.read(self._to_inner(path))

    @override
    def write(self, path: VPath) -> StreamWriterLike:
        return self._inner.write(self._to_inner(path))

    @override
    def remove(self, path: VPath) -> None:
        self._inner.remove(self._to_inner(path))

    @override
    def rename(self, src_path: VPath, dst_path: VPath) -> None:
        self._inner.rename(self._to_inner(src_path), self._to_inner(dst_path))

    @override
    def rmdir(self, path: VPath) -> None:
        self._inner.rmdir(self._to_inner(path))

    @override
    def mkdir(self, path: VPath) -> None:
        self._inner.mkdir(self._to_inner(path))

    @override
    def copy_stat(self, path: VPath, stat: Stat) -> None:
        self._inner.copy_stat(self._to_inner(path), stat)

    @override
    def refresh(self, path: VPath | None = None) -> None:
        self._inner.refresh(self._to_inner(path) if path is not None else None)

    @override
    def readlink(self, path: VPath) -> str:
        return self._inner.readlink(self._to_inner(path))
