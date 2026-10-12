"""REQ-21 view part: the hex view reads only length and bounded byte ranges, on an 8 GiB sparse file (S0003 ACT4 task 2, design 9).

Skipped when the file system has no sparse files.
"""

from __future__ import annotations

import ast
import time
import tracemalloc
from collections.abc import Awaitable, Callable, Iterator
from contextlib import contextmanager
from pathlib import Path
from unittest.mock import patch

import pytest
from textual.pilot import Pilot

from nova_editor.core import SourceChanged
from nova_editor.core.byte_source import BLOCK_SIZE
from nova_editor.document import LazyDocument
from nova_editor.widget import HexView, hex_layout, hex_view
from tests.nova_editor.widget.hex_helpers import (
    MIB,
    SPARSE_SIZE,
    CountingSource,
    HexHost,
    ReadLog,
    forbid,
    logged_reads,
    make_sparse,
    opened,
    row_texts,
    visible_height,
    write_file,
)

SIZE = (81, 24)
"""Terminal size: 79 content cells hold 16 bytes per row with the 9 offset digits of an 8 GiB file (80 columns give 8)."""
BPR = 16
"""Bytes per row at `SIZE`."""
FORBIDDEN = {"snapshot", "row_at_offset", "line_count", "get_line", "byte_offset", "line_length", "rows", "lines"}


@contextmanager
def budget_view(path: Path, *, fail_after: int | None = None) -> Iterator[tuple[LazyDocument, CountingSource, ReadLog, HexView]]:
    """A hex view over `path` whose source counts reads, whose `read_bytes` is logged and whose row APIs fail the test."""
    source = CountingSource(path)
    with opened(source, autostart=False) as document:
        if fail_after is not None:  # counted from here: opening the document reads the first and the last byte
            source.fail_after = len(source.reads) + fail_after
        with logged_reads(document) as log:
            forbid(document)
            yield document, source, log, HexView(lambda: document)


def first_offset(view: HexView) -> int:
    """Return the offset printed at the start of the first visible row."""
    return int(row_texts(view)[0].split(" ", 1)[0], 16)


def total_rows_of(view: HexView) -> int:
    return view.length // view.hex_layout.bytes_per_row + 1


def check_budget(view: HexView, source: CountingSource, log: ReadLog, calls_before: int, reads_before: int) -> None:
    """At most one `read_bytes` call of at most three screens since `calls_before`, and source blocks bounded by its size."""
    calls = log.calls[calls_before:]
    limit = 3 * visible_height(view) * view.hex_layout.bytes_per_row
    assert len(calls) <= 1, calls
    assert all(size <= limit for _, size in calls), calls
    size = sum(size for _, size in calls)
    assert source.blocks(reads_before) <= size // BLOCK_SIZE + 2


@pytest.mark.asyncio
async def test_first_page_reads_once(tmp_path: Path) -> None:
    with budget_view(make_sparse(tmp_path)) as (_, source, log, view):
        reads_before = len(source.reads)
        async with HexHost(view).run_test(size=SIZE) as pilot:
            await pilot.pause()
            assert first_offset(view) == 0
            assert view.hex_layout.bytes_per_row == BPR
            assert len(log.calls) == 1
            check_budget(view, source, log, 0, reads_before)


async def _row_scroll(pilot: Pilot[None], view: HexView) -> int:
    view.scroll_down(animate=False)
    await pilot.pause()
    return 1


async def _page_up(pilot: Pilot[None], view: HexView) -> int:
    view.scroll_page_up(animate=False)
    await pilot.pause()
    return 3 * visible_height(view)


async def _goto_end(pilot: Pilot[None], view: HexView) -> int:
    view.scroll_to(y=total_rows_of(view) - 1, animate=False, immediate=True)
    await pilot.pause()
    return total_rows_of(view) - visible_height(view)


async def _goto_far(pilot: Pilot[None], view: HexView) -> int:
    view.scroll_to(y=0x1_2345_6789 // BPR, animate=False, immediate=True)
    await pilot.pause()
    return 0x1_2345_6789 // BPR


async def _scrollbar_jump(pilot: Pilot[None], view: HexView) -> int:
    await pilot.click(view.vertical_scrollbar, offset=(0, 12))
    await pilot.pause()
    return round(view.scroll_y)


async def _resize(pilot: Pilot[None], view: HexView) -> int:
    await pilot.resize_terminal(120, 30)
    await pilot.pause()
    assert visible_height(view) == 30
    return 0


OPERATIONS: dict[str, Callable[[Pilot[None], HexView], Awaitable[int]]] = {
    "row_scroll": _row_scroll,
    "page_up": _page_up,
    "goto_end": _goto_end,
    "goto_far": _goto_far,
    "scrollbar_jump": _scrollbar_jump,
    "resize": _resize,
}
"""Operation name -> coroutine that runs it and returns the row expected at the top afterwards."""


@pytest.mark.asyncio
@pytest.mark.parametrize("name", list(OPERATIONS))
async def test_budget_per_operation(tmp_path: Path, name: str) -> None:
    with budget_view(make_sparse(tmp_path)) as (_, source, log, view):
        async with HexHost(view).run_test(size=SIZE) as pilot:
            await pilot.pause()
            row_texts(view)
            if name == "page_up":  # start four pages down, so that the page above is outside the first window
                view.scroll_to(y=4 * visible_height(view), animate=False, immediate=True)
                await pilot.pause()
                row_texts(view)
            calls_before, reads_before = len(log.calls), len(source.reads)
            expected_top = await OPERATIONS[name](pilot, view)
            assert first_offset(view) == expected_top * BPR
            check_budget(view, source, log, calls_before, reads_before)


@pytest.mark.asyncio
async def test_page_down_four_times_reads_at_most_once_per_step(tmp_path: Path) -> None:
    with budget_view(make_sparse(tmp_path)) as (_, source, log, view):
        async with HexHost(view).run_test(size=SIZE) as pilot:
            await pilot.pause()
            row_texts(view)
            height = visible_height(view)
            for step in range(1, 5):
                calls_before, reads_before = len(log.calls), len(source.reads)
                view.scroll_page_down(animate=False)
                await pilot.pause()
                assert first_offset(view) == step * height * BPR
                check_budget(view, source, log, calls_before, reads_before)
            assert len(log.calls) <= 3  # the first page plus one refill for every second page


@pytest.mark.asyncio
async def test_virtual_height_of_the_8_gib_file(tmp_path: Path) -> None:
    with budget_view(make_sparse(tmp_path)) as (_, _source, _log, view):
        async with HexHost(view).run_test(size=SIZE) as pilot:
            await pilot.pause()
            assert view.virtual_size.height == SPARSE_SIZE // BPR + 1
            assert view.hex_layout.offset_digits == 9


async def _peak_of_sequence(path: Path) -> tuple[int, int, int]:
    """Run first page, four page downs, end and middle; return the traced peak, the window size and its limit."""
    with budget_view(path) as (_, _source, _log, view):
        async with HexHost(view).run_test(size=SIZE) as pilot:
            await pilot.pause()
            row_texts(view)
            tracemalloc.start()
            try:
                for _ in range(4):
                    view.scroll_page_down(animate=False)
                    await pilot.pause()
                    row_texts(view)
                for row in (total_rows_of(view) - 1, total_rows_of(view) // 2):
                    view.scroll_to(y=row, animate=False, immediate=True)
                    await pilot.pause()
                    row_texts(view)
                peak = tracemalloc.get_traced_memory()[1]
            finally:
                tracemalloc.stop()
            return peak, len(view._win_data), 3 * visible_height(view) * BPR


@pytest.mark.asyncio
async def test_memory_does_not_depend_on_the_file_size(tmp_path: Path) -> None:
    sparse = make_sparse(tmp_path)
    small = write_file(tmp_path, bytes(MIB), "small.bin")
    small_peak, _, _ = await _peak_of_sequence(small)
    sparse_peak, window, limit = await _peak_of_sequence(sparse)
    assert abs(sparse_peak - small_peak) < 256 * 1024
    assert window <= limit


@pytest.mark.asyncio
async def test_a_source_change_is_reported_once_and_draws_blanks(tmp_path: Path) -> None:
    path = write_file(tmp_path, bytes(range(256)) * 256)
    reports: list[SourceChanged] = []
    with budget_view(path, fail_after=1) as (_, _source, _log, view):
        view.on_source_changed = reports.append
        async with HexHost(view).run_test(size=SIZE) as pilot:
            await pilot.pause()
            assert row_texts(view)[0].startswith("00000000  00 01 02")
            view.scroll_to(y=2000, animate=False, immediate=True)
            await pilot.pause()
            assert all(text.strip() == "" for text in row_texts(view))
            view.scroll_to(y=3000, animate=False, immediate=True)
            await pilot.pause()
            row_texts(view)
            view.scroll_to(y=0, animate=False, immediate=True)
            await pilot.pause()
            assert row_texts(view)[0].startswith("00000000  00 01 02")
    assert len(reports) == 1
    assert isinstance(reports[0], SourceChanged)


@pytest.mark.asyncio
async def test_a_closed_document_draws_blanks_without_a_report(tmp_path: Path) -> None:
    path = write_file(tmp_path, bytes(range(256)) * 256)
    reports: list[SourceChanged] = []
    with budget_view(path) as (document, _source, _log, view):
        view.on_source_changed = reports.append
        async with HexHost(view).run_test(size=SIZE) as pilot:
            await pilot.pause()
            assert row_texts(view)[0].startswith("00000000  00 01 02")
            document.close()
            assert document.wait_closed(10.0)
            view.scroll_to(y=2000, animate=False, immediate=True)
            await pilot.pause()
            assert all(text.strip() == "" for text in row_texts(view))
    assert reports == []


@pytest.mark.asyncio
async def test_wall_clock_sanity(tmp_path: Path) -> None:
    with budget_view(make_sparse(tmp_path)) as (_, _source, _log, view):
        started = time.monotonic()
        async with HexHost(view).run_test(size=SIZE) as pilot:
            await pilot.pause()
            assert first_offset(view) == 0
            assert time.monotonic() - started < 1.0
            started = time.monotonic()
            view.scroll_to(y=total_rows_of(view) - 1, animate=False, immediate=True)
            await pilot.pause()
            assert row_texts(view)[-1].rstrip() == f"{SPARSE_SIZE:09X}"
            assert time.monotonic() - started < 1.0


def test_hex_sources_do_not_use_whole_row_apis() -> None:
    for module in (hex_view, hex_layout):
        assert module.__file__ is not None
        tree = ast.parse(Path(module.__file__).read_text())
        for node in ast.walk(tree):
            if isinstance(node, ast.Attribute):
                assert node.attr not in FORBIDDEN, (module.__name__, node.attr, node.lineno)
            if isinstance(node, ast.Name):
                assert node.id not in FORBIDDEN, (module.__name__, node.id, node.lineno)


@pytest.mark.asyncio
async def test_the_foreground_is_touched_by_keys_and_mouse(tmp_path: Path) -> None:
    with opened(write_file(tmp_path, bytes(64))) as document:
        view = HexView(lambda: document)
        with patch.object(document.foreground, "touch", wraps=document.foreground.touch) as touch:
            async with HexHost(view).run_test(size=SIZE) as pilot:
                await pilot.pause()
                assert row_texts(view)[0].startswith("00000000  00")
                touch.reset_mock()
                await pilot.press("down")
                await pilot.pause()
                assert touch.called
                touch.reset_mock()
                await pilot.click(view, offset=(20, 1))
                await pilot.pause()
                assert touch.called
