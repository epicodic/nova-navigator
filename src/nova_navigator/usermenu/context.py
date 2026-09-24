"""Evaluation context for user menu conditions and placeholders."""

from __future__ import annotations

import fnmatch
import mimetypes
import os
import re
import shlex
import shutil
from dataclasses import dataclass
from datetime import datetime
from pathlib import PurePosixPath
from urllib.parse import urlparse

from nova_navigator.vfs import Stat, VPath

CONTEXT_NAMES = frozenset({"file", "selection", "targets", "dir", "other", "which", "env"})
"""Names visible to ``when``/``default`` expressions and placeholders."""


@dataclass(frozen=True)
class PanelSnapshot:
    """Panel state captured on the UI thread before evaluation."""

    dir: VPath
    cursor: VPath | None
    selection: tuple[VPath, ...]
    listing: frozenset[str]


class Probe:
    """Cached I/O helpers shared by one menu evaluation.

    Commands run in *run_dir*, so ``which()`` checks the filesystem of that directory.
    """

    def __init__(self, run_dir: VPath) -> None:
        self._run_dir = run_dir
        self._which: dict[str, bool] = {}
        self._find_up: dict[tuple[VPath, str], VPath | None] = {}

    def which(self, command: str) -> bool:
        """Return True if *command* exists where the entry would run."""
        if command not in self._which:
            self._which[command] = self._lookup(command)
        return self._which[command]

    def find_up(self, start: VPath, name: str) -> VPath | None:
        """Return the nearest directory at or above *start* that contains *name*."""
        key = (start, name)
        if key not in self._find_up:
            self._find_up[key] = _search_up(start, name)
        return self._find_up[key]

    def _lookup(self, command: str) -> bool:
        fs = self._run_dir.filesystem
        if fs.scheme == "local":
            return shutil.which(command) is not None
        if not fs.capabilities.commands:
            return False
        result = fs.exec_command(f"command -v {shlex.quote(command)} >/dev/null", self._run_dir)
        return result.exit_code == 0


def _search_up(start: VPath, name: str) -> VPath | None:
    current = start
    while True:
        if _exists(current / name):
            return current
        parent = current.parent
        if parent.path == current.path:
            return None
        current = parent


def _exists(vpath: VPath) -> bool:
    try:
        vpath.filesystem.stat(vpath)
    except OSError:
        return False
    return True


class FileInfo:
    """Read-only view of a file or directory for conditions and placeholders."""

    def __init__(self, vpath: VPath) -> None:
        self._vpath = vpath

    @property
    def _stat(self) -> Stat:
        return self._vpath.stat

    @property
    def name(self) -> str:
        return self._vpath.name

    @property
    def stem(self) -> str:
        return PurePosixPath(self.name).stem

    @property
    def ext(self) -> str:
        """Last suffix without the dot (``"gz"`` for ``a.tar.gz``)."""
        return PurePosixPath(self.name).suffix.removeprefix(".")

    @property
    def path(self) -> str:
        return self._vpath.path.as_posix()

    @property
    def uri(self) -> str:
        return self._vpath.uri

    @property
    def is_file(self) -> bool:
        return not self._stat.is_directory

    @property
    def is_dir(self) -> bool:
        return self._stat.is_directory

    @property
    def is_link(self) -> bool:
        return self._stat.is_symlink

    @property
    def is_broken_link(self) -> bool:
        return self._stat.is_broken_symlink

    @property
    def is_executable(self) -> bool:
        return self._stat.is_executable

    @property
    def is_hidden(self) -> bool:
        return self._stat.is_hidden

    @property
    def size(self) -> int:
        return self._stat.size

    @property
    def mtime(self) -> datetime:
        return datetime.fromtimestamp(self._stat.modified)

    @property
    def mimetype(self) -> str:
        """MIME type guessed from the name, or ``""`` if unknown."""
        return mimetypes.guess_type(self.name)[0] or ""

    def matches(self, *globs: str, ignore_case: bool = False) -> bool:
        """Return True if the name matches any of *globs* (``fnmatch`` syntax)."""
        name = self.name.lower() if ignore_case else self.name
        return any(fnmatch.fnmatchcase(name, glob.lower() if ignore_case else glob) for glob in globs)

    def re(self, pattern: str) -> bool:
        """Return True if ``re.search(pattern, name)`` finds a match."""
        return re.search(pattern, self.name) is not None

    def __str__(self) -> str:
        return self.path

    def __repr__(self) -> str:
        return f"FileInfo({self.path!r})"


class DirInfo:
    """Read-only view of a panel directory."""

    def __init__(self, vpath: VPath, listing: frozenset[str], probe: Probe) -> None:
        self._vpath = vpath
        self._listing = listing
        self._probe = probe

    @property
    def name(self) -> str:
        return self._vpath.name

    @property
    def path(self) -> str:
        return self._vpath.path.as_posix()

    @property
    def uri(self) -> str:
        return self._vpath.uri

    @property
    def scheme(self) -> str:
        return self._vpath.filesystem.scheme

    @property
    def is_local(self) -> bool:
        return self.scheme == "local"

    @property
    def host(self) -> str:
        """Host part of the URI, or ``""`` for local directories."""
        return urlparse(self.uri).hostname or ""

    def has(self, name: str) -> bool:
        """Return True if *name* is in the panel's loaded listing (no I/O).

        Directories returned by :meth:`find_up` have no listing, so ``has()`` is always False for them.
        """
        return name in self._listing

    def find_up(self, name: str) -> DirInfo | None:
        """Return the nearest directory at or above this one that contains *name*."""
        found = self._probe.find_up(self._vpath, name)
        return None if found is None else DirInfo(found, frozenset(), self._probe)

    def __str__(self) -> str:
        return self.path

    def __repr__(self) -> str:
        return f"DirInfo({self.path!r})"


class PanelInfo:
    """One panel's cursor item, selection, targets and directory."""

    file: FileInfo | None
    selection: list[FileInfo]
    targets: list[FileInfo]
    dir: DirInfo

    def __init__(self, snapshot: PanelSnapshot, probe: Probe) -> None:
        self.file = FileInfo(snapshot.cursor) if snapshot.cursor is not None else None
        self.selection = [FileInfo(p) for p in snapshot.selection]
        self.targets = self.selection or ([self.file] if self.file is not None else [])
        self.dir = DirInfo(snapshot.dir, snapshot.listing, probe)


def build_context(active: PanelSnapshot, other: PanelSnapshot) -> dict[str, object]:
    """Return the names visible to conditions and placeholders."""
    probe = Probe(active.dir)
    panel = PanelInfo(active, probe)
    return {
        "file": panel.file,
        "selection": panel.selection,
        "targets": panel.targets,
        "dir": panel.dir,
        "other": PanelInfo(other, probe),
        "which": probe.which,
        "env": dict(os.environ),
    }
