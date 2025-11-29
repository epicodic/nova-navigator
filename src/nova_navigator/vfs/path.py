from abc import abstractmethod
from typing import Self, override

from ..path_stats import PathStats


class VFSPath:
    """A class representing a path in the virtual file system."""

    def iterdir(self) -> list["VFSPath"]:
        return []

    @property
    def stats(self) -> PathStats:
        """Return the stats associated with this path."""
        return PathStats()

    @property
    @abstractmethod
    def name(self) -> str:
        raise NotImplementedError

    @property
    @abstractmethod
    def parent(self) -> "VFSPath":
        raise NotImplementedError

    @property
    @abstractmethod
    def compact_path_str(self) -> str:
        raise NotImplementedError


class UpPath(VFSPath):
    """A class representing the parent directory path ("..")."""

    def __eq__(self, other: object) -> bool:
        return isinstance(other, UpPath)

    def __hash__(self) -> int:
        return hash("UpPath")

    @property
    def stats(self) -> PathStats:
        return PathStats(is_directory=True)

    @property
    @override
    def name(self) -> str:
        return ".."

    @property
    @override
    def parent(self) -> Self:
        return self

    @property
    @abstractmethod
    def compact_path_str(self) -> str:
        return ".."
