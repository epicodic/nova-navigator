import os
from pathlib import Path, PurePath
from stat import S_ISDIR, S_ISLNK
from typing import override

from .path import PathStats, VFSPath


class LocalPath(VFSPath):
    """A class representing a local filesystem path."""

    _path: PurePath
    _stat: os.stat_result | None = None
    _lstat: os.stat_result | None = None

    def __init__(self, path: str | os.PathLike[str]) -> None:
        self._path = PurePath(path)
        self._lstat = None
        self._stat = None

    def _ensure_stat(self) -> None:
        if self._stat is not None:
            return
        try:
            self._lstat = os.stat(self._path, follow_symlinks=False)
            self._stat = os.stat(self._path, follow_symlinks=True)
        except:  # noqa: E722
            self._stat = os.stat_result((0,) * 10)
            self._lstat = self._stat

    def __truediv__(self, other: str | os.PathLike[str]) -> "LocalPath":
        try:
            return LocalPath(self._path.joinpath(other))
        except TypeError:
            return NotImplemented

    def __eq__(self, other: object) -> bool:
        if not isinstance(other, LocalPath):
            return False
        return self._path == other._path

    def __hash__(self) -> int:
        return hash(self._path)

    @property
    @override
    def stats(self) -> PathStats:
        self._ensure_stat()
        assert self._stat is not None
        assert self._lstat is not None
        return PathStats(
            size=self._stat.st_size,
            modified=self._stat.st_mtime,
            is_hidden=self.name.startswith("."),
            is_directory=S_ISDIR(self._stat.st_mode),
            is_executable=self._stat.st_mode & 0o111 != 0,
            is_symlink=S_ISLNK(self._lstat.st_mode),
        )

    @override
    def iterdir(self) -> list[VFSPath]:
        return [self / name for name in os.listdir(self._path)]

    @property
    @override
    def name(self) -> str:
        return self._path.name

    @property
    @override
    def parent(self) -> "LocalPath":
        return LocalPath(self._path.parent)

    @property
    @override
    def compact_path_str(self) -> str:
        # if path is inside home directory, return relative to home
        home = Path.home()
        try:
            rel_path = self._path.relative_to(home)
            return str("~" / rel_path)
        except ValueError:
            return str(self._path)

    @property
    def path(self) -> PurePath:
        return self._path

    @staticmethod
    def cwd() -> "LocalPath":
        """Get the current working directory as a LocalPath."""
        return LocalPath(os.getcwd())
