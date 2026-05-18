from __future__ import annotations

import threading
from abc import ABC, abstractmethod
from collections.abc import AsyncIterator, Awaitable, Callable
from contextlib import asynccontextmanager
from dataclasses import dataclass
from pathlib import PurePath
from typing import Protocol

from .types import Stat
from .vpath import VPath


class StreamReaderLike(Protocol):
    """Protocol for objects that can read binary data in fixed-size chunks."""

    def read(self, size: int) -> bytes: ...
    def close(self) -> None: ...


class StreamWriterLike(Protocol):
    """Protocol for objects that can write binary data."""

    def write(self, data: bytes) -> int: ...
    def close(self) -> None: ...


@dataclass(frozen=True)
class FilesystemCapabilities:
    """Runtime capabilities of a filesystem instance."""

    streaming_iterdir: bool = False
    """True if iterdir yields items incrementally before all entries are ready.

    False means all items arrive in a single burst.
    The browser uses this flag to decide whether to show a spinner.
    """

    watch: bool = False
    """True if the filesystem can notify on directory changes."""

    symlinks: bool = False
    """True if this filesystem instance supports symbolic links."""

    permissions: bool = False
    """True if this filesystem instance exposes POSIX permission bits."""


class Filesystem(ABC):
    """Abstract base class for virtual filesystem implementations.

    Subclasses provide access to local disks, remote SSH hosts, archives, and
    other storage backends through a uniform path-oriented interface.  All path
    arguments must be :class:`~nova_navigator.vfs.vpath.VPath` instances that
    belong to *this* filesystem instance; :meth:`_assert_vpath` enforces this
    invariant.
    """

    def _assert_vpath(self, path: VPath) -> None:
        if path.filesystem != self:
            raise ValueError(f"VPath {path} does not belong to filesystem {self}")

    @property
    def capabilities(self) -> FilesystemCapabilities:
        """Return the runtime capabilities of this filesystem instance."""
        return FilesystemCapabilities()

    def path(self, p: str | PurePath) -> VPath:
        """Create a :class:`~nova_navigator.vfs.vpath.VPath` bound to this filesystem."""
        return VPath(str(p), self)

    def uri_for_path(self, path: PurePath) -> str:
        """Return the URI string for *path* on this filesystem.

        The default implementation returns a home-relative path (``~/…``) when the
        path is inside the home directory, falling back to the absolute path string.
        Override in subclasses to include a scheme and authority, e.g. ``ssh://…``.
        """
        home = self.home()
        try:
            rel_path = path.relative_to(home.path)
            return str("~" / rel_path)
        except ValueError:
            return str(path)

    def resolve_link(self, path: VPath) -> VPath:
        """Return the resolved target path of the symbolic link at *path*."""
        self._assert_vpath(path)
        target = self.readlink(path)
        if target.startswith("/"):
            return self.path(target)
        return self.parent(path) / target

    @abstractmethod
    def is_same_device(self, path1: VPath, path2: VPath) -> bool:
        """Return True if *path1* and *path2* are on the same device (filesystem)."""

    @abstractmethod
    def cwd(self) -> VPath:
        """Return the current working directory path."""

    @abstractmethod
    def root(self) -> VPath:
        """Return the root directory path."""

    @abstractmethod
    def home(self) -> VPath:
        """Return the home directory path."""

    @abstractmethod
    def iterdir(
        self,
        path: VPath,
        *,
        cancel: threading.Event | None = None,
    ) -> AsyncIterator[VPath]:
        """Yield directory entries as they arrive, with stat pre-populated.

        Each yielded VPath has ``_stat`` already set from data bundled in the
        listing response; no separate ``stat()`` call is needed for the listing.
        Implementations must check ``cancel.is_set()`` between batches and stop
        cleanly when it is set.
        """

    @asynccontextmanager
    async def watch(
        self,
        path: VPath,
        callback: Callable[[VPath], Awaitable[None]],
    ) -> AsyncIterator[None]:
        """Async context manager that invokes callback when path changes.

        The default implementation is a no-op.
        Subclasses that support watching override this method and set
        ``capabilities.watch = True``.
        """
        yield  # default no-op; subclasses override

    @abstractmethod
    def stat(self, path: VPath) -> Stat:
        """Return the stats associated with the path."""

    @abstractmethod
    def parent(self, path: VPath) -> VPath:
        """Return the parent path of the given path."""

    @abstractmethod
    def read(self, path: VPath) -> StreamReaderLike:
        """Return a stream reader for the given path."""

    @abstractmethod
    def write(self, path: VPath) -> StreamWriterLike:
        """Return a stream writer for the given path."""

    @abstractmethod
    def remove(self, path: VPath) -> None:
        """Remove the file at the given path."""

    @abstractmethod
    def rename(self, src_path: VPath, dst_path: VPath) -> None:
        """Rename the file at *src_path* to *dst_path*."""

    @abstractmethod
    def rmdir(self, path: VPath) -> None:
        """Remove the directory at the given path (must be empty)."""

    @abstractmethod
    def mkdir(self, path: VPath) -> None:
        """Create a directory at the given path.

        If the directory already exists, FileExistsError is raised.
        If a parent directory in the path does not exist, FileNotFoundError is raised.
        """

    @abstractmethod
    def copy_stat(self, path: VPath, stat: Stat) -> None:
        """Apply file attributes from *stat* (modification time, permissions) to *path*.

        Filesystems that do not support setting attributes (e.g. read-only archive
        filesystems) should implement this as a no-op.
        """

    @abstractmethod
    def refresh(self, path: VPath | None = None) -> None:
        """Discard any cached data for *path* (or all cached data if *path* is None).

        The next read after this call will fetch fresh data from the source.
        Filesystems without caching should implement this as a no-op.
        """

    @abstractmethod
    def readlink(self, path: VPath) -> str:
        """Return the target of the symbolic link at *path*.

        Raises ``OSError`` if *path* is not a symbolic link.
        """
