"""The per-document state of the editor screen (ADR-4, REQ-15)."""

from __future__ import annotations

import os
import stat
from pathlib import Path


def not_regular_reason(path: Path) -> str | None:
    """Return why `path` cannot be opened (it exists but is a FIFO, a device, a directory, ...), or `None`."""
    try:
        mode = os.stat(path).st_mode
    except OSError:
        return None
    if stat.S_ISREG(mode):
        return None
    return f"{path}: not a regular file"
