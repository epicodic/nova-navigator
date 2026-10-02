"""Save support of the core: change detection of the file behind a document (REQ-14).

`ChangeKind`, `FileIdentity` and `SourceChanged` live in `byte_source` (the source needs them and `save` imports the source); they are
re-exported here. This module imports only `core` modules.
"""

from __future__ import annotations

import os
from pathlib import Path

from nova_editor.core.byte_source import ChangeKind, FileIdentity

__all__ = ["ChangeKind", "FileIdentity", "check_path"]


def check_path(path: Path | str, held: FileIdentity | None) -> ChangeKind:
    """Compare the file at `path` with the identity `held` since open time using one `os.stat` of the resolved path.

    `held=None` means "expected new": an existing file is `CREATED`, a missing one `UNCHANGED`.
    A directory in the way, a permission error or any other `OSError` is `UNREADABLE`.
    """
    try:
        info = os.stat(os.path.realpath(path))
    except FileNotFoundError:
        return ChangeKind.DELETED if held is not None else ChangeKind.UNCHANGED
    except OSError:
        return ChangeKind.UNREADABLE
    if held is None:
        return ChangeKind.CREATED
    if (info.st_dev, info.st_ino) != (held.dev, held.ino):
        return ChangeKind.REPLACED
    if info.st_size < held.size:
        return ChangeKind.TRUNCATED
    if info.st_size != held.size or info.st_mtime_ns != held.mtime_ns:
        return ChangeKind.MODIFIED
    return ChangeKind.UNCHANGED
