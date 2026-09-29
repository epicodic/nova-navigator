"""Test doubles and reference implementations for nova_editor.core."""

from __future__ import annotations

import threading

from nova_editor.core.byte_source import ByteSource

LF = 0x0A
CR = 0x0D

ATOMS: list[bytes] = [
    b"a",
    b"b",
    b"\n",
    b"\r",
    b"\r\n",
    b"\t",
    "\u00e9".encode(),
    "\u6f22".encode(),
    "e\u0301".encode(),
    "\U0001f600".encode(),
    b"\x80",
    b"\xff",
    "\u2028".encode(),
    b"\x0b",
    b"\x85",
    b"\xef\xbb\xbf",
]

VALID_ATOMS: list[bytes] = [b"a", b"b", b"\t", "\u00e9".encode(), "\u6f22".encode(), "e\u0301".encode(), "\U0001f600".encode(), "\u2028".encode(), b"\x0b"]


class MemorySource:
    """In-memory `ByteSource` that counts reads.

    `gate`, when given, blocks reads with `cache=False` (the scan path) until it is set; reads with `cache=True` (the UI path) are never blocked.
    """

    def __init__(self, data: bytes, *, gate: threading.Event | None = None) -> None:
        self._data = data
        self.gate = gate
        self._lock = threading.Lock()
        self.reads = 0
        self.bytes_read = 0

    def length(self) -> int:
        return len(self._data)

    def read(self, offset: int, size: int, *, cache: bool = True) -> bytes:
        if not cache and self.gate is not None:
            self.gate.wait()
        chunk = self._data[offset : max(offset, min(offset + size, len(self._data)))]
        with self._lock:
            self.reads += 1
            self.bytes_read += len(chunk)
        return chunk

    def close(self) -> None:
        return

    def reset_counters(self) -> None:
        with self._lock:
            self.reads = 0
            self.bytes_read = 0


class CountingSource:
    """Wraps any `ByteSource` and counts the bytes returned by reads."""

    def __init__(self, inner: ByteSource) -> None:
        self._inner = inner
        self.bytes_read = 0

    def length(self) -> int:
        return self._inner.length()

    def read(self, offset: int, size: int, *, cache: bool = True) -> bytes:
        chunk = self._inner.read(offset, size, cache=cache)
        self.bytes_read += len(chunk)
        return chunk

    def close(self) -> None:
        self._inner.close()


def reference_rows(data: bytes) -> list[tuple[int, int, int]]:
    """Return (start, content_end, end) of every row; terminators are LF, CRLF and lone CR (DEC-13)."""
    rows: list[tuple[int, int, int]] = []
    start = 0
    i = 0
    size = len(data)
    while i < size:
        byte = data[i]
        if byte == LF:
            rows.append((start, i, i + 1))
            start = i + 1
            i += 1
        elif byte == CR:
            after = i + 2 if data[i + 1 : i + 2] == b"\n" else i + 1
            rows.append((start, i, after))
            start = after
            i = after
        else:
            i += 1
    rows.append((start, size, size))
    return rows
