"""Opening a file in the built-in editor and ending the session: local file directly, anything else through a local copy.

Textual-free. The host (``NovaNavigator.open_editor``) pushes the editor screen for the returned
:class:`EditorTarget` and calls :func:`finish_editing` once the screen is gone.
"""

from __future__ import annotations

import errno
import os
import stat
from dataclasses import dataclass
from pathlib import Path
from typing import Literal

from nova_navigator.vfs.local_copy import is_local_source
from nova_navigator.vfs.vpath import VPath

from .manager import CopyEntry, CopyStatus, LocalCopyManager


@dataclass(frozen=True)
class EditorTarget:
    """What the editor edits for one session."""

    source: VPath
    """The file the user chose in the file list."""
    path: Path
    """The local file the editor opens: the source itself, or the local copy."""
    entry: CopyEntry | None
    """The local copy entry; `None` when the source is a local file and nothing was copied."""

    @property
    def read_only(self) -> bool:
        """True for a copy that is never written back (a JAR, or an archive on a read-only source)."""
        return self.entry is not None and self.entry.copy.read_only


@dataclass(frozen=True)
class SessionNotice:
    """A message for the user after the session ended."""

    message: str
    severity: Literal["warning", "error"]


def check_readable(path: Path) -> None:
    """Raise the `OSError` that opening *path* for reading raises (missing, no permission); refuse a FIFO or a device, whose opening can block.

    Raises:
        OSError: *path* cannot be read as a regular file.
    """
    mode = os.stat(path).st_mode
    if not stat.S_ISREG(mode):
        raise OSError(errno.EINVAL, "Not a regular file", str(path))
    with path.open("rb"):
        pass


async def open_for_editing(manager: LocalCopyManager, source: VPath) -> EditorTarget | None:
    """Prepare *source* for the editor.

    A local file is edited in place (no copy, no entry); any other file gets a local copy through *manager*.

    Returns:
        The target, or `None` when the copy could not be made (the open job failed or was cancelled, or a reopen conflict was answered with Cancel).

    Raises:
        IsADirectoryError: *source* is a directory.
        OSError: A local file is missing or unreadable.
    """
    if source.stat.is_directory:
        raise IsADirectoryError(errno.EISDIR, os.strerror(errno.EISDIR), str(source.path))
    if is_local_source(source):
        path = Path(source.path)
        check_readable(path)
        return EditorTarget(source, path, None)
    entry = await manager.open(source)
    if entry is None:
        return None
    return EditorTarget(source, entry.copy.path, entry)


async def finish_editing(manager: LocalCopyManager, target: EditorTarget) -> SessionNotice | None:
    """End the session: write a modified copy back once and say what the user must know.

    Returns:
        A notice for a failed or conflicting write-back, else `None`.
    """
    entry = target.entry
    if entry is None:
        return None
    await manager.release(entry)
    name = target.source.name
    if entry.status is CopyStatus.FAILED:
        return SessionNotice(
            f"Could not write {name} back: {entry.error}. The local copy keeps your changes; use Sync now in the Local Copies dialog.",
            "error",
        )
    if entry.status is CopyStatus.CONFLICT:
        return SessionNotice(f"{name} changed on the source. Your copy was not written back; resolve it in the Local Copies dialog.", "warning")
    return None
