"""Byte-range access to the original file: protocol, change detection, and a pread source with a block cache.

The implementation is `os.pread` behind a small LRU block cache (DEC-9). A memory map is never used: a truncated
file must raise `SourceChanged`, never a signal (REQ-14).
"""

from __future__ import annotations

import os
import threading
from collections import OrderedDict
from pathlib import Path
from typing import Protocol

BLOCK_SIZE = 64 * 1024
CACHE_BLOCKS = 128


class SourceChanged(Exception):
    """The file changed underneath the editor: size or mtime differs, a read came back short, or the OS reported an I/O error."""


class ByteSource(Protocol):
    """A read-only, random-access byte source of a fixed length."""

    def length(self) -> int:
        """Return the length recorded at open time."""
        ...

    def read(self, offset: int, size: int, *, cache: bool = True) -> bytes:
        """Return `min(size, length - offset)` bytes at `offset`, or `b""` at or beyond the end.

        `cache=False` marks a bulk scan read that must not evict blocks the UI uses.
        Raises `SourceChanged` when the underlying data changed.
        """
        ...

    def close(self) -> None:
        """Release the source.

        Callers that share the source with a background scan should `cancel()` and `join()` the scan before closing.
        """
        ...


class PreadSource:
    """`os.pread` with one shared LRU block cache and change detection."""

    def __init__(self, path: Path | str, *, block_size: int = BLOCK_SIZE, cache_blocks: int = CACHE_BLOCKS) -> None:
        if block_size <= 0 or cache_blocks <= 0:
            raise ValueError("block_size and cache_blocks must be positive")
        self._fd = os.open(path, os.O_RDONLY)
        info = os.fstat(self._fd)
        self._size = info.st_size
        self._mtime_ns = info.st_mtime_ns
        self._block_size = block_size
        self._max_blocks = cache_blocks
        self._cache: OrderedDict[int, bytes] = OrderedDict()
        self._lock = threading.Condition()  # guards _cache, _failed and the close state; never held across I/O
        self._failed: SourceChanged | None = None
        self._inflight = 0
        self._closing = False
        self._closed = False

    def length(self) -> int:
        return self._size

    def read(self, offset: int, size: int, *, cache: bool = True) -> bytes:
        if offset < 0 or size < 0:
            raise ValueError("offset and size must not be negative")
        with self._lock:
            if self._closing:
                raise ValueError("read of a closed source")
            self._inflight += 1
        try:
            return self._read(offset, size, cache)
        finally:
            with self._lock:
                self._inflight -= 1
                if self._inflight == 0:
                    self._lock.notify_all()

    def _read(self, offset: int, size: int, cache: bool) -> bytes:
        self._check()
        end = min(offset + size, self._size)
        if end <= offset:
            return b""
        if not cache or end - offset > self._block_size * self._max_blocks:
            return self._pread_exact(offset, end - offset)
        first = offset // self._block_size
        last = (end - 1) // self._block_size
        data = b"".join(self._block(index) for index in range(first, last + 1))
        skip = offset - first * self._block_size
        return data[skip : skip + (end - offset)]

    def close(self) -> None:
        """Close the file descriptor once every read in flight has finished.

        This call blocks until reads already in flight return; it never interrupts them.
        Reads that start after `close` began raise `ValueError`.
        A second `close()` waits until the first one finished and then returns, also when `os.close` raised.
        Callers that share the source with a background scan should `cancel()` and `join()` the scan before closing, otherwise
        the scan's next read raises `ValueError` on its thread.
        """
        with self._lock:
            if self._closing:
                while not self._closed:
                    self._lock.wait()
                return
            self._closing = True
            while self._inflight:
                self._lock.wait()
            try:
                os.close(self._fd)
            finally:
                self._closed = True
                self._lock.notify_all()

    def _check(self) -> None:
        with self._lock:
            failed = self._failed
        if failed is not None:
            raise failed
        self._check_stat()

    def _check_stat(self) -> None:
        info = os.fstat(self._fd)
        if info.st_size != self._size or info.st_mtime_ns != self._mtime_ns:
            raise self._fail(SourceChanged(f"file changed: size {self._size} -> {info.st_size}, mtime_ns {self._mtime_ns} -> {info.st_mtime_ns}"))

    def _fail(self, error: SourceChanged) -> SourceChanged:
        with self._lock:
            if self._failed is None:
                self._failed = error
            return self._failed

    def _block(self, index: int) -> bytes:
        with self._lock:
            block = self._cache.get(index)
            if block is not None:
                self._cache.move_to_end(index)
                return block
        offset = index * self._block_size
        block = self._pread_exact(offset, min(self._block_size, self._size - offset))
        with self._lock:
            self._cache[index] = block
            self._cache.move_to_end(index)
            while len(self._cache) > self._max_blocks:
                self._cache.popitem(last=False)
        return block

    def _pread_exact(self, offset: int, size: int) -> bytes:
        """Read exactly `size` bytes or fail the source: a short read means the file shrank (REQ-14)."""
        chunks: list[bytes] = []
        got = 0
        while got < size:
            try:
                data = os.pread(self._fd, size - got, offset + got)
            except OSError as error:
                raise self._fail(SourceChanged(f"read failed at {offset + got}: {error}")) from error
            if not data:
                break
            chunks.append(data)
            got += len(data)
        if got < size:
            raise self._fail(SourceChanged(f"short read at {offset}: wanted {size}, got {got}"))
        return chunks[0] if len(chunks) == 1 else b"".join(chunks)
