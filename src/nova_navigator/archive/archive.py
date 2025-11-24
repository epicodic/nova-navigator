from abc import abstractmethod
from pathlib import PurePath
from typing import Literal

from ..path_stats import PathStats


class Archive:
    """A class providing an abstraction for archive files."""

    Mode = Literal["r", "w", "a"]

    _archive_path: PurePath
    _mode: Mode

    def __init__(self, archive_path: PurePath, mode: Mode) -> None:
        self._archive_path = archive_path
        self._mode = mode

    @abstractmethod
    def listdir(self, path: PurePath) -> list[PurePath]:
        """List the contents of a directory inside the archive."""
        raise NotImplementedError

    @abstractmethod
    def stats(self, path: PurePath) -> PathStats:
        """Get the stats of a file/directory inside the archive."""
        raise NotImplementedError
