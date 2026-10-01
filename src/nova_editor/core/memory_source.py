"""`ByteSource` over bytes held in memory."""

from __future__ import annotations


class BytesSource:
    """A `ByteSource` over an immutable `bytes` value (small files and text given as a string)."""

    def __init__(self, data: bytes) -> None:
        self._data = bytes(data)

    def length(self) -> int:
        return len(self._data)

    def read(self, offset: int, size: int, *, cache: bool = True) -> bytes:
        if offset < 0 or size < 0:
            raise ValueError("offset and size must not be negative")
        return self._data[offset : offset + size]

    def close(self) -> None:
        """Nothing to release."""
