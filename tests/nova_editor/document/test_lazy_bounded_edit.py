"""Edits, undo and copy of a huge part of one long row read no bytes and allocate no copy of it (ACT4 Task 16, design 7 and 10)."""

from __future__ import annotations

import tracemalloc

from nova_editor.document._lazy_config import LazyConfig
from nova_editor.document._lazy_document import LazyDocument

ROW_BYTES = 8 << 20
READ_LIMIT = 256 << 10
ALLOC_LIMIT = 2 << 20
CONFIG = LazyConfig(stride=4096, index_long_line_threshold=1 << 16, long_row_threshold=1 << 18)


class CountingSource:
    """A single-row source that generates its bytes and counts what is read after `armed`."""

    def __init__(self, size: int) -> None:
        self._size = size
        self.armed = False
        self.bytes_read = 0

    def length(self) -> int:
        return self._size

    def read(self, offset: int, size: int, *, cache: bool = True) -> bytes:
        size = max(0, min(size, self._size - offset))
        if self.armed and cache:
            self.bytes_read += size
        unit = b"0123456789abcdef"
        return (unit * (size // len(unit) + 2))[offset % len(unit) : offset % len(unit) + size]

    def close(self) -> None:
        pass


def _open() -> tuple[LazyDocument, CountingSource]:
    source = CountingSource(ROW_BYTES)
    doc = LazyDocument(source, CONFIG)
    assert doc.wait_indexed(60.0)
    assert doc.is_long(0)
    assert doc.long_index(0).join(60.0)
    source.armed = True
    return doc, source


def test_delete_undo_redo_and_copy_of_half_a_long_row_read_and_allocate_little() -> None:
    doc, source = _open()
    quarter, three_quarters = ROW_BYTES // 4, ROW_BYTES // 4 * 3
    tracemalloc.start()
    try:
        peaks: dict[str, int] = {}

        def measure(step: str) -> None:
            peaks[step] = tracemalloc.get_traced_memory()[1]
            tracemalloc.reset_peak()

        tracemalloc.reset_peak()
        copied = doc.selection_content((0, quarter), (0, three_quarters))
        measure("copy")
        removed = doc.replace_range((0, quarter), (0, three_quarters), "")
        measure("delete")
        assert removed.removed is not None
        assert removed.end_location == (0, quarter)
        undone = doc.splice_bytes(quarter, quarter, removed.removed)
        measure("undo")
        assert doc.long_index(0).join(60.0)
        tracemalloc.reset_peak()
        assert removed.inserted is not None
        redone = doc.splice_bytes(quarter, three_quarters, removed.inserted)
        measure("redo")
    finally:
        tracemalloc.stop()
    assert copied.length == ROW_BYTES // 2
    assert copied.tail_chars == ROW_BYTES // 2
    assert removed.removed.tail_chars == ROW_BYTES // 2
    assert undone.end_location == (0, three_quarters)
    assert redone.end_location == (0, quarter)
    assert doc.length == ROW_BYTES // 2
    assert source.bytes_read < READ_LIMIT
    assert max(peaks.values()) < ALLOC_LIMIT, peaks
    doc.close()
