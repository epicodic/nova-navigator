"""Opening a file in the F3 viewer and ending the session: a local file in place, anything else through a private copy.

Textual-free. The host pushes the viewer screen for the returned :class:`ViewTarget` and calls
:func:`finish_viewing` once the screen is gone. A private copy is never registered with the
manager, never watched and never written back, so the source is never changed by viewing.
"""

from __future__ import annotations

import errno
import os
from dataclasses import dataclass
from pathlib import Path

from nova_navigator.vfs.local_copy import is_local_source
from nova_navigator.vfs.vpath import VPath

from .editor_session import check_readable
from .manager import LocalCopyManager


@dataclass(frozen=True)
class ViewTarget:
    """What the viewer shows for one session."""

    path: Path
    """The local file the viewer reads: the source itself, or a private copy."""
    directory: Path | None = None
    """The private copy's directory, removed by :func:`finish_viewing`; `None` for a local file."""
    key: str | None = None
    """The source's registry key, used to name the source in a notice."""


async def open_for_viewing(manager: LocalCopyManager, source: VPath) -> ViewTarget | None:
    """Prepare *source* for the viewer.

    A local file is viewed in place; any other file is downloaded into a private copy that is
    never written back.

    Returns:
        The target, or `None` when the private copy could not be made (the download failed or was cancelled).

    Raises:
        IsADirectoryError: *source* is a directory.
        OSError: A local file is missing or unreadable.
    """
    if source.stat.is_directory:
        raise IsADirectoryError(errno.EISDIR, os.strerror(errno.EISDIR), str(source.path))
    if is_local_source(source):
        path = Path(source.path)
        check_readable(path)
        return ViewTarget(path)
    view = await manager.create_view_copy(source)
    if view is None:
        return None
    return ViewTarget(view.copy.path, view.directory, view.key)


def finish_viewing(manager: LocalCopyManager, target: ViewTarget) -> None:
    """End the viewer session: remove the private copy, if any. The source is never touched."""
    if target.directory is not None:
        manager.discard_view_copy(target.directory)
