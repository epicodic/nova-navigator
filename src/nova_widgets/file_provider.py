"""FileProvider protocol for abstracting filesystem operations.

Provides a protocol-based abstraction for filesystem operations,
with LocalFileProvider for real filesystems and InMemoryFileProvider
for testing.
"""

from __future__ import annotations

from collections.abc import Iterator
from functools import lru_cache
from pathlib import Path, PurePath, PurePosixPath
from typing import Protocol, runtime_checkable

# --- Protocol definitions ---


@runtime_checkable
class FileStat(Protocol):
    """Read-only view of a file/directory entry metadata.

    Provides access to basic metadata about a filesystem entry
    without exposing the full Stat object.
    """

    @property
    def name(self) -> str:
        """Basename of the entry."""
        ...

    @property
    def is_dir(self) -> bool:
        """True if this entry is a directory."""
        ...

    @property
    def is_file(self) -> bool:
        """True if this entry is a regular file."""
        ...

    @property
    def is_hidden(self) -> bool:
        """True if this entry is hidden (name starts with .)."""
        ...


@runtime_checkable
class FileProvider(Protocol):
    """Abstract protocol for filesystem operations.

    Provides operations for navigating and querying a filesystem,
    supporting both real and in-memory implementations.
    """

    def home(self) -> PurePath:
        """Return the user's home directory."""
        ...

    def iterdir(self, path: PurePath) -> Iterator[FileStat]:
        """List entries in a directory.

        Yields FileStat objects for each entry in the directory.
        """
        ...

    def is_dir(self, path: PurePath) -> bool:
        """Check if a path is a directory."""
        ...

    def is_file(self, path: PurePath) -> bool:
        """Check if a path is a regular file."""
        ...

    def parent(self, path: PurePath) -> PurePath:
        """Return the parent directory of a path."""
        ...

    def joinpath(self, path: PurePath, name: str) -> PurePath:
        """Join a path with a name component."""
        ...

    def resolve(self, path: PurePath) -> PurePath | None:
        """Resolve a path to its canonical form.

        Returns the resolved path if the parent directory exists
        (even if the file itself doesn't exist), or None if the
        parent directory doesn't exist.
        """
        ...


# --- LocalFileProvider implementation ---


class LocalFileStat:
    """FileStat implementation for real filesystem entries."""

    def __init__(self, path: Path) -> None:
        """Initialize with a Path object."""
        self._path = path

    @property
    def name(self) -> str:
        """Basename of the entry."""
        return self._path.name

    @property
    def is_dir(self) -> bool:
        """True if this entry is a directory."""
        return self._path.is_dir()

    @property
    def is_file(self) -> bool:
        """True if this entry is a regular file."""
        return self._path.is_file()

    @property
    def is_hidden(self) -> bool:
        """True if this entry is hidden (name starts with .)."""
        return self._path.name.startswith(".")


class LocalFileProvider:
    """FileProvider implementation for the local filesystem.

    Returns real pathlib.Path objects (not wrapped PurePath).
    """

    def home(self) -> Path:
        """Return the user's home directory as a real Path."""
        return Path.home()

    def iterdir(self, path: PurePath) -> Iterator[FileStat]:
        """List entries in a directory."""
        real_path = Path(path)
        for entry in real_path.iterdir():
            yield LocalFileStat(entry)

    def is_dir(self, path: PurePath) -> bool:
        """Check if a path is a directory."""
        return Path(path).is_dir()

    def is_file(self, path: PurePath) -> bool:
        """Check if a path is a regular file."""
        return Path(path).is_file()

    def parent(self, path: PurePath) -> Path:
        """Return the parent directory as a real Path."""
        return Path(path).parent

    def joinpath(self, path: PurePath, name: str) -> Path:
        """Join a path with a name component, returning a real Path."""
        return Path(path) / name

    def resolve(self, path: PurePath) -> Path | None:
        """Resolve a path if parent directory exists.

        Returns the path if the parent directory exists (even if the
        file itself doesn't), or None if the parent doesn't exist.
        """
        real_path = Path(path)
        parent = real_path.parent
        if parent.exists() and parent.is_dir():
            return real_path
        return None


# --- InMemoryFileProvider implementation ---


class InMemoryFileStat:
    """FileStat implementation for in-memory filesystem entries."""

    def __init__(self, name: str, is_directory: bool) -> None:
        """Initialize with a name and directory flag."""
        self._name = name
        self._is_directory = is_directory

    @property
    def name(self) -> str:
        """Basename of the entry."""
        return self._name

    @property
    def is_dir(self) -> bool:
        """True if this entry is a directory."""
        return self._is_directory

    @property
    def is_file(self) -> bool:
        """True if this entry is a regular file."""
        return not self._is_directory

    @property
    def is_hidden(self) -> bool:
        """True if this entry is hidden (name starts with .)."""
        return self._name.startswith(".")


class InMemoryFileProvider:
    """FileProvider implementation for an in-memory filesystem.

    Useful for testing. Uses PurePosixPath internally.
    """

    def __init__(self) -> None:
        """Initialize an empty in-memory filesystem."""
        self._entries: dict[PurePosixPath, bool] = {}

    def add_dir(self, path: str) -> None:
        """Add a directory entry."""
        pure_path = PurePosixPath(path)
        self._entries[pure_path] = True

    def add_file(self, path: str) -> None:
        """Add a file entry."""
        pure_path = PurePosixPath(path)
        self._entries[pure_path] = False
        # Also ensure parent directory exists
        parent = pure_path.parent
        if parent != pure_path and parent not in self._entries:
            self._entries[parent] = True

    def home(self) -> PurePosixPath:
        """Return the home directory as a PurePosixPath."""
        return PurePosixPath("/home/user")

    def iterdir(self, path: PurePath) -> Iterator[FileStat]:
        """List entries in a directory."""
        path_obj = PurePosixPath(path)
        entries_in_dir: dict[str, bool] = {}
        for entry_path, is_dir in self._entries.items():
            if entry_path.parent == path_obj and entry_path != path_obj:
                entries_in_dir[entry_path.name] = is_dir
        for name, is_dir in sorted(entries_in_dir.items()):
            yield InMemoryFileStat(name, is_dir)

    def is_dir(self, path: PurePath) -> bool:
        """Check if a path is a directory."""
        path_obj = PurePosixPath(path)
        return self._entries.get(path_obj, False)

    def is_file(self, path: PurePath) -> bool:
        """Check if a path is a regular file."""
        path_obj = PurePosixPath(path)
        return path_obj in self._entries and not self._entries[path_obj]

    def parent(self, path: PurePath) -> PurePosixPath:
        """Return the parent directory."""
        return PurePosixPath(path).parent

    def joinpath(self, path: PurePath, name: str) -> PurePosixPath:
        """Join a path with a name component."""
        return PurePosixPath(path) / name

    def resolve(self, path: PurePath) -> PurePosixPath | None:
        """Resolve a path if parent directory exists.

        Returns the path if the parent directory exists (even if the
        file itself doesn't), or None if the parent doesn't exist.
        """
        path_obj = PurePosixPath(path)
        parent = path_obj.parent
        if parent in self._entries or parent == PurePosixPath("/"):
            return path_obj
        return None


# --- Singleton factory ---


@lru_cache(maxsize=1)
def default_file_provider() -> LocalFileProvider:
    """Get the default file provider singleton.

    Returns a LocalFileProvider singleton for use in the application.
    """
    return LocalFileProvider()
