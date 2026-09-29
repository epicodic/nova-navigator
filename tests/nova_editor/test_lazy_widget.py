"""Tests of `NovaTextArea.open`: lazy read-only documents, windowed rendering, lifecycle (ACT3 design 4.5, 6)."""

from __future__ import annotations

import threading
import time
from collections.abc import Callable
from pathlib import Path

import pytest
from rich.cells import cell_len
from textual.pilot import Pilot

from nova_editor.core import ByteSource, PreadSource, SourceChanged
from nova_editor.document._lazy_config import LazyConfig
from nova_editor.document._lazy_document import MAX_WINDOW_CHARS, LazyDocument
from nova_editor.widget import NovaTextArea
from tests.nova_editor.helpers_view import (
    LOWERED_OPTIONS,
    HostApp,
    lazy_wrapped,
    make_mixed,
    oracle_display_column,
    oracle_row_text,
    oracle_section,
    oracle_window,
    row_strip_text,
    wait_frontier,
)

_LONG_ROW = 12
_MEDIUM_ROW = 11


def _config() -> LazyConfig:
    return LazyConfig(**LOWERED_OPTIONS)


async def _until(pilot: Pilot[None], condition: Callable[[], bool], limit: float = 10.0) -> None:
    """Let the app run until `condition()` holds."""
    deadline = time.monotonic() + limit
    while not condition():
        assert time.monotonic() < deadline, "condition not reached"
        await pilot.pause(0.02)


async def _settle(pilot: Pilot[None], area: NovaTextArea, row: int) -> LazyDocument:
    """Show `row`, wait until its whole scan is done and the final size estimate is applied; return the document."""
    doc = area.document
    assert isinstance(doc, LazyDocument)
    assert doc.wait_indexed(10)
    area.scroll_to(y=lazy_wrapped(area).y_of_row(row), animate=False)
    await pilot.pause()  # rendering the row starts its long index
    wait_frontier(doc, row)
    await _until(pilot, lambda: not area.is_estimating)
    return doc


class SpySource:
    """`ByteSource` that counts closes, can slow the scan down and can fail like a changed file."""

    def __init__(self, path: Path, *, scan_delay: float = 0.0) -> None:
        self._inner = PreadSource(path)
        self._scan_delay = scan_delay
        self.closes = 0
        self.reads_after_close = 0
        self.failing = False
        self._lock = threading.Lock()

    def length(self) -> int:
        return self._inner.length()

    def read(self, offset: int, size: int, *, cache: bool = True) -> bytes:
        with self._lock:
            if self.closes:
                self.reads_after_close += 1
        if self.failing:
            msg = "file changed"
            raise SourceChanged(msg)
        if not cache and self._scan_delay:
            time.sleep(self._scan_delay)
        return self._inner.read(offset, size, cache=cache)

    def close(self) -> None:
        with self._lock:
            self.closes += 1
        self._inner.close()


def _as_source(spy: SpySource) -> ByteSource:
    return spy


@pytest.mark.asyncio
async def test_open_lazy_file_renders_first_rows_read_only(tmp_path: Path) -> None:
    path = make_mixed(tmp_path / "m.txt")
    area = NovaTextArea.open(path, config=_config())
    app = HostApp(area)
    async with app.run_test(size=(60, 12)) as pilot:
        await pilot.pause()
        assert area.is_lazy
        assert area.read_only
        first = "".join(seg.text for seg in area.render_line(0))
        assert first.startswith(oracle_row_text(path.read_bytes(), 0)[:10])
        await pilot.press("a")  # typing is ignored while lazy
        assert area.document.get_line(0) == oracle_row_text(path.read_bytes(), 0)
        assert area.text == ""
        with pytest.raises(RuntimeError):
            area.load_text("x")
        area.read_only = False
        assert area.read_only
        assert area.find_matching_bracket("(", (0, 0)) is None


@pytest.mark.asyncio
async def test_long_row_window_matches_oracle_at_columns(tmp_path: Path) -> None:
    path = make_mixed(tmp_path / "m.txt", long_chars=6000)
    data = path.read_bytes()
    area = NovaTextArea.open(path, config=_config())
    async with HostApp(area).run_test(size=(40, 10)) as pilot:
        await pilot.pause()
        doc = area.document
        assert isinstance(doc, LazyDocument)
        assert doc.wait_indexed(10)
        row = next(r for r in range(doc.line_count) if doc.is_long(r))
        assert row == _LONG_ROW
        await _settle(pilot, area, row)
        area.scroll_to(y=row, animate=False)
        for x in (0, 37, 500, 1999, 2048, 4000):
            area.scroll_to(x=x, animate=False)
            await pilot.pause()
            strip = row_strip_text(area, row)
            width = area.scrollable_content_region.width
            assert strip == oracle_window(data, row, x, width), x
            assert "�" not in strip


@pytest.mark.asyncio
async def test_window_starting_inside_a_sequence_never_shows_a_replacement_character(tmp_path: Path) -> None:
    path = make_mixed(tmp_path / "m.txt", long_chars=1200)
    data = path.read_bytes()
    area = NovaTextArea.open(path, config=_config())
    async with HostApp(area).run_test(size=(30, 10)) as pilot:
        await pilot.pause()
        doc = area.document
        assert isinstance(doc, LazyDocument)
        await _settle(pilot, area, _LONG_ROW)
        area.scroll_to(y=_LONG_ROW, animate=False)
        for x in [*range(12), 33, 61, 97]:
            area.scroll_to(x=x, animate=False)
            await pilot.pause()
            strip = row_strip_text(area, _LONG_ROW)
            assert "�" not in strip
            assert strip == oracle_window(data, _LONG_ROW, x, area.scrollable_content_region.width), x


@pytest.mark.asyncio
async def test_wrap_mode_renders_a_long_row_section_like_the_grid_oracle(tmp_path: Path) -> None:
    path = make_mixed(tmp_path / "m.txt", long_chars=3000)
    data = path.read_bytes()
    area = NovaTextArea.open(path, soft_wrap=True, config=_config())
    async with HostApp(area).run_test(size=(40, 10)) as pilot:
        await pilot.pause()
        doc = area.document
        assert isinstance(doc, LazyDocument)
        await _settle(pilot, area, _LONG_ROW)
        grid = area.wrap_width
        view = area.scrollable_content_region.width
        sections = lazy_wrapped(area).row_sections(_LONG_ROW)
        for section in (0, 1, 2, sections // 2, sections - 1):
            area.scroll_to(y=lazy_wrapped(area).y_of_row(_LONG_ROW) + section, animate=False)
            await pilot.pause()
            y = lazy_wrapped(area).y_of_row(_LONG_ROW) + section - area.scroll_offset.y
            strip = area.render_line(y).crop(0, view).text
            assert strip == oracle_section(data, _LONG_ROW, section, grid, view), section


@pytest.mark.asyncio
async def test_medium_row_renders_in_full(tmp_path: Path) -> None:
    path = make_mixed(tmp_path / "m.txt")
    data = path.read_bytes()
    area = NovaTextArea.open(path, config=_config())
    async with HostApp(area).run_test(size=(330, 20)) as pilot:
        await pilot.pause()
        doc = area.document
        assert isinstance(doc, LazyDocument)
        assert doc.row_class(_MEDIUM_ROW) == "medium"
        width = area.scrollable_content_region.width
        assert row_strip_text(area, _MEDIUM_ROW) == oracle_window(data, _MEDIUM_ROW, 0, width)
        assert area.get_line(_MEDIUM_ROW).plain == oracle_row_text(data, _MEDIUM_ROW)


@pytest.mark.asyncio
async def test_get_line_of_a_long_row_is_a_bounded_prefix(tmp_path: Path) -> None:
    path = make_mixed(tmp_path / "m.txt", long_chars=6000)
    area = NovaTextArea.open(path, config=_config())
    async with HostApp(area).run_test(size=(40, 10)) as pilot:
        await pilot.pause()
        doc = area.document
        assert isinstance(doc, LazyDocument)
        wait_frontier(doc, _LONG_ROW)
        prefix = area.get_line(_LONG_ROW).plain
        assert prefix == oracle_row_text(path.read_bytes(), _LONG_ROW)[:1024]


@pytest.mark.asyncio
async def test_cursor_style_is_shifted_into_the_window(tmp_path: Path) -> None:
    path = make_mixed(tmp_path / "m.txt", long_chars=3000)
    data = path.read_bytes()
    area = NovaTextArea.open(path, config=_config())
    async with HostApp(area).run_test(size=(40, 10)) as pilot:
        await pilot.pause()
        doc = area.document
        assert isinstance(doc, LazyDocument)
        await _settle(pilot, area, _LONG_ROW)
        area.focus()
        await pilot.pause()
        column = 1500
        area.scroll_to(y=_LONG_ROW, animate=False)
        area.move_cursor((_LONG_ROW, column))
        await pilot.pause()
        expected = oracle_display_column(oracle_row_text(data, _LONG_ROW), column) - area.scroll_offset.x
        y = _LONG_ROW - area.scroll_offset.y
        cursor_style = area._theme.cursor_style
        assert cursor_style is not None
        offset = 0
        found = -1
        for segment in area.render_line(y):
            if found < 0 and segment.style is not None and segment.style.bgcolor == cursor_style.bgcolor and segment.text.strip():
                found = offset
            offset += cell_len(segment.text)
        assert found - area.gutter_width == expected


@pytest.mark.asyncio
async def test_close_is_idempotent_and_unmount_closes_the_source_while_a_scan_runs(tmp_path: Path) -> None:
    path = tmp_path / "big.txt"
    path.write_bytes(b"line\n" * 800_000)  # four scan blocks
    spy = SpySource(path, scan_delay=0.2)
    area = NovaTextArea.open(_as_source(spy), config=_config())
    doc = area.document
    assert isinstance(doc, LazyDocument)
    async with HostApp(area).run_test() as pilot:
        await pilot.pause()
        assert doc.is_growing()
    assert spy.closes == 1
    assert not doc.is_growing()
    area.close()
    area.close()
    assert spy.closes == 1
    assert spy.reads_after_close == 0


@pytest.mark.asyncio
async def test_close_before_mount_is_safe(tmp_path: Path) -> None:
    spy = SpySource(make_mixed(tmp_path / "m.txt"))
    area = NovaTextArea.open(_as_source(spy), config=_config())
    area.close()
    area.close()
    assert spy.closes == 1
    async with HostApp(area).run_test() as pilot:
        await pilot.pause()
    assert spy.closes == 1


def test_soft_wrap_default_false(tmp_path: Path) -> None:
    area = NovaTextArea.open(make_mixed(tmp_path / "m.txt"))
    assert area.soft_wrap is False
    area.close()
    wrapping = NovaTextArea.open(make_mixed(tmp_path / "w.txt"), soft_wrap=True)
    assert wrapping.soft_wrap is True
    wrapping.close()


def test_open_passes_the_widget_tab_width_to_the_document(tmp_path: Path) -> None:
    area = NovaTextArea.open(make_mixed(tmp_path / "m.txt"), config=LazyConfig(tab_width=7))
    assert isinstance(area.document, LazyDocument)
    assert area.document.tab_width == area.indent_width
    area.close()


def test_stock_text_area_is_unchanged() -> None:
    area = NovaTextArea(text="a\nb\tc")
    assert not area.is_lazy
    assert area.text == "a\nb\tc"
    assert not area.read_only
    area.load_text("x")
    assert area.text == "x"
    area.close()  # a no-op for stock documents
    assert area.text == "x"


@pytest.mark.asyncio
async def test_rendering_a_screen_over_a_huge_row_decodes_at_most_one_window(tmp_path: Path) -> None:
    path = make_mixed(tmp_path / "m.txt", long_chars=300_000)
    area = NovaTextArea.open(path, config=_config())
    async with HostApp(area).run_test(size=(60, 20)) as pilot:
        await pilot.pause()
        doc = area.document
        assert isinstance(doc, LazyDocument)
        await _settle(pilot, area, _LONG_ROW)
        for x in (0, 1000, 150_000, 250_000):
            area.scroll_to(x=x, y=0, animate=False)
            await pilot.pause()
            for y in range(area.size.height):
                area.render_line(y)
        area.soft_wrap = True
        await pilot.pause()
        area.scroll_to(y=lazy_wrapped(area).y_of_row(_LONG_ROW), animate=False)
        await pilot.pause()
        for y in range(area.size.height):
            area.render_line(y)
        assert doc.call_log.max_decoded_chars <= MAX_WINDOW_CHARS
        assert not doc.call_log.refusals


@pytest.mark.asyncio
async def test_source_changed_renders_blank_rows_and_posts_one_message(tmp_path: Path) -> None:
    spy = SpySource(make_mixed(tmp_path / "m.txt", long_chars=3000))
    area = NovaTextArea.open(_as_source(spy), config=_config())
    app = HostApp(area)
    async with app.run_test(size=(40, 10)) as pilot:
        await pilot.pause()
        doc = area.document
        assert isinstance(doc, LazyDocument)
        await _settle(pilot, area, _LONG_ROW)
        area.scroll_to(y=_LONG_ROW, animate=False)
        await pilot.pause()
        assert row_strip_text(area, _LONG_ROW).strip()
        spy.failing = True
        for x in (5, 900, 1800):
            area.scroll_to(x=x, animate=False)
            await pilot.pause()
            assert row_strip_text(area, _LONG_ROW).strip() == ""
        area.focus()
        await pilot.press("right", "down", "end", "pagedown")
        await _until(pilot, lambda: bool(app.source_changed))
        await pilot.pause(0.1)
        assert len(app.source_changed) == 1


@pytest.mark.asyncio
async def test_estimate_timer_stops_when_every_scan_completed(tmp_path: Path) -> None:
    path = make_mixed(tmp_path / "m.txt", long_chars=6000)
    area = NovaTextArea.open(path, config=_config())
    async with HostApp(area).run_test(size=(40, 10)) as pilot:
        await pilot.pause()
        doc = area.document
        assert isinstance(doc, LazyDocument)
        await _settle(pilot, area, _LONG_ROW)
        assert not doc.is_growing()
        exact = oracle_display_column(oracle_row_text(path.read_bytes(), _LONG_ROW), 10**9)
        assert area.virtual_size.width == exact + area.gutter_width + 1  # the final estimate was applied


@pytest.mark.asyncio
async def test_navigation_over_short_and_medium_rows(tmp_path: Path) -> None:
    path = make_mixed(tmp_path / "m.txt")
    data = path.read_bytes()
    area = NovaTextArea.open(path, config=_config())
    async with HostApp(area).run_test(size=(60, 12)) as pilot:
        await pilot.pause()
        doc = area.document
        assert isinstance(doc, LazyDocument)
        assert doc.wait_indexed(10)
        area.focus()
        await pilot.pause()
        await pilot.press("down", "down", "down")
        assert area.cursor_location == (3, 0)
        await pilot.press("right", "right")
        assert area.cursor_location == (3, 2)
        await pilot.press("left")
        assert area.cursor_location == (3, 1)
        await pilot.press("end")
        assert area.cursor_location == (3, len(oracle_row_text(data, 3)))
        await pilot.press("home")
        assert area.cursor_location == (3, 0)
        await pilot.press("up")
        assert area.cursor_location == (2, 0)
        await pilot.press("pagedown")
        assert area.cursor_location[0] > 2
        await pilot.press("pageup")
        assert area.cursor_location[0] < 6
        area.move_cursor((_MEDIUM_ROW, 0))
        await pilot.press("end")
        assert area.cursor_location == (_MEDIUM_ROW, len(oracle_row_text(data, _MEDIUM_ROW)))
        await pilot.press("home")
        assert area.cursor_location == (_MEDIUM_ROW, 0)


@pytest.mark.asyncio
async def test_navigation_across_wrapped_medium_row(tmp_path: Path) -> None:
    path = make_mixed(tmp_path / "m.txt")
    area = NovaTextArea.open(path, soft_wrap=True, config=_config())
    async with HostApp(area).run_test(size=(40, 12)) as pilot:
        await pilot.pause()
        doc = area.document
        assert isinstance(doc, LazyDocument)
        assert doc.wait_indexed(10)
        area.focus()
        area.move_cursor((_MEDIUM_ROW, 0))
        await pilot.pause()
        sections = lazy_wrapped(area).row_sections(_MEDIUM_ROW)
        assert sections > 3
        for step in range(1, sections):
            await pilot.press("down")
            assert area.cursor_location[0] == _MEDIUM_ROW, step
            assert area.cursor_location[1] > 0
        await pilot.press("up")
        assert area.cursor_location[0] == _MEDIUM_ROW
        assert lazy_wrapped(area).location_to_offset(area.cursor_location).y == lazy_wrapped(area).y_of_row(_MEDIUM_ROW) + sections - 2
