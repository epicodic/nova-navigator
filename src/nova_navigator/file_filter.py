import fnmatch
from abc import ABC, abstractmethod
from dataclasses import dataclass
from typing import Self

from .vfs import VPath


class FileFilter(ABC):
    """Base class for filters that decide which files to include in copy/move operations."""

    @abstractmethod
    def matches(self, vpath: VPath) -> bool:
        """Return True if *vpath* should be included in the operation."""


@dataclass
class FilenamePatternFilter(FileFilter):
    """Filter files by fnmatch-style filename patterns.

    Multiple patterns are separated by semicolons; a file matches if any pattern matches.
    """

    patterns: list[str]

    @classmethod
    def from_pattern_string(cls, s: str) -> Self:
        """Create a filter from a semicolon-separated pattern string.

        An empty or whitespace-only string is treated as ``["*"]`` (match all).
        """
        tokens = [t.strip() for t in s.split(";") if t.strip()]
        return cls(patterns=tokens if tokens else ["*"])

    @property
    def pattern_string(self) -> str:
        """Semicolon-joined patterns, suitable for display in a UI input."""
        return ";".join(self.patterns)

    def matches(self, vpath: VPath) -> bool:
        return any(fnmatch.fnmatch(vpath.name, p) for p in self.patterns)
