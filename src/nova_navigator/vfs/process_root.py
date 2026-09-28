"""Per-process scratch root shared by local copies and archive rebuild staging.

Kept dependency-free (no imports of :mod:`nova_navigator.vfs.local_copy` or
:mod:`nova_navigator.vfs.filesystems.archive`) so both modules can import it at
module scope without creating a circular import between them.
"""

from __future__ import annotations

import os
import tempfile
from pathlib import Path

LOCAL_COPY_ROOT = Path(tempfile.gettempdir()) / "nova-navigator"


def process_root(pid: int | None = None) -> Path:
    """Directory holding this process's local copies."""
    return LOCAL_COPY_ROOT / str(os.getpid() if pid is None else pid)
