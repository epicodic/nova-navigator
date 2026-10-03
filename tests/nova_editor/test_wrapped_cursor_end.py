"""A first move to the end of a wrapped document does not chase a stale virtual height (ACT7 design 3)."""

from __future__ import annotations

from pathlib import Path

import pytest
from textual.geometry import Offset, Size
from textual.pilot import Pilot

from nova_editor.document._document import Selection
from nova_editor.document._lazy_wrapped_document import LazyWrappedDocument
from nova_editor.widget import NovaTextArea
from tests.nova_editor.helpers_view import HostApp, await_first_layout, lazy_wrapped, wait_until

ROWS = 20_000
WIDTH = 20
MAX_MEASURES = 8
"""Most blocks a first jump to the end may measure (the old code measured more than 100)."""
MARK = "LASTROW-MARK"


def make_file(tmp_path: Path) -> Path:
    """Short rows (6 characters, so the first layout settles) first, then rows of 8 to 32 characters: at width 20 the running mean of extra wrapped lines drifts over more than 300 blocks."""
    lines = ["abcdef" if i < 64 else "x" * (8 + (i * 7) % 25) for i in range(ROWS - 1)]
    lines.append(MARK)
    path = tmp_path / "wrapped.txt"
    path.write_text("\n".join(lines))
    return path


def count_measures(monkeypatch: pytest.MonkeyPatch) -> list[int]:
    """Record the block of every `LazyWrappedDocument._measure` call."""
    calls: list[int] = []
    original = LazyWrappedDocument._measure

    def counting(self: LazyWrappedDocument, block: int) -> object:
        calls.append(block)
        return original(self, block)

    monkeypatch.setattr(LazyWrappedDocument, "_measure", counting)
    return calls


async def settled(pilot: Pilot[None], area: NovaTextArea) -> None:
    """Wait for the first layout and the complete line scan, then let the estimate timer go quiet."""
    await await_first_layout(pilot, area)
    assert area.document.wait_indexed(10.0)
    await wait_until(pilot, lambda: area.line_count_exact)
    await pilot.pause(0.2)


@pytest.mark.asyncio
async def test_move_cursor_to_the_last_row_measures_few_blocks(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    area = NovaTextArea.open(make_file(tmp_path), soft_wrap=True)
    async with HostApp(area).run_test(size=(WIDTH, 10)) as pilot:
        await settled(pilot, area)
        calls = count_measures(monkeypatch)
        area.move_cursor((area.line_count - 1, 0))
        await pilot.pause(0.2)
        assert len(calls) <= MAX_MEASURES, len(calls)
        wrapped = lazy_wrapped(area)
        assert area.virtual_size.height >= wrapped.height
        y = wrapped.y_of_row(area.line_count - 1)
        assert area.scroll_offset.y <= y < area.scroll_offset.y + area.size.height


@pytest.mark.asyncio
async def test_goto_the_last_line_measures_few_blocks(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    area = NovaTextArea.open(make_file(tmp_path), soft_wrap=True)
    async with HostApp(area).run_test(size=(WIDTH, 10)) as pilot:
        await settled(pilot, area)
        calls = count_measures(monkeypatch)
        area.goto_line(area.line_count)
        await wait_until(pilot, lambda: area.pending_progress is None and area.cursor_location[0] == area.line_count - 1)
        await pilot.pause(0.2)
        assert len(calls) <= MAX_MEASURES, len(calls)
        assert area.virtual_size.height >= lazy_wrapped(area).height


@pytest.mark.asyncio
async def test_search_placement_at_the_last_row_measures_few_blocks(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    area = NovaTextArea.open(make_file(tmp_path), soft_wrap=True)
    async with HostApp(area).run_test(size=(WIDTH, 10)) as pilot:
        await settled(pilot, area)
        calls = count_measures(monkeypatch)
        assert area.search(MARK, backward=True)
        await wait_until(pilot, lambda: not area.searching and area.cursor_location[0] == area.line_count - 1)
        await pilot.pause(0.2)
        assert len(calls) <= MAX_MEASURES, len(calls)
        assert area.virtual_size.height >= lazy_wrapped(area).height


@pytest.mark.asyncio
async def test_wrap_off_scroll_cursor_visible_does_not_refresh_the_size(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    area = NovaTextArea.open(make_file(tmp_path), soft_wrap=False)
    async with HostApp(area).run_test(size=(WIDTH, 10)) as pilot:
        await settled(pilot, area)
        area.move_cursor((area.line_count - 1, 0))
        await pilot.pause(0.2)
        refreshes: list[int] = []
        original = NovaTextArea._refresh_size

        def counting(self: NovaTextArea) -> None:
            refreshes.append(1)
            original(self)

        monkeypatch.setattr(NovaTextArea, "_refresh_size", counting)
        area.scroll_cursor_visible()
        assert refreshes == []


def count_height_reads(monkeypatch: pytest.MonkeyPatch) -> list[int]:
    """Record every read of `LazyWrappedDocument.height`."""
    reads: list[int] = []
    getter = LazyWrappedDocument.height.fget
    assert getter is not None

    def counting(self: LazyWrappedDocument) -> int:
        reads.append(1)
        return getter(self)

    monkeypatch.setattr(LazyWrappedDocument, "height", property(counting))
    return reads


@pytest.mark.asyncio
async def test_cursor_inside_the_virtual_size_does_not_read_the_wrapped_height(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    area = NovaTextArea.open(make_file(tmp_path), soft_wrap=True)
    async with HostApp(area).run_test(size=(WIDTH, 10)) as pilot:
        await settled(pilot, area)
        area.move_cursor((3, 0))
        await pilot.pause(0.1)
        assert area.virtual_size.height > 10
        reads = count_height_reads(monkeypatch)
        area.scroll_cursor_visible()
        assert reads == []


@pytest.mark.asyncio
async def test_cursor_beyond_the_virtual_size_reads_the_wrapped_height(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    area = NovaTextArea.open(make_file(tmp_path), soft_wrap=True)
    async with HostApp(area).run_test(size=(WIDTH, 10)) as pilot:
        await settled(pilot, area)
        area.selection = Selection.cursor((area.line_count - 1, 0))
        area.virtual_size = Size(0, 5)
        reads = count_height_reads(monkeypatch)
        area.scroll_cursor_visible()
        assert reads != []


@pytest.mark.asyncio
async def test_refresh_size_does_not_scroll_the_cursor_visible(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    area = NovaTextArea.open(make_file(tmp_path), soft_wrap=True)
    async with HostApp(area).run_test(size=(WIDTH, 10)) as pilot:
        await settled(pilot, area)
        scrolls: list[int] = []

        original = NovaTextArea.scroll_cursor_visible

        def counting(self: NovaTextArea, center: bool = False, animate: bool = False) -> Offset:
            scrolls.append(1)
            return original(self, center, animate)

        monkeypatch.setattr(NovaTextArea, "scroll_cursor_visible", counting)
        area._refresh_size()
        assert scrolls == []


@pytest.mark.asyncio
async def test_empty_document_scroll_cursor_visible_with_wrap(tmp_path: Path) -> None:
    path = tmp_path / "empty.txt"
    path.write_text("")
    area = NovaTextArea.open(path, soft_wrap=True)
    async with HostApp(area).run_test(size=(WIDTH, 10)) as pilot:
        await pilot.pause(0.1)
        assert area.scroll_cursor_visible() == Offset(0, 0)
        assert area.cursor_location == (0, 0)


@pytest.mark.asyncio
async def test_wrap_off_scroll_cursor_visible_does_not_read_the_wrapped_height(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    area = NovaTextArea.open(make_file(tmp_path), soft_wrap=False)
    async with HostApp(area).run_test(size=(WIDTH, 10)) as pilot:
        await settled(pilot, area)
        area.move_cursor((area.line_count - 1, 0))
        await pilot.pause(0.2)
        reads = count_height_reads(monkeypatch)
        area.scroll_cursor_visible()
        assert reads == []
