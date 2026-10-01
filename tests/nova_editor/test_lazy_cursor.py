"""Tests of the provisional byte-anchored cursor on long rows in `NovaTextArea` (ACT3 design 7)."""

from __future__ import annotations

import sys
import time
from collections.abc import AsyncIterator, Callable
from contextlib import asynccontextmanager
from pathlib import Path

import pytest
from textual.cache import LRUCache
from textual.pilot import Pilot
from textual.strip import Strip

from nova_editor.core import ByteSource
from nova_editor.document._cursor_anchor import CursorState, Op
from nova_editor.document._lazy_config import LazyConfig
from nova_editor.document._lazy_document import MAX_WINDOW_CHARS, LazyDocument
from nova_editor.widget import NovaTextArea
from tests.nova_editor.helpers_view import (
    LOWERED_OPTIONS,
    HostApp,
    TrickleSource,
    lazy_wrapped,
    make_mixed,
    oracle_display_column,
    oracle_row_ranges,
    oracle_row_text,
    row_strip_text,
    wait_frontier,
)

_MEDIUM_ROW = 11
_LONG_ROW = 12
_TAB = 4
_RESOLVED = CursorState.RESOLVED
_PROVISIONAL = CursorState.PROVISIONAL
_PENDING = CursorState.PENDING


def _config() -> LazyConfig:
    return LazyConfig(**LOWERED_OPTIONS)


def _as_source(source: TrickleSource) -> ByteSource:
    return source


async def _until(pilot: Pilot[None], condition: Callable[[], bool], limit: float = 10.0) -> None:
    """Let the app run until `condition()` holds."""
    deadline = time.monotonic() + limit
    while not condition():
        assert time.monotonic() < deadline, "condition not reached"
        await pilot.pause(0.02)


async def _idle(pilot: Pilot[None], area: NovaTextArea) -> None:
    """Wait until the size re-estimate of the widget has come to rest."""
    await _until(pilot, lambda: not area.is_estimating)


def _detab_tail(path: Path, row: int, nbytes: int = 800) -> None:
    """Replace the tabs in the last bytes of `row` by spaces (same length): the tab phase of a provisional window is an estimate."""
    data = bytearray(path.read_bytes())
    found = oracle_row_ranges(bytes(data))[row]
    start = max(found.start, found.content_end - nbytes)
    data[start : found.content_end] = data[start : found.content_end].replace(b"\t", b" ")
    path.write_bytes(bytes(data))


def _row_bytes(text: str, column: int) -> int:
    return len(text[:column].encode("utf-8", "surrogateescape"))


class _Rig:
    """A widget over a partly scanned file: the long-row scan is held back after `budget` reads."""

    def __init__(self, path: Path, *, budget: int = 2, wrap: bool = False) -> None:
        self.path = path
        self.data = path.read_bytes()
        self.source = TrickleSource(path, budget=budget)
        self.area = NovaTextArea.open(_as_source(self.source), soft_wrap=wrap, config=_config())
        self.app = HostApp(self.area)

    @property
    def doc(self) -> LazyDocument:
        doc = self.area.document
        assert isinstance(doc, LazyDocument)
        return doc

    def _laid_out(self) -> bool:
        """The first layout (size, scroll bars) is done: a file taller than the view can scroll."""
        area = self.area
        return area.size.height > 0 and (area.max_scroll_y > 0 or self.doc.line_count <= area.content_size.height)

    @asynccontextmanager
    async def run(self) -> AsyncIterator[Pilot[None]]:
        """Run the app; the held-back scan is always released at the end."""
        async with self.app.run_test(size=(40, 10)) as pilot:
            try:
                yield pilot
            finally:
                self.source.release()

    async def enter(self, pilot: Pilot[None], row: int) -> None:
        """Put the cursor at the start of `row` once the line scan is done, and wait until the long scan is held back."""
        await pilot.pause()
        assert self.doc.wait_indexed(10)
        await _until(pilot, self._laid_out)
        self.area.move_cursor((row, 0))
        await pilot.pause()
        if self.source.blocked.is_set() or self.doc.is_long(row):
            await _until(pilot, self.source.blocked.is_set)


def _mixed(tmp_path: Path) -> Path:
    path = make_mixed(tmp_path / "m.txt", long_chars=6000)
    _detab_tail(path, _LONG_ROW)
    return path


@pytest.mark.asyncio
async def test_end_is_provisional_then_resolves_without_visual_change(tmp_path: Path) -> None:
    rig = _Rig(_mixed(tmp_path))
    area = rig.area
    async with rig.run() as pilot:
        await rig.enter(pilot, _LONG_ROW)
        assert area.cursor_state is _RESOLVED
        started = time.perf_counter()
        await pilot.press("end")
        assert time.perf_counter() - started < 5.0  # smoke bound only; the structural assertions below are the test
        found = oracle_row_ranges(rig.data)[_LONG_ROW]
        assert area.cursor_state is _PROVISIONAL
        assert not area.column_exact
        assert area.cursor_byte_offset == found.content_end
        assert rig.source.blocked.is_set()
        before = (row_strip_text(area, _LONG_ROW), area.cursor_screen_offset)
        region = area.content_region
        assert region.x <= before[1].x < region.x + region.width
        rig.source.release()
        await _until(pilot, lambda: area.cursor_state is _RESOLVED)
        await pilot.pause()
        after = (row_strip_text(area, _LONG_ROW), area.cursor_screen_offset)
        assert before == after
        assert area.column_exact
        assert area.cursor_location == (_LONG_ROW, len(oracle_row_text(rig.data, _LONG_ROW)))
        assert area.cursor_byte_offset == found.content_end
        assert area.pending_progress is None


@pytest.mark.asyncio
async def test_provisional_window_shows_the_end_of_the_row(tmp_path: Path) -> None:
    rig = _Rig(_mixed(tmp_path))
    area = rig.area
    async with rig.run() as pilot:
        await rig.enter(pilot, _LONG_ROW)
        await pilot.press("end")
        assert area.cursor_state is _PROVISIONAL
        text = oracle_row_text(rig.data, _LONG_ROW)
        width = area.scrollable_content_region.width - area.gutter_width
        shown = row_strip_text(area, _LONG_ROW).rstrip()
        assert shown
        assert text.rstrip().endswith(shown.strip())
        assert "\N{REPLACEMENT CHARACTER}" not in shown
        assert width > 0


@pytest.mark.asyncio
async def test_up_from_provisional_repaints_progress_immediately(tmp_path: Path) -> None:
    rig = _Rig(_mixed(tmp_path))
    area = rig.area
    progress: list[float] = []

    class Spy(HostApp):
        def on_nova_text_area_jump_progress(self, message: NovaTextArea.JumpProgress) -> None:
            progress.append(message.fraction)

    rig.app = Spy(area)
    async with rig.run() as pilot:
        await rig.enter(pilot, _LONG_ROW)
        await pilot.press("end")
        started = time.perf_counter()
        await pilot.press("up")
        assert time.perf_counter() - started < 5.0  # smoke bound only; the structural assertions below are the test
        assert area.pending_progress is not None
        assert 0.0 <= area.pending_progress < 1.0
        assert area.cursor_state is _PENDING
        assert area.cursor_location[0] == _LONG_ROW
        assert progress
        rig.source.release()
        await _until(pilot, lambda: area.cursor_location[0] == _MEDIUM_ROW)
        assert area.cursor_state is _RESOLVED
        assert area.pending_progress is None


@pytest.mark.asyncio
async def test_cancel_of_a_deferred_op_keeps_the_provisional_end(tmp_path: Path) -> None:
    rig = _Rig(_mixed(tmp_path))
    area = rig.area
    async with rig.run() as pilot:
        await rig.enter(pilot, _LONG_ROW)
        await pilot.press("right", "right", "end", "down")
        assert area.cursor_state is _PENDING
        end = oracle_row_ranges(rig.data)[_LONG_ROW].content_end
        area.cancel_pending()
        await pilot.pause()
        assert area.pending_progress is None
        assert area.cursor_state is _PROVISIONAL
        assert area.cursor_byte_offset == end
        assert area.cursor_location[0] == _LONG_ROW
        rig.source.release()
        await _until(pilot, lambda: area.cursor_state is _RESOLVED)
        assert area.cursor_location == (_LONG_ROW, len(oracle_row_text(rig.data, _LONG_ROW)))


@pytest.mark.asyncio
async def test_cancel_of_a_wrap_jump_restores_the_previous_position(tmp_path: Path) -> None:
    rig = _Rig(_mixed(tmp_path), budget=0, wrap=True)
    area = rig.area
    async with rig.run() as pilot:
        await rig.enter(pilot, _LONG_ROW)
        await pilot.press("end")
        assert area.cursor_state is _PENDING
        area.cancel_pending()
        await pilot.pause()
        assert area.pending_progress is None
        assert area.cursor_state is _RESOLVED
        assert area.cursor_location == (_LONG_ROW, 0)


@pytest.mark.asyncio
async def test_wrap_toggle_keeps_cursor_on_the_same_byte_on_every_row_class(tmp_path: Path) -> None:
    path = make_mixed(tmp_path / "m.txt", long_chars=6000)
    data = path.read_bytes()
    for row in (3, _MEDIUM_ROW, _LONG_ROW):
        area = NovaTextArea.open(path, config=_config())
        async with HostApp(area).run_test(size=(40, 10)) as pilot:
            await pilot.pause()
            doc = area.document
            assert isinstance(doc, LazyDocument)
            assert doc.wait_indexed(10)
            area.move_cursor((row, 37 if row != 3 else 5))
            await pilot.pause()
            if doc.is_long(row):
                wait_frontier(doc, row)
                await _idle(pilot, area)
            found = oracle_row_ranges(data)[row]
            expected = found.start + _row_bytes(oracle_row_text(data, row), area.cursor_location[1])
            assert area.cursor_byte_offset == expected
            for _ in range(2):
                area.toggle_wrap()
                await pilot.pause()
                assert area.cursor_byte_offset == expected
                assert area.cursor_state is _RESOLVED
                assert area.cursor_location[0] == row


@pytest.mark.asyncio
async def test_wrap_toggle_on_a_provisional_cursor_waits_for_the_scan(tmp_path: Path) -> None:
    rig = _Rig(_mixed(tmp_path))
    area = rig.area
    async with rig.run() as pilot:
        await rig.enter(pilot, _LONG_ROW)
        await pilot.press("end")
        byte = area.cursor_byte_offset
        assert area.cursor_state is _PROVISIONAL
        area.toggle_wrap()
        await pilot.pause()
        assert area.cursor_state is _PENDING
        assert area.cursor_byte_offset == byte
        assert area.pending_progress is not None
        area.toggle_wrap()
        await pilot.pause()
        assert area.cursor_state is _PROVISIONAL
        assert area.cursor_byte_offset == byte
        area.toggle_wrap()
        await pilot.pause()
        rig.source.release()
        await _until(pilot, lambda: area.cursor_state is _RESOLVED)
        assert area.cursor_byte_offset == byte
        assert area.cursor_location == (_LONG_ROW, len(oracle_row_text(rig.data, _LONG_ROW)))


@pytest.mark.asyncio
async def test_wrap_mode_end_beyond_the_frontier_is_pending_and_completes(tmp_path: Path) -> None:
    rig = _Rig(_mixed(tmp_path), budget=0, wrap=True)
    area = rig.area
    async with rig.run() as pilot:
        await rig.enter(pilot, _LONG_ROW)
        await pilot.press("end")
        assert area.cursor_state is _PENDING
        assert area.cursor_location == (_LONG_ROW, 0)
        assert area.pending_progress is not None
        rig.source.release()
        text = oracle_row_text(rig.data, _LONG_ROW)
        await _until(pilot, lambda: area.cursor_location == (_LONG_ROW, len(text)))
        await _until(pilot, lambda: area.cursor_state is _RESOLVED)
        assert area.pending_progress is None
        assert area.cursor_byte_offset == oracle_row_ranges(rig.data)[_LONG_ROW].content_end


@pytest.mark.asyncio
async def test_home_from_provisional_resolves_at_the_row_start(tmp_path: Path) -> None:
    rig = _Rig(_mixed(tmp_path))
    area = rig.area
    async with rig.run() as pilot:
        await rig.enter(pilot, _LONG_ROW)
        await pilot.press("end")
        assert area.cursor_state is _PROVISIONAL
        await pilot.press("home")
        assert area.cursor_state is _RESOLVED
        assert area.cursor_location == (_LONG_ROW, 0)
        assert area.cursor_byte_offset == oracle_row_ranges(rig.data)[_LONG_ROW].start
        assert area.scroll_offset.x == 0


@pytest.mark.asyncio
async def test_right_at_the_end_of_a_provisional_row_goes_to_the_next_row(tmp_path: Path) -> None:
    rig = _Rig(_mixed(tmp_path))
    area = rig.area
    async with rig.run() as pilot:
        await rig.enter(pilot, _LONG_ROW)
        await pilot.press("end", "right")
        assert area.cursor_location == (_LONG_ROW + 1, 0)
        assert area.cursor_state is _RESOLVED


_UNIT_A = "ab\t日本é😀xyz "
_UNIT_B = "\t\t日é̈ab 😀😀c"


@pytest.mark.asyncio
@pytest.mark.parametrize("unit", [_UNIT_A, _UNIT_B], ids=["mixed", "tabs"])
async def test_relative_moves_after_end_match_a_plain_string_cursor(tmp_path: Path, unit: str) -> None:
    text = unit * 120
    prefix = b"first\n"
    path = tmp_path / "rel.txt"
    path.write_bytes(prefix + text.encode() + b"\nlast\n")
    rig = _Rig(path, budget=1)
    area = rig.area
    moves = ["left"] * 5 + ["right"] * 2 + ["ctrl+left", "left", "left", "ctrl+right", "right", "right", "right", "ctrl+left", "left"]
    step = {"left": -1, "right": 1, "ctrl+left": -8, "ctrl+right": 8}
    async with rig.run() as pilot:
        await rig.enter(pilot, 1)
        await pilot.press("end")
        assert area.cursor_state is _PROVISIONAL
        reference = len(text)
        for key in moves:
            await pilot.press(key)
            reference = min(len(text), max(0, reference + step[key]))
            assert area.cursor_byte_offset == len(prefix) + _row_bytes(text, reference), key
        rig.source.release()
        await _until(pilot, lambda: area.cursor_state is _RESOLVED)
        assert area.cursor_location == (1, reference)
        assert area.cursor_byte_offset == len(prefix) + _row_bytes(text, reference)


@pytest.mark.asyncio
@pytest.mark.parametrize("wrap", [False, True], ids=["nowrap", "wrap"])
async def test_navigation_over_every_row_class_never_decodes_a_whole_row(tmp_path: Path, wrap: bool) -> None:
    path = make_mixed(tmp_path / "m.txt", long_chars=6000)
    area = NovaTextArea.open(path, soft_wrap=wrap, config=_config())
    async with HostApp(area).run_test(size=(40, 10)) as pilot:
        await pilot.pause()
        doc = area.document
        assert isinstance(doc, LazyDocument)
        assert doc.wait_indexed(10)
        keys = ["home", "end", "up", "down", "pageup", "pagedown", "left", "right", "ctrl+left", "ctrl+right", "shift+right", "end", "home"]
        for row in (0, 10, _MEDIUM_ROW, _LONG_ROW, _LONG_ROW + 1, doc.line_count - 1):
            area.move_cursor((row, 0))
            await pilot.pause()
            for key in keys:
                await pilot.press(key)
        assert not doc.call_log.refusals
        assert doc.call_log.max_decoded_chars <= MAX_WINDOW_CHARS


@pytest.mark.asyncio
async def test_provisional_navigation_never_decodes_a_whole_row(tmp_path: Path) -> None:
    rig = _Rig(_mixed(tmp_path))
    area = rig.area
    async with rig.run() as pilot:
        await rig.enter(pilot, _LONG_ROW)
        await pilot.press("end", "left", "ctrl+left", "right", "ctrl+right", "home", "end", "pageup", "down")
        rig.source.release()
        await _until(pilot, lambda: area.cursor_state is _RESOLVED and area.pending_progress is None)
        assert not rig.doc.call_log.refusals
        assert rig.doc.call_log.max_decoded_chars <= MAX_WINDOW_CHARS


def _cover(text: str, target: int) -> int:
    """Column of the character that covers display column `target` (the length when none does)."""
    disp = 0
    for column, char in enumerate(text):
        end = disp + (_TAB - disp % _TAB if char == "\t" else oracle_display_column(char, 1))
        if end > target:
            return column
        disp = end
    return len(text)


def _first_at_or_after(text: str, target: int) -> int:
    """First column whose display start is at or after `target` (the length when none)."""
    column = 0
    while column < len(text) and oracle_display_column(text, column) < target:
        column += 1
    return column


@pytest.mark.asyncio
async def test_resolved_navigation_without_wrap_matches_the_oracle(tmp_path: Path) -> None:
    path = make_mixed(tmp_path / "m.txt", long_chars=6000)
    data = path.read_bytes()
    area = NovaTextArea.open(path, config=_config())
    async with HostApp(area).run_test(size=(40, 10)) as pilot:
        await pilot.pause()
        doc = area.document
        assert isinstance(doc, LazyDocument)
        assert doc.wait_indexed(10)
        area.move_cursor((_LONG_ROW, 100))
        await pilot.pause()
        wait_frontier(doc, _LONG_ROW)
        await _idle(pilot, area)
        text = oracle_row_text(data, _LONG_ROW)
        x = oracle_display_column(text, 100)
        rows = doc.line_count

        def target(row: int) -> tuple[int, int]:
            return row, min(_cover(oracle_row_text(data, row), x), len(oracle_row_text(data, row)))

        for key, expected in (
            ("home", (_LONG_ROW, 0)),
            ("end", (_LONG_ROW, len(text))),
            ("up", target(_MEDIUM_ROW)),
            ("down", target(_LONG_ROW + 1)),
            ("pagedown", target(min(rows - 1, _LONG_ROW + area.content_size.height))),
            ("pageup", target(_LONG_ROW - area.content_size.height)),
        ):
            area.move_cursor((_LONG_ROW, 100))
            await pilot.pause()
            await pilot.press(key)
            assert area.cursor_location == expected, key
            assert area.cursor_state is _RESOLVED


@pytest.mark.asyncio
async def test_resolved_navigation_with_wrap_matches_the_oracle(tmp_path: Path) -> None:
    path = make_mixed(tmp_path / "m.txt", long_chars=6000)
    data = path.read_bytes()
    area = NovaTextArea.open(path, soft_wrap=True, config=_config())
    async with HostApp(area).run_test(size=(40, 10)) as pilot:
        await pilot.pause()
        doc = area.document
        assert isinstance(doc, LazyDocument)
        assert doc.wait_indexed(10)
        area.move_cursor((_LONG_ROW, 0))
        await pilot.pause()
        wait_frontier(doc, _LONG_ROW)
        await _idle(pilot, area)
        text = oracle_row_text(data, _LONG_ROW)
        grid = area.wrap_width
        assert lazy_wrapped(area).row_sections(_LONG_ROW) > 40
        start = _first_at_or_after(text, 20 * grid) + 3
        disp = oracle_display_column(text, start)
        section = disp // grid

        def section_start(index: int) -> int:
            return 0 if index == 0 else _first_at_or_after(text, index * grid)

        x = disp - oracle_display_column(text, section_start(section))

        def in_section(target_section: int) -> int:
            column = _cover(text, oracle_display_column(text, section_start(target_section)) + x)
            return max(min(column, section_start(target_section + 1) - 1), section_start(target_section))

        for key, expected in (
            ("home", section_start(section)),
            ("end", section_start(section + 1) - 1),
            ("up", in_section(section - 1)),
            ("down", in_section(section + 1)),
            ("pagedown", in_section(section + area.content_size.height)),
            ("pageup", in_section(section - area.content_size.height)),
        ):
            area.move_cursor((_LONG_ROW, start))
            await pilot.pause()
            await pilot.press(key)
            assert area.cursor_location == (_LONG_ROW, expected), key
            assert area.cursor_state is _RESOLVED


async def _click_long_row(rig: _Rig, pilot: Pilot[None]) -> None:
    """Show the long row (the scroll range follows the size estimate, so scroll until it is on screen) and click its fifth visible cell."""
    area = rig.area
    top = lazy_wrapped(area).y_of_row(_LONG_ROW)

    def shown() -> bool:
        area.scroll_to(x=0, y=top - 5, animate=False)
        return 0 <= top - area.scroll_offset.y < area.content_size.height

    await _until(pilot, shown)
    y = top - area.scroll_offset.y + area.gutter.top
    await pilot.click(area, offset=(area.gutter.left + area.gutter_width + 5, y))
    await pilot.pause()


@pytest.mark.asyncio
async def test_click_beyond_the_frontier_is_ignored(tmp_path: Path) -> None:
    rig = _Rig(_mixed(tmp_path), budget=0)
    area = rig.area
    async with rig.run() as pilot:
        await rig.enter(pilot, _LONG_ROW)
        area.move_cursor((_MEDIUM_ROW, 3))
        await pilot.pause()
        await _click_long_row(rig, pilot)
        assert area.cursor_location == (_MEDIUM_ROW, 3)


@pytest.mark.asyncio
async def test_covered_click_moves_the_cursor_and_a_provisional_row_ignores_clicks(tmp_path: Path) -> None:
    rig = _Rig(_mixed(tmp_path), budget=1)
    area = rig.area
    async with rig.run() as pilot:
        await rig.enter(pilot, _LONG_ROW)
        area.move_cursor((_MEDIUM_ROW, 3))
        await pilot.pause()
        await _click_long_row(rig, pilot)
        assert area.cursor_location == (_LONG_ROW, _cover(oracle_row_text(rig.data, _LONG_ROW), 5))
        await pilot.press("end")
        assert area.cursor_state is _PROVISIONAL
        location = area.cursor_location
        await _click_long_row(rig, pilot)
        assert area.cursor_location == location
        assert area.cursor_state is _PROVISIONAL


@pytest.mark.asyncio
async def test_scan_completion_is_one_pass_and_the_replay_follows_the_resolving_paint(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    """The progress callback that resolves a provisional cursor clears the line cache and refreshes once; the deferred op runs after the paint.

    The scan may announce its progress in several callbacks (a coalesced tick can follow the resolving one), so the callback under test is the
    one that moves the cursor from pending to resolved, not merely the last one before the replay. Textual reactives (`virtual_size`,
    `selection`, `scroll_x`) request their own repaints during that callback; the repaint flag coalesces them, so the callback itself must issue
    exactly one `refresh` of its own.
    """
    rig = _Rig(_mixed(tmp_path))
    area = rig.area
    log: list[str] = []

    def spy(name: str, original: Callable[..., object], *, bracket: bool = False) -> Callable[..., object]:
        def wrapper(*args: object, **kwargs: object) -> object:
            log.append(f"{name} (own)" if name == "refresh" and sys._getframe(1).f_code.co_name == "_on_index_progress" else name)
            before = area.cursor_state
            try:
                return original(*args, **kwargs)
            finally:
                if name == "_reconcile_cursor" and before is not _RESOLVED and area.cursor_state is _RESOLVED:
                    log.append("resolved")
                if bracket:
                    log.append(f"end {name}")

        return wrapper

    async with rig.run() as pilot:
        await rig.enter(pilot, _LONG_ROW)
        await pilot.press("end", "up")
        assert area.cursor_state is _PENDING
        monkeypatch.setattr(area, "_on_index_progress", spy("progress", area._on_index_progress, bracket=True))
        for name in ("_replay", "refresh", "render_line", "_reconcile_cursor", "_refresh_size"):
            monkeypatch.setattr(area, name, spy(name, getattr(area, name)))

        class SpyCache(LRUCache[tuple, Strip]):
            def clear(self) -> None:
                log.append("clear")
                super().clear()

        monkeypatch.setattr(area, "_line_cache", SpyCache(1024))
        rig.source.release()
        await _until(pilot, lambda: area.cursor_location[0] == _MEDIUM_ROW)
        await pilot.pause()
    resolved = log.index("resolved")
    start = max(i for i in range(resolved) if log[i] == "progress")
    end = log.index("end progress", resolved)
    call = log[start:end]
    replay = log.index("_replay")
    assert call.count("clear") == 1, call
    assert call.count("refresh (own)") == 1, call
    assert call.count("_reconcile_cursor") == 1, call
    assert call.count("_refresh_size") <= 1, call
    assert "render_line" not in call, "nothing paints inside the resolving callback"
    assert replay > end, "the deferred operation must not run inside the resolving callback"
    assert "render_line" in log[end:replay], "the resolving paint comes before the replay"


def _spy_replay(area: NovaTextArea, monkeypatch: pytest.MonkeyPatch, performed: list[Op]) -> None:
    """Record the deferred operations that the widget replays instead of performing them."""

    def record(op: Op, *, select: bool) -> None:
        assert select in (True, False)
        performed.append(op)

    monkeypatch.setattr(area, "_replay", record)


@pytest.mark.asyncio
async def test_cancel_between_the_resolving_paint_and_the_replay_drops_the_operation(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    rig = _Rig(_mixed(tmp_path))
    area = rig.area
    performed: list[Op] = []
    async with rig.run() as pilot:
        await pilot.pause()
        _spy_replay(area, monkeypatch, performed)
        area._schedule_replay((Op.DOWN, False))
        area.cancel_pending()
        await pilot.pause(0.1)
        assert performed == []
        area._schedule_replay((Op.UP, False))
        await pilot.pause(0.1)
        assert performed == [Op.UP]


@pytest.mark.asyncio
async def test_a_new_jump_between_the_resolving_paint_and_the_replay_drops_the_operation(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    rig = _Rig(_mixed(tmp_path))
    area = rig.area
    performed: list[Op] = []
    async with rig.run() as pilot:
        await pilot.pause()
        _spy_replay(area, monkeypatch, performed)
        area._schedule_replay((Op.DOWN, False))
        area.goto_line(2)
        await pilot.pause(0.1)
        assert performed == []


@pytest.mark.asyncio
async def test_cursor_actions_after_close_do_not_raise(tmp_path: Path) -> None:
    rig = _Rig(_mixed(tmp_path))
    area = rig.area
    async with rig.run() as pilot:
        await rig.enter(pilot, _LONG_ROW)
        await pilot.press("end")
        area.close()
        rig.source.release()
        for action in (area.action_cursor_down, area.action_cursor_up, area.action_cursor_line_end, area.action_cursor_left, area.action_cursor_right):
            action()
        area.move_cursor((0, 0))
