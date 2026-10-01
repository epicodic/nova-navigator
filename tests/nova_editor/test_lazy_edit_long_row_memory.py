"""Enter and typing inside one huge row allocate memory in proportion to the view, never to the row (ACT4 Task 16, second round)."""

from __future__ import annotations

import tracemalloc
from collections.abc import AsyncIterator
from contextlib import asynccontextmanager

import pytest
from textual.pilot import Pilot

from nova_editor.document._cursor_anchor import CursorState
from nova_editor.document._lazy_config import LazyConfig
from nova_editor.document._lazy_document import LazyDocument
from nova_editor.widget import NovaTextArea
from tests.nova_editor.helpers_view import HostApp, await_first_layout, wait_until

ROW_BYTES = 8 << 20
ALLOC_LIMIT = 4 << 20
"""Peak traced allocation a step may add; a row-wide padded line would be 4 MiB or more per rendered row."""
RETAINED_LIMIT = 2 << 20
"""Memory 200 scattered edits may leave allocated; 64 retained indexes would hold about 8 MiB."""
CONFIG = LazyConfig(stride=4096, index_long_line_threshold=1 << 16, long_row_threshold=1 << 18, scan_block=1 << 16)
_UNIT = b"0123456789abcdef"


class GeneratedSource:
    """A single-row source that generates its bytes."""

    def __init__(self, size: int) -> None:
        self._size = size

    def length(self) -> int:
        return self._size

    def read(self, offset: int, size: int, *, cache: bool = True) -> bytes:
        size = max(0, min(size, self._size - offset))
        return (_UNIT * (size // len(_UNIT) + 2))[offset % len(_UNIT) : offset % len(_UNIT) + size]

    def close(self) -> None:
        pass


def _doc(area: NovaTextArea) -> LazyDocument:
    doc = area.document
    assert isinstance(doc, LazyDocument)
    return doc


def _frame(area: NovaTextArea) -> int:
    """Render every visible line and return the widest strip in cells."""
    return max(area.render_line(y).cell_length for y in range(area.size.height))


@asynccontextmanager
async def _open(*, wrap: bool) -> AsyncIterator[tuple[NovaTextArea, Pilot[None]]]:
    area = NovaTextArea.open(GeneratedSource(ROW_BYTES), soft_wrap=wrap, config=CONFIG)
    async with HostApp(area).run_test(size=(60, 14)) as pilot:
        await await_first_layout(pilot, area)
        doc = _doc(area)
        assert doc.wait_indexed(60)
        assert doc.long_index(0).join(60)
        area.move_cursor((0, ROW_BYTES // 2))
        await wait_until(pilot, lambda: area.cursor_state is CursorState.RESOLVED, 60)
        await pilot.pause()
        yield area, pilot


async def _joined(pilot: Pilot[None], area: NovaTextArea) -> None:
    doc = _doc(area)
    for row in range(doc.line_count):
        if doc.is_long(row):
            assert doc.long_index(row).join(60)
    await pilot.pause()
    await wait_until(pilot, lambda: area.cursor_state is CursorState.RESOLVED, 60)


@pytest.mark.asyncio
@pytest.mark.parametrize("wrap", [False, True])
async def test_enter_in_the_middle_of_a_huge_row_then_typing_stays_small(wrap: bool) -> None:
    async with _open(wrap=wrap) as (area, pilot):
        tracemalloc.start()
        try:
            for key in ("enter", "x", "x", "backspace", "enter", "x"):
                tracemalloc.reset_peak()
                await pilot.press(key)
                await _joined(pilot, area)
                widest = _frame(area)
                peak = tracemalloc.get_traced_memory()[1]
                assert peak < ALLOC_LIMIT, (key, peak)
                assert widest <= area.region.width + 1024, (key, widest)
        finally:
            tracemalloc.stop()
        assert _doc(area).line_count == 3


@pytest.mark.asyncio
@pytest.mark.parametrize("wrap", [False, True])
async def test_typing_after_scattered_edits_in_a_huge_row_stays_small(wrap: bool) -> None:
    async with _open(wrap=wrap) as (area, pilot):
        doc = _doc(area)
        spots = [(k + 1) * ROW_BYTES // 201 for k in range(200)]
        for column in reversed(spots):
            area.insert("e", (0, column))
        await _joined(pilot, area)
        area.move_cursor((0, ROW_BYTES // 2))
        await _joined(pilot, area)
        assert doc.line_count == 1
        tracemalloc.start()
        try:
            for key in ("x", "x", "backspace", "delete", "right", "left", "x"):
                tracemalloc.reset_peak()
                await pilot.press(key)
                await _joined(pilot, area)
                widest = _frame(area)
                peak = tracemalloc.get_traced_memory()[1]
                assert peak < ALLOC_LIMIT, (key, peak)
                assert widest <= area.region.width + 1024, (key, widest)
        finally:
            tracemalloc.stop()


def test_scattered_edits_do_not_retain_replaced_indexes() -> None:
    """Every edit replaces the long index of the row; the replaced ones (checkpoint arrays and a piece snapshot each) must not pile up."""
    size = 1 << 20
    config = LazyConfig(stride=4096, index_long_line_threshold=1 << 16, long_row_threshold=1 << 18, checkpoint_chars=256)
    doc = LazyDocument.from_text("0123456789abcdef" * (size // 16), config)
    assert doc.wait_indexed(60)
    assert doc.long_index(0).join(60)
    tracemalloc.start()
    try:
        before = tracemalloc.get_traced_memory()[0]
        for k in range(200):
            column = size - (k + 1) * (size // 202)
            doc.replace_range((0, column), (0, column), "e")
            assert doc.long_index(0).join(60)
        retained = tracemalloc.get_traced_memory()[0] - before
    finally:
        tracemalloc.stop()
    doc.close()
    assert retained < RETAINED_LIMIT, retained
