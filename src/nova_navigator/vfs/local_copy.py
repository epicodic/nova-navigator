"""Local copies of VFS files with baselines and write-back."""

from __future__ import annotations

import hashlib
import os
from pathlib import Path, PurePosixPath

from .filesystems.archive import ArchiveFilesystem
from .filesystems.local import LocalFilesystem
from .vpath import VPath

LOCAL_COPY_ROOT = Path("/tmp/nova-navigator")
_MAX_SEGMENT_BYTES = 255
_MAX_SUFFIX_BYTES = 16


def process_root(pid: int | None = None) -> Path:
    """Directory holding this process's local copies."""
    return LOCAL_COPY_ROOT / str(os.getpid() if pid is None else pid)


def sanitize_segment(segment: str) -> str:
    """Validate one path segment and shorten it to a filesystem-safe length.

    Raises:
        ValueError: *segment* is empty, ``.``, ``..``, or contains a path
            separator or NUL byte.
    """
    if segment in {"", ".", ".."} or "/" in segment or "\0" in segment:
        raise ValueError(f"Unsafe path segment: {segment!r}")
    if len(segment.encode()) <= _MAX_SEGMENT_BYTES:
        return segment
    suffix = PurePosixPath(segment).suffix
    suffix = suffix if len(suffix.encode()) <= _MAX_SUFFIX_BYTES else ""
    tag = "~" + hashlib.sha256(segment.encode()).hexdigest()[:_MAX_SUFFIX_BYTES]
    budget = _MAX_SEGMENT_BYTES - len(tag.encode()) - len(suffix.encode())
    stem = segment.encode()[:budget].decode(errors="ignore")
    return stem + tag + suffix


def _segments(path: PurePosixPath) -> PurePosixPath:
    """Sanitize every non-root part of *path* and rejoin it as a relative path."""
    return PurePosixPath(*[sanitize_segment(part) for part in path.parts if part != "/"])


def relative_copy_path(source: VPath) -> PurePosixPath:
    """Map *source* to a path below the process root that mirrors its URI."""
    filesystem = source.filesystem
    if isinstance(filesystem, ArchiveFilesystem):
        return PurePosixPath("archive") / relative_copy_path(filesystem.source) / _segments(source.path)
    if isinstance(filesystem.unwrap(), LocalFilesystem):
        return PurePosixPath("local") / _segments(source.path)
    scheme, separator, rest = source.uri.partition("://")
    if not separator:
        raise ValueError(f"Cannot map URI without scheme: {source.uri}")
    return _segments(PurePosixPath(scheme)) / _segments(PurePosixPath("/" + rest))
