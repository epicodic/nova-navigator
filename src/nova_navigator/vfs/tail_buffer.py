"""TailBuffer — thread-safe byte buffer that keeps only the newest bytes."""

from __future__ import annotations

import threading


class TailBuffer:
    """Accumulate bytes, keeping at most *limit* of the newest ones."""

    def __init__(self, limit: int) -> None:
        self._limit = limit
        self._data = bytearray()
        self._lock = threading.Lock()

    def append(self, chunk: bytes) -> None:
        """Add *chunk*, discarding the oldest bytes beyond the limit."""
        with self._lock:
            self._data += chunk
            excess = len(self._data) - self._limit
            if excess > 0:
                del self._data[:excess]

    def text(self) -> str:
        """Return the buffered bytes decoded as UTF-8 (invalid bytes replaced)."""
        with self._lock:
            return self._data.decode("utf-8", errors="replace")
