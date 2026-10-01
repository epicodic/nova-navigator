"""Pilot tests of editing a lazily opened document in `NovaTextArea` (ACT4 design 11.1, 11.2, REQ-13, DEC-23 condition 1)."""

from __future__ import annotations

from collections.abc import AsyncIterator
from contextlib import asynccontextmanager
from pathlib import Path

import pytest
from textual import events
from textual.pilot import Pilot

from nova_editor.core.pieces import Content
from nova_editor.document._cursor_anchor import CursorState
from nova_editor.document._document import EditResult
from nova_editor.document._lazy_config import LazyConfig
from nova_editor.document._lazy_document import LazyDocument, RowUnavailable
from nova_editor.widget import NovaTextArea
from tests.nova_editor.helpers_view import (
    LOWERED_OPTIONS,
    HostApp,
    TrickleSource,
    await_first_layout,
    make_mixed,
    row_strip_text,
    wait_frontier,
    wait_until,
)

_MEDIUM_ROW = 11
_LONG_ROW = 12
_REPLACEMENT = "\N{REPLACEMENT CHARACTER}"
_ESCAPE = "\udcff"


class _Host(HostApp):
    """Host that records the `EditRefused` messages of the widget."""

    def __init__(self, widget: NovaTextArea) -> None:
        super().__init__(widget)
        self.refused: list[str] = []
        self.bells = 0

    def on_nova_text_area_edit_refused(self, message: NovaTextArea.EditRefused) -> None:
        self.refused.append(message.reason)

    def bell(self) -> None:
        self.bells += 1


def _config() -> LazyConfig:
    return LazyConfig(**LOWERED_OPTIONS)


def _doc(area: NovaTextArea) -> LazyDocument:
    doc = area.document
    assert isinstance(doc, LazyDocument)
    return doc


def _bytes(area: NovaTextArea) -> bytes:
    doc = _doc(area)
    return doc.read_bytes(0, doc.length)


def _offset(text: str, location: tuple[int, int]) -> int:
    rows = text.split("\n")
    row, column = location
    return sum(len(r) + 1 for r in rows[:row]) + column


def _location(text: str, offset: int) -> tuple[int, int]:
    head = text[:offset].split("\n")
    return len(head) - 1, len(head[-1])


def _replace(text: str, start: int, end: int, inserted: str) -> str:
    return text[:start] + inserted + text[end:]


def _frame(area: NovaTextArea) -> None:
    """Render every visible line: an edit must leave a drawable view."""
    for y in range(area.size.height):
        area.render_line(y)


async def _until_resolved(pilot: Pilot[None], area: NovaTextArea) -> None:
    await wait_until(pilot, lambda: area.cursor_state is CursorState.RESOLVED)


@asynccontextmanager
async def _open(tmp_path: Path, *, wrap: bool, long_chars: int = 2000) -> AsyncIterator[tuple[NovaTextArea, _Host, Pilot[None], str]]:
    path = make_mixed(tmp_path / "m.txt", long_chars=long_chars)
    original = path.read_bytes().decode("utf-8", "surrogateescape")
    area = NovaTextArea.open(path, soft_wrap=wrap, config=_config())
    host = _Host(area)
    async with host.run_test(size=(60, 14)) as pilot:
        await await_first_layout(pilot, area)
        doc = _doc(area)
        assert doc.wait_indexed(10)
        wait_frontier(doc, _LONG_ROW)
        yield area, host, pilot, original


def _targets(text: str) -> dict[str, tuple[int, int]]:
    rows = text.split("\n")
    return {
        "short": (0, 3),
        "medium": (_MEDIUM_ROW, 150),
        "long": (_LONG_ROW, 400),
        "long_end": (_LONG_ROW, len(rows[_LONG_ROW])),
        "document_end": (len(rows) - 1, len(rows[-1])),
    }


async def _settle(pilot: Pilot[None], area: NovaTextArea) -> None:
    """Wait until every long row is indexed again (an edit that rebuilds an index leaves its columns unresolved until the scan is done)."""
    doc = _doc(area)
    for row in range(doc.line_count):
        if doc.is_long(row):
            assert doc.long_index(row).join(10)
    await pilot.pause()
    await _until_resolved(pilot, area)


async def _check(pilot: Pilot[None], area: NovaTextArea, model: str, cursor: tuple[int, int], step: str) -> None:
    await _settle(pilot, area)
    assert _bytes(area) == model.encode("utf-8", "surrogateescape"), step
    assert area.cursor_location == cursor, step
    assert area.cursor_state is CursorState.RESOLVED, step
    assert _doc(area).line_count == len(model.split("\n")), step
    _frame(area)


async def _undo_all_redo_all(pilot: Pilot[None], area: NovaTextArea, original: str, final: str, location: tuple[int, int]) -> None:
    """Undo every batch (the original bytes and cursor come back), then redo every batch (the final bytes and cursor come back)."""
    final_cursor = area.cursor_location
    while area.history.undo_stack:
        await _settle(pilot, area)
        area.undo()
    await _settle(pilot, area)
    assert _bytes(area) == original.encode("utf-8", "surrogateescape")
    assert area.cursor_location == location
    assert area.cursor_state is CursorState.RESOLVED
    assert _doc(area).line_count == len(original.split("\n"))
    _frame(area)
    while area.history.redo_stack:
        await _settle(pilot, area)
        area.redo()
    await _settle(pilot, area)
    assert _bytes(area) == final.encode("utf-8", "surrogateescape")
    assert area.cursor_location == final_cursor
    assert area.cursor_state is CursorState.RESOLVED
    _frame(area)


@pytest.mark.asyncio
@pytest.mark.parametrize("wrap", [False, True], ids=["nowrap", "wrap"])
@pytest.mark.parametrize("where", ["short", "medium", "long", "long_end", "document_end"])
async def test_typing_backspace_delete_enter_selection_undo_redo(tmp_path: Path, wrap: bool, where: str) -> None:
    async with _open(tmp_path, wrap=wrap) as (area, host, pilot, original):
        doc = _doc(area)
        assert not area.read_only
        location = _targets(original)[where]
        area.move_cursor(location)
        await pilot.pause()
        await _until_resolved(pilot, area)
        model = original
        position = _offset(model, location)

        await pilot.press("x", "y")
        model = _replace(model, position, position, "xy")
        position += 2
        await _check(pilot, area, model, _location(model, position), "typing")

        await pilot.press("backspace")
        model = _replace(model, position - 1, position, "")
        position -= 1
        await _check(pilot, area, model, _location(model, position), "backspace")

        await pilot.press("delete")
        if position < len(model):
            model = _replace(model, position, position + 1, "")
        await _check(pilot, area, model, _location(model, position), "delete")

        await pilot.press("enter")
        model = _replace(model, position, position, "\n")
        position += 1
        await _check(pilot, area, model, _location(model, position), "enter")

        start = max(position - 3, 0)
        end = min(position + 2, len(model))
        area.move_cursor(_location(model, start))
        area.move_cursor(_location(model, end), select=True)
        await pilot.pause()
        top, bottom = _location(model, start)[0], _location(model, end)[0]
        if not any(doc.is_long(row) for row in range(top, bottom + 1)):  # a long row is never read whole
            assert area.selected_text == model[start:end]
        await pilot.press("delete")
        model = _replace(model, start, end, "")
        position = start
        await _check(pilot, area, model, _location(model, position), "selection delete")

        await _undo_all_redo_all(pilot, area, original, model, location)
        assert not host.refused
        assert not doc.call_log.refusals


@pytest.mark.asyncio
@pytest.mark.parametrize("wrap", [False, True], ids=["nowrap", "wrap"])
async def test_undo_redo_by_key_and_repeated_typing_is_one_undo(tmp_path: Path, wrap: bool) -> None:
    async with _open(tmp_path, wrap=wrap) as (area, _host, pilot, original):
        area.move_cursor((_LONG_ROW, 300))
        await pilot.pause()
        await _until_resolved(pilot, area)
        await pilot.press(*"hello")
        assert len(area.history.undo_stack) == 1
        await pilot.press("ctrl+z")
        assert _bytes(area) == original.encode("utf-8", "surrogateescape")
        await pilot.press("ctrl+y")
        position = _offset(original, (_LONG_ROW, 300))
        assert _bytes(area) == _replace(original, position, position, "hello").encode("utf-8", "surrogateescape")
        assert area.cursor_location == (_LONG_ROW, 305)


@pytest.mark.asyncio
@pytest.mark.parametrize("wrap", [False, True], ids=["nowrap", "wrap"])
async def test_api_edits_and_paste_on_a_lazy_document(tmp_path: Path, wrap: bool) -> None:
    async with _open(tmp_path, wrap=wrap) as (area, _host, pilot, original):
        area.insert("AB\nCD", (_MEDIUM_ROW, 10))
        model = _replace(original, _offset(original, (_MEDIUM_ROW, 10)), _offset(original, (_MEDIUM_ROW, 10)), "AB\nCD")
        assert _bytes(area) == model.encode("utf-8", "surrogateescape")
        area.replace("Z", (0, 0), (1, 5))
        model = _replace(model, 0, _offset(model, (1, 5)), "Z")
        assert _bytes(area) == model.encode("utf-8", "surrogateescape")
        area.delete((_LONG_ROW + 1, 100), (_LONG_ROW + 1, 200))
        model = _replace(model, _offset(model, (_LONG_ROW + 1, 100)), _offset(model, (_LONG_ROW + 1, 200)), "")
        assert _bytes(area) == model.encode("utf-8", "surrogateescape")
        area.move_cursor((0, 1))
        await area._on_paste(events.Paste("pasted\ntext"))
        await pilot.pause()
        model = _replace(model, 1, 1, "pasted\ntext")
        assert _bytes(area) == model.encode("utf-8", "surrogateescape")
        area.clear()
        await pilot.pause()
        assert _bytes(area) == b""
        area.undo()
        assert _bytes(area) == model.encode("utf-8", "surrogateescape")


@pytest.mark.asyncio
@pytest.mark.parametrize("wrap", [False, True], ids=["nowrap", "wrap"])
async def test_edits_on_a_long_row_do_not_call_get_line(tmp_path: Path, wrap: bool) -> None:
    async with _open(tmp_path, wrap=wrap, long_chars=6000) as (area, _host, pilot, _original):
        doc = _doc(area)
        area.move_cursor((_LONG_ROW, 800))
        await pilot.pause()
        await _until_resolved(pilot, area)
        doc.call_log.events.clear()
        await pilot.press("a", "b", "backspace", "delete")
        await pilot.press("enter")
        area.undo()
        area.undo()
        area.redo()
        await pilot.pause()
        _frame(area)
        assert not [event for event in doc.call_log.events if event.method == "get_line" and event.row == _LONG_ROW]
        assert not doc.call_log.refusals


@pytest.mark.asyncio
@pytest.mark.parametrize("wrap", [False, True], ids=["nowrap", "wrap"])
async def test_invalid_bytes_are_visible_selectable_and_deletable(tmp_path: Path, wrap: bool) -> None:
    rows = [b"ab\xffcd", b"line 2", b"m" * 10 + b"\xff" + b"n" * 300, b"k" * 10 + b"\xfe" + b"z" * 900, b"end"]
    path = tmp_path / "invalid.txt"
    path.write_bytes(b"\n".join(rows) + b"\n")
    area = NovaTextArea.open(path, soft_wrap=wrap, config=_config())
    host = _Host(area)
    async with host.run_test(size=(60, 14)) as pilot:
        await await_first_layout(pilot, area)
        doc = _doc(area)
        assert doc.wait_indexed(10)
        wait_frontier(doc, 3)
        for row in (0, 2, 3):
            assert _REPLACEMENT in row_strip_text(area, row, 40), row
            assert _ESCAPE not in row_strip_text(area, row, 40), row
        assert "ab�cd" in row_strip_text(area, 0, 40)
        # The model keeps the escape character: it can be selected like any character.
        area.move_cursor((0, 2))
        area.move_cursor((0, 3), select=True)
        assert area.selected_text == _ESCAPE
        original = _bytes(area)
        assert b"\xff" in original
        await pilot.press("delete")
        edited = _bytes(area)
        assert edited == original.replace(b"ab\xffcd", b"abcd", 1)
        assert area.document.get_line(0) == "abcd"
        # Delete the invalid byte of the medium and the long row with Backspace.
        for row, byte in ((2, b"\xff"), (3, b"\xfe")):
            area.move_cursor((row, 11))
            await pilot.pause()
            await _until_resolved(pilot, area)
            await pilot.press("backspace")
            assert byte not in _bytes(area)
        assert b"\xff" not in _bytes(area)
        assert b"\xfe" not in _bytes(area)
        assert _REPLACEMENT not in row_strip_text(area, 0, 40)
        area.undo()
        assert b"\xfe" in _bytes(area)
        await pilot.pause()
        assert _REPLACEMENT in row_strip_text(area, 3, 40)


@pytest.mark.asyncio
async def test_stock_document_edits_still_work() -> None:
    area = NovaTextArea(text="hello\nworld")
    app = _Host(area)
    async with app.run_test() as pilot:
        await pilot.pause()
        assert not area.read_only
        area.move_cursor((0, 5))
        await pilot.press("!", "enter", "x")
        assert area.text == "hello!\nx\nworld"
        for _ in range(3):
            area.undo()
        assert area.text == "hello\nworld"
        area.redo()
        assert area.text == "hello!\nworld"
        assert not app.refused


class _Rig:
    """A widget over a partly scanned file: the long-row scan is held back after two reads."""

    def __init__(self, path: Path, *, wrap: bool) -> None:
        self.data = path.read_bytes()
        self.source = TrickleSource(path, budget=2)
        self.area = NovaTextArea.open(self.source, soft_wrap=wrap, config=_config())
        self.host = _Host(self.area)

    @asynccontextmanager
    async def run(self) -> AsyncIterator[Pilot[None]]:
        async with self.host.run_test(size=(40, 10)) as pilot:
            try:
                yield pilot
            finally:
                self.source.release()


@pytest.mark.asyncio
@pytest.mark.parametrize("wrap", [False, True], ids=["nowrap", "wrap"])
async def test_edit_at_an_unresolved_position_is_refused_with_a_visible_message(tmp_path: Path, wrap: bool) -> None:
    path = make_mixed(tmp_path / "m.txt", long_chars=6000)
    rig = _Rig(path, wrap=False)
    area = rig.area
    async with rig.run() as pilot:
        await pilot.pause()
        doc = _doc(area)
        assert doc.wait_indexed(10)
        await wait_until(pilot, lambda: area.size.height > 0)
        area.move_cursor((_LONG_ROW, 0))
        await pilot.pause()
        await wait_until(pilot, rig.source.blocked.is_set)
        await pilot.press("end")
        if wrap:  # a provisional cursor waits for the scan in wrap mode: the state is PENDING
            area.soft_wrap = True
            await pilot.pause()
        assert area.cursor_state is (CursorState.PENDING if wrap else CursorState.PROVISIONAL)
        before = _bytes(area)
        history_before = (len(area.history.undo_stack), len(area.history.redo_stack))
        location = area.cursor_location
        await pilot.press("x")
        await pilot.pause()
        assert _bytes(area) == before
        assert (len(area.history.undo_stack), len(area.history.redo_stack)) == history_before
        assert area.cursor_location == location
        assert len(rig.host.refused) == 1
        assert rig.host.bells == 1
        notes = [note for note in rig.host._notifications if note.severity == "warning"]
        assert len(notes) == 1
        assert notes[0].message == f"Edit refused: {rig.host.refused[0]}. It works once indexing reaches the position."
        assert "indexing" in notes[0].message
        # A deletion whose end lies beyond the scanned part of the row is refused as well.
        area.delete((_LONG_ROW, 0), (_LONG_ROW, 5000))
        await pilot.pause()
        assert _bytes(area) == before
        assert len(rig.host.refused) == 2
        rig.source.release()
        await wait_until(pilot, lambda: area.cursor_state is CursorState.RESOLVED)
        await pilot.press("x")
        await pilot.pause()
        assert _bytes(area) != before
        assert len(rig.host.refused) == 2


@pytest.mark.asyncio
async def test_edit_beyond_the_scan_frontier_is_refused_and_unchanged(tmp_path: Path) -> None:
    rig = _Rig(make_mixed(tmp_path / "m.txt", long_chars=6000), wrap=False)
    area = rig.area
    async with rig.run() as pilot:
        await pilot.pause()
        assert _doc(area).wait_indexed(10)
        await wait_until(pilot, lambda: area.size.height > 0)
        area.move_cursor((_LONG_ROW, 0))
        await pilot.pause()
        await wait_until(pilot, rig.source.blocked.is_set)
        assert area.cursor_state is CursorState.RESOLVED
        before = _bytes(area)
        area.delete((_LONG_ROW, 0), (_LONG_ROW, 5000))
        await pilot.pause()
        assert _bytes(area) == before
        assert len(rig.host.refused) == 1
        assert "not resolved yet" in rig.host.refused[0]
        assert not area.history.undo_stack
        assert rig.host.bells == 1


@pytest.mark.asyncio
async def test_refused_undo_and_redo_keep_the_history_and_roll_back_a_partial_batch(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    path = make_mixed(tmp_path / "m.txt")
    area = NovaTextArea.open(path, config=_config())
    host = _Host(area)
    async with host.run_test(size=(60, 14)) as pilot:
        await await_first_layout(pilot, area)
        doc = _doc(area)
        assert doc.wait_indexed(10)
        original = _bytes(area)
        area.insert("a", (0, 0))
        area.insert("b", (2, 0))  # not adjacent: both edits stay in one batch
        edited = _bytes(area)
        assert len(area.history.undo_stack) == 1
        assert len(area.history.undo_stack[0]) == 2
        real = LazyDocument.splice_bytes
        calls: list[int] = []

        def failing(self: LazyDocument, start_byte: int, end_byte: int, content: Content) -> EditResult:
            calls.append(start_byte)
            if len(calls) == 2:
                msg = "test"
                raise RowUnavailable(msg)
            return real(self, start_byte, end_byte, content)

        monkeypatch.setattr(LazyDocument, "splice_bytes", failing)
        area.undo()
        await pilot.pause()
        assert _bytes(area) == edited
        assert len(area.history.undo_stack) == 1
        assert not area.history.redo_stack
        assert len(host.refused) == 1
        monkeypatch.setattr(LazyDocument, "splice_bytes", real)
        area.undo()
        assert _bytes(area) == original
        calls.clear()
        monkeypatch.setattr(LazyDocument, "splice_bytes", failing)
        area.redo()
        await pilot.pause()
        assert _bytes(area) == original
        assert len(area.history.redo_stack) == 1
        assert not area.history.undo_stack
        assert len(host.refused) == 2
        monkeypatch.setattr(LazyDocument, "splice_bytes", real)
        area.redo()
        assert _bytes(area) == edited
