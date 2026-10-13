"""Binary detection (REQ-3): a file is binary when its first 64 KiB contain a NUL byte."""

from __future__ import annotations

from collections.abc import Callable

from nova_editor.core.byte_source import SourceChanged

HEAD_LIMIT = 65536
"""Number of bytes at the start of a file that are examined for a NUL byte."""


def head_is_binary(read: Callable[[int, int], bytes]) -> bool:
    """Tell whether the first `HEAD_LIMIT` bytes served by `read(offset, size)` contain a NUL byte.

    `read` is called exactly once, as `read(0, HEAD_LIMIT)`; a longer answer is cut to `HEAD_LIMIT` bytes.
    `SourceChanged` and `OSError` mean "text": the open itself worked and the next real read reports the problem.
    """
    try:
        head = read(0, HEAD_LIMIT)[:HEAD_LIMIT]
    except (SourceChanged, OSError):
        return False
    return b"\x00" in head
