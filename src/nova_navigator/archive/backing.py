"""Where an archive mount's bytes live and how a rebuilt archive is committed."""

from __future__ import annotations

from abc import ABC, abstractmethod
from pathlib import Path
from typing import TYPE_CHECKING

from ..vfs.vpath import VPath

if TYPE_CHECKING:
    # Only used in annotations below; a module-level import would cycle back through
    # nova_navigator.vfs.local_copy -> nova_navigator.vfs.filesystems.archive -> this module.
    from ..vfs.local_copy import LocalCopy

_CHUNK = 1024 * 1024


class ArchiveBacking(ABC):
    """Local archive bytes plus the operation that publishes a rebuilt archive."""

    @property
    @abstractmethod
    def source(self) -> VPath:
        """The archive file in its original filesystem."""

    @property
    @abstractmethod
    def local_path(self) -> Path:
        """Local file the archive reader opens."""

    @abstractmethod
    def prepare_write(self) -> None:
        """Make local_path current with the source before a rebuild."""

    @abstractmethod
    def commit(self, rebuilt: Path) -> None:
        """Publish *rebuilt* as the new archive; *rebuilt* is consumed."""


class LocalArchiveBacking(ArchiveBacking):
    """Archive mounted directly from a local file; commit replaces it in place."""

    def __init__(self, source: VPath) -> None:
        self._source = source

    @property
    def source(self) -> VPath:
        return self._source

    @property
    def local_path(self) -> Path:
        return Path(self._source.path)

    def prepare_write(self) -> None:
        pass

    def commit(self, rebuilt: Path) -> None:
        writer = self._source.filesystem.write_atomic(self._source)
        try:
            with rebuilt.open("rb") as reader:
                while chunk := reader.read(_CHUNK):
                    writer.write(chunk)
        except BaseException:
            writer.abort()
            raise
        writer.close()
        rebuilt.unlink()


class CopiedArchiveBacking(ArchiveBacking):
    """Archive mounted from a LocalCopy of a remote archive file."""

    def __init__(self, copy: LocalCopy) -> None:
        self._copy = copy

    @property
    def source(self) -> VPath:
        return self._copy.source

    @property
    def local_path(self) -> Path:
        return self._copy.path

    @property
    def copy(self) -> LocalCopy:
        """The underlying local copy of the archive file."""
        return self._copy

    def prepare_write(self) -> None:
        if self._copy.source_changed():
            self._copy.refresh()

    def commit(self, rebuilt: Path) -> None:
        rebuilt.replace(self._copy.path)
        self._copy.write_back()
