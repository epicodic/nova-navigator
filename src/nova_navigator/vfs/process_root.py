"""Per-process scratch root shared by local copies and archive rebuild staging.

Kept dependency-free (no imports of :mod:`nova_navigator.vfs.local_copy` or
:mod:`nova_navigator.vfs.filesystems.archive`) so both modules can import it at
module scope without creating a circular import between them.
"""

from __future__ import annotations

import os
import shutil
import tempfile
from collections.abc import Callable
from pathlib import Path

LOCAL_COPY_ROOT = Path(tempfile.gettempdir()) / "nova-navigator"


def process_root(pid: int | None = None) -> Path:
    """Directory holding this process's local copies."""
    return LOCAL_COPY_ROOT / str(os.getpid() if pid is None else pid)


def pid_is_alive(pid: int) -> bool:
    """True if *pid* names a process that is still running (or one we cannot inspect)."""
    try:
        os.kill(pid, 0)
    except ProcessLookupError:
        return False
    except PermissionError:
        return True
    return True


def find_orphans(root: Path = LOCAL_COPY_ROOT, is_alive: Callable[[int], bool] = pid_is_alive) -> list[Path]:
    """Remove empty per-process directories of dead processes and return those that still hold files.

    A child directory is only touched when its name is a plain integer and ``is_alive`` reports
    the pid as dead (this process's own directory is always alive, so it is never touched). An
    empty dead-pid directory is removed outright; one that still contains files is left in place
    and returned so the caller can tell the user about it.
    """
    orphans: list[Path] = []
    if not root.is_dir():
        return orphans
    for child in sorted(root.iterdir()):
        if not child.is_dir() or not child.name.isdigit() or is_alive(int(child.name)):
            continue
        if any(p.is_file() for p in child.rglob("*")):
            orphans.append(child)
        else:
            shutil.rmtree(child, ignore_errors=True)
    return orphans
