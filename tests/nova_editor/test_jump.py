"""Tests of goto, deferred jumps, progress, cancel and the index thread model (ACT3 design 8, 9)."""

from __future__ import annotations

import threading
from pathlib import Path

import pytest

from nova_editor.document._lazy_config import LazyConfig
from nova_editor.document._lazy_document import LazyDocument
from nova_editor.widget import NovaTextArea
from tests.nova_editor.helpers_view import (
    LOWERED_OPTIONS,
    HostApp,
    TrickleSource,
    gated_source,
    make_mixed,
    open_with_gated_line_scan,
    oracle_row_ranges,
    oracle_row_text,
    release_line_scan,
    wait_until,
)

_LONG_ROW = 12


def _config() -> LazyConfig:
    return LazyConfig(**LOWERED_OPTIONS)


def _scan_threads() -> list[str]:
    return [t.name for t in threading.enumerate() if t.name.startswith(("line-index-scan", "long-line-scan")) and t.is_alive()]


def _doc(area: NovaTextArea) -> LazyDocument:
    doc = area.document
    assert isinstance(doc, LazyDocument)
    return doc


@pytest.mark.asyncio
async def test_goto_line_past_frontier_shows_progress_then_completes(tmp_path: Path) -> None:
    area, _ = open_with_gated_line_scan(tmp_path, rows=3000)
    app = HostApp(area)
    async with app.run_test(size=(40, 10)) as pilot:
        await wait_until(pilot, gated_source(area).blocked.is_set)
        area.goto_line(2500)
        await pilot.pause()
        progress = area.pending_progress
        assert progress is not None
        assert 0.0 < progress < 1.0
        assert area.cursor_location == (0, 0)
        assert not area.line_count_exact
        assert not area.indexing_complete
        assert 1 < area.line_count < 2500
        release_line_scan(area)
        await wait_until(pilot, lambda: area.pending_progress is None)
        assert area.cursor_location == (2499, 0)
        await wait_until(pilot, lambda: area.indexing_complete)
        assert area.line_count == 3001
        assert area.line_count_exact
        assert area.indexing_complete
        await wait_until(pilot, lambda: "JumpCompleted" in app.names())
        assert "JumpProgress" in app.names()
        done = [m for m in app.messages if isinstance(m, NovaTextArea.JumpCompleted)]
        assert [(m.row, m.column) for m in done] == [(2499, 0)]


@pytest.mark.asyncio
async def test_goto_can_be_cancelled_with_escape(tmp_path: Path) -> None:
    area, _ = open_with_gated_line_scan(tmp_path, rows=3000)
    app = HostApp(area)
    async with app.run_test(size=(40, 10)) as pilot:
        await wait_until(pilot, gated_source(area).blocked.is_set)
        area.focus()
        area.goto_line(2500)
        await pilot.pause()
        assert area.pending_progress is not None
        await pilot.press("escape")
        await pilot.pause()
        assert area.pending_progress is None
        assert area.cursor_location == (0, 0)
        release_line_scan(area)
        assert _doc(area).wait_indexed(10)
        await wait_until(pilot, lambda: area.indexing_complete)
        await pilot.pause()
        assert area.cursor_location == (0, 0)  # a cancelled jump never completes later
        assert "JumpCompleted" not in app.names()


@pytest.mark.asyncio
async def test_escape_is_not_claimed_when_nothing_is_pending(tmp_path: Path) -> None:
    area, _ = open_with_gated_line_scan(tmp_path, rows=50, threshold=None)
    async with HostApp(area).run_test(size=(40, 10)) as pilot:
        await pilot.pause()
        assert area.check_action("cancel_pending", ()) is False


@pytest.mark.asyncio
async def test_a_new_request_cancels_the_pending_one(tmp_path: Path) -> None:
    area, _ = open_with_gated_line_scan(tmp_path, rows=3000)
    app = HostApp(area)
    async with app.run_test(size=(40, 10)) as pilot:
        await wait_until(pilot, gated_source(area).blocked.is_set)
        area.goto_line(2500)
        area.goto_line(2000)
        release_line_scan(area)
        await wait_until(pilot, lambda: area.pending_progress is None and area.indexing_complete)
        await pilot.pause()
        assert area.cursor_location == (1999, 0)
        assert [(m.row, m.column) for m in app.messages if isinstance(m, NovaTextArea.JumpCompleted)] == [(1999, 0)]


@pytest.mark.asyncio
async def test_goto_byte_inside_multibyte_character_lands_on_it(tmp_path: Path) -> None:
    path = tmp_path / "w.txt"
    path.write_bytes("ab日本語cd\n".encode())
    area = NovaTextArea.open(path, config=_config())
    async with HostApp(area).run_test() as pilot:
        await pilot.pause()
        area.goto_byte(4)  # last byte of 日 (bytes 2..4)
        await wait_until(pilot, lambda: area.cursor_location == (0, 2))
        assert area.cursor_byte_offset == 2
        await wait_until(pilot, lambda: area.indexing_complete)
        area.goto_byte(3)  # middle byte of 日: still 日
        assert area.cursor_location == (0, 2)
        area.goto_byte(8)  # first byte of 語
        assert area.cursor_location == (0, 4)


@pytest.mark.asyncio
@pytest.mark.parametrize("bad", [10_000, -1])
async def test_goto_out_of_range_is_rejected(tmp_path: Path, bad: int) -> None:
    path = make_mixed(tmp_path / "m.txt")
    area = NovaTextArea.open(path, config=_config())
    app = HostApp(area)
    async with app.run_test() as pilot:
        await pilot.pause()
        await wait_until(pilot, lambda: area.indexing_complete)
        area.move_cursor((3, 2))
        before = area.cursor_location
        area.goto_byte(bad)
        await wait_until(pilot, lambda: app.names().count("JumpRejected") == 1)
        area.goto_line(99_999)
        await wait_until(pilot, lambda: app.names().count("JumpRejected") == 2)
        area.goto_line(0)
        await wait_until(pilot, lambda: app.names().count("JumpRejected") == 3)
        area.goto_byte(len(path.read_bytes()))
        await wait_until(pilot, lambda: app.names().count("JumpRejected") == 4)
        assert area.cursor_location == before
        assert area.pending_progress is None
        assert all(m.reason for m in app.messages if isinstance(m, NovaTextArea.JumpRejected))


@pytest.mark.asyncio
async def test_unmount_with_running_scan_does_not_raise(tmp_path: Path) -> None:
    area, _ = open_with_gated_line_scan(tmp_path, rows=3000, threshold=None, delay=0.01)
    async with HostApp(area).run_test() as pilot:
        await wait_until(pilot, lambda: gated_source(area).reads > 1)
        assert not _doc(area).snapshot().complete
    assert not _scan_threads()


@pytest.mark.asyncio
async def test_callbacks_after_unmount_are_harmless(tmp_path: Path) -> None:
    area, _ = open_with_gated_line_scan(tmp_path, rows=3000, threshold=None, delay=0.002)
    async with HostApp(area).run_test() as pilot:
        await wait_until(pilot, lambda: gated_source(area).reads > 1)
    area._index_callback()  # a late scan callback reaches a closed widget
    assert not _scan_threads()


@pytest.mark.asyncio
async def test_goto_line_first_middle_and_last(tmp_path: Path) -> None:
    area, _ = open_with_gated_line_scan(tmp_path, rows=400, threshold=None)
    async with HostApp(area).run_test(size=(40, 10)) as pilot:
        await pilot.pause()
        await wait_until(pilot, lambda: area.indexing_complete)
        area.goto_line(200)
        assert area.cursor_location == (199, 0)
        area.goto_line(1)
        assert area.cursor_location == (0, 0)
        area.goto_line(400)
        assert area.cursor_location == (399, 0)
        assert area.pending_progress is None
        area.goto_line(401)  # the empty row after the final terminator
        assert area.cursor_location == (400, 0)


@pytest.mark.asyncio
async def test_goto_byte_in_a_short_row_of_a_complete_index(tmp_path: Path) -> None:
    path = make_mixed(tmp_path / "m.txt")
    data = path.read_bytes()
    area = NovaTextArea.open(path, config=_config())
    async with HostApp(area).run_test() as pilot:
        await pilot.pause()
        await wait_until(pilot, lambda: area.indexing_complete)
        ranges = oracle_row_ranges(data)
        area.goto_byte(ranges[4].start + 3)
        assert area.cursor_location == (4, 3)
        assert area.cursor_byte_offset == ranges[4].start + 3
        area.goto_byte(ranges[4].content_end)  # a terminator byte belongs to its row: the end of the row
        assert area.cursor_location == (4, len(oracle_row_text(data, 4)))


@pytest.mark.asyncio
async def test_goto_byte_in_a_long_row_is_provisional_without_wrap(tmp_path: Path) -> None:
    path = make_mixed(tmp_path / "m.txt", long_chars=6000)
    source = TrickleSource(path)
    area = NovaTextArea.open(source, config=_config())
    app = HostApp(area)
    async with app.run_test(size=(40, 10)) as pilot:
        try:
            await pilot.pause()
            await wait_until(pilot, lambda: area.indexing_complete)
            found = oracle_row_ranges(path.read_bytes())[_LONG_ROW]
            area.goto_line(_LONG_ROW + 1)
            await wait_until(pilot, source.blocked.is_set)
            target = found.start + 5000
            area.goto_byte(target)
            assert area.pending_progress is None
            assert not area.column_exact
            assert area.cursor_byte_offset == target
            assert area.cursor_location[0] == _LONG_ROW
            source.release()
            await wait_until(pilot, lambda: area.column_exact)
            assert area.cursor_byte_offset == target
            done = [m for m in app.messages if isinstance(m, NovaTextArea.JumpCompleted)]
            assert done
            assert done[-1].row == _LONG_ROW
        finally:
            source.release()


@pytest.mark.asyncio
async def test_goto_byte_in_a_long_row_is_pending_with_wrap_and_resolves_later(tmp_path: Path) -> None:
    path = make_mixed(tmp_path / "m.txt", long_chars=6000)
    source = TrickleSource(path)
    area = NovaTextArea.open(source, soft_wrap=True, config=_config())
    app = HostApp(area)
    async with app.run_test(size=(40, 10)) as pilot:
        try:
            await pilot.pause()
            await wait_until(pilot, lambda: area.indexing_complete)
            found = oracle_row_ranges(path.read_bytes())[_LONG_ROW]
            area.goto_line(_LONG_ROW + 1)
            await wait_until(pilot, source.blocked.is_set)
            before = area.cursor_location
            target = found.start + 5000
            area.goto_byte(target)
            progress = area.pending_progress
            assert progress is not None
            assert 0.0 <= progress < 1.0
            assert area.cursor_location == before
            source.release()
            await wait_until(pilot, lambda: area.pending_progress is None and area.cursor_byte_offset == target)
            assert area.column_exact
            assert area.cursor_location[0] == _LONG_ROW
            assert [m.row for m in app.messages if isinstance(m, NovaTextArea.JumpCompleted)][-1] == _LONG_ROW
        finally:
            source.release()


@pytest.mark.asyncio
async def test_index_messages_arrive_and_complete_once(tmp_path: Path) -> None:
    area, _ = open_with_gated_line_scan(tmp_path, rows=3000, threshold=1024)
    app = HostApp(area)
    async with app.run_test(size=(40, 10)) as pilot:
        await wait_until(pilot, gated_source(area).blocked.is_set)
        await wait_until(pilot, lambda: "IndexProgress" in app.names())
        assert "IndexingComplete" not in app.names()
        release_line_scan(area)
        await wait_until(pilot, lambda: "IndexingComplete" in app.names())
        await pilot.pause()
        assert app.names().count("IndexingComplete") == 1
        progress = [m for m in app.messages if isinstance(m, NovaTextArea.IndexProgress)]
        assert progress[-1].complete
        counts = [m.count for m in progress]
        assert counts == sorted(counts)


@pytest.mark.asyncio
async def test_frontier_callbacks_are_coalesced(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    area, _ = open_with_gated_line_scan(tmp_path, rows=20_000, threshold=1024)
    source = gated_source(area)
    doc = _doc(area)
    callbacks: list[int] = []
    ui_calls: list[int] = []
    doc.subscribe(lambda: callbacks.append(1))
    original = area._on_index_progress

    def counting() -> None:
        ui_calls.append(1)
        original()

    monkeypatch.setattr(area, "_on_index_progress", counting)
    async with HostApp(area).run_test(size=(40, 10)) as pilot:
        await wait_until(pilot, source.blocked.is_set)
        callbacks.clear()
        ui_calls.clear()
        source.release()
        assert doc.wait_indexed(30)
        await wait_until(pilot, lambda: area.indexing_complete)
        await pilot.pause()
        assert len(callbacks) > 300
        assert 1 <= len(ui_calls) < len(callbacks) // 3
