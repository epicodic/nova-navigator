"""Internal clipboard of piece references and the system clipboard cap (ACT4 design 10, Task 12)."""

from __future__ import annotations

import pytest
from textual.pilot import Pilot

from nova_editor.document._document import Selection
from nova_editor.document._lazy_config import LazyConfig
from nova_editor.document._lazy_document import LazyDocument, RowUnavailable
from nova_editor.widget import NovaTextArea
from tests.nova_editor.document.test_edit_history import ROW, ROWS, RowsSource
from tests.nova_editor.helpers_view import HostApp

_REPLACEMENT = "\N{REPLACEMENT CHARACTER}"
_ESCAPE = "\udcff"


def _text(area: NovaTextArea) -> str:
    doc = area.document
    return doc.read_bytes(0, doc.length).decode("utf-8", "surrogateescape")


async def _warnings(pilot: Pilot[None]) -> list[str]:
    await pilot.pause()
    return [note.message for note in pilot.app._notifications if note.severity == "warning"]


def _rows(count: int) -> str:
    return "\n".join(f"row {n} " + "x" * (n % 7) for n in range(count))


def test_clipboard_cap_default_is_4_mib() -> None:
    """The system clipboard cap is set to 4 MiB (the p95 of measured copy + terminal write time)."""
    area = NovaTextArea(text="test")
    assert area.clipboard_cap == 4_194_304


@pytest.mark.asyncio
async def test_multi_row_copy_and_paste_equals_the_reference() -> None:
    text = _rows(40)
    area = NovaTextArea(text=text)
    async with HostApp(area).run_test() as pilot:
        await pilot.pause()
        area.selection = Selection((3, 2), (9, 4))
        area.action_copy()
        reference = text.split("\n")
        expected = "\n".join([reference[3][2:], *reference[4:9], reference[9][:4]])
        assert pilot.app.clipboard == expected
        area.selection = Selection.cursor((20, 3))
        area.action_paste()
        offset = sum(len(r) + 1 for r in reference[:20]) + 3
        assert _text(area) == text[:offset] + expected + text[offset:]
        assert area.cursor_location == (26, 4)
        assert not (await _warnings(pilot))


@pytest.mark.asyncio
async def test_paste_undo_and_redo() -> None:
    text = _rows(30)
    area = NovaTextArea(text=text)
    async with HostApp(area).run_test() as pilot:
        await pilot.pause()
        area.selection = Selection((2, 1), (6, 3))
        area.action_copy()
        area.selection = Selection((10, 2), (12, 1))
        area.action_paste()
        pasted = _text(area)
        assert pasted != text
        area.undo()
        assert _text(area) == text
        assert area.selection == Selection((10, 2), (12, 1))
        area.redo()
        assert _text(area) == pasted
        area.undo()
        assert _text(area) == text


@pytest.mark.asyncio
async def test_text_from_outside_is_inserted_as_text() -> None:
    text = _rows(10)
    area = NovaTextArea(text=text)
    async with HostApp(area).run_test() as pilot:
        await pilot.pause()
        area.selection = Selection((1, 0), (2, 0))
        area.action_copy()
        pilot.app.copy_to_clipboard("outside\r\ntext")
        area.selection = Selection.cursor((5, 0))
        area.action_paste()
        offset = sum(len(r) + 1 for r in text.split("\n")[:5])
        assert _text(area) == text[:offset] + "outside\ntext" + text[offset:]


@pytest.mark.asyncio
async def test_invalid_bytes_survive_the_internal_clipboard_exactly() -> None:
    area = NovaTextArea(text=f"ab{_ESCAPE}cd\nxy{_ESCAPE}z\n")
    async with HostApp(area).run_test() as pilot:
        await pilot.pause()
        area.selection = Selection((0, 1), (1, 3))
        area.action_copy()
        assert pilot.app.clipboard == f"b{_REPLACEMENT}cd\nxy{_REPLACEMENT}"
        area.selection = Selection.cursor((2, 0))
        area.action_paste()
        doc = area.document
        data = doc.read_bytes(0, doc.length)
        assert data == b"ab\xffcd\nxy\xffz\nb\xffcd\nxy\xff"


@pytest.mark.asyncio
async def test_warning_above_the_cap_keeps_the_internal_copy() -> None:
    text = _rows(40)
    area = NovaTextArea(text=text)
    area.clipboard_cap = 20
    async with HostApp(area).run_test() as pilot:
        await pilot.pause()
        pilot.app.copy_to_clipboard("before")
        area.selection = Selection((2, 0), (8, 0))
        area.action_copy()
        assert pilot.app.clipboard == "before"
        assert any("clipboard" in message for message in (await _warnings(pilot)))
        area.selection = Selection.cursor((30, 0))
        area.action_paste()
        rows = text.split("\n")
        offset = sum(len(r) + 1 for r in rows[:30])
        piece = text[sum(len(r) + 1 for r in rows[:2]) : sum(len(r) + 1 for r in rows[:8])]
        assert _text(area) == text[:offset] + piece + text[offset:]
        # Another application changes the clipboard: its text is pasted instead.
        pilot.app.copy_to_clipboard("changed")
        area.selection = Selection.cursor((0, 0))
        area.action_paste()
        assert _text(area).startswith("changedrow 0")


@pytest.mark.asyncio
async def test_cut_over_64_kib_uses_the_internal_clipboard_and_warns_above_the_cap() -> None:
    text = "\n".join("y" * 99 for _ in range(1500))
    area = NovaTextArea(text=text)
    async with HostApp(area).run_test() as pilot:
        await pilot.pause()
        area.selection = Selection((0, 0), (1000, 0))
        area.clipboard_cap = 200_000
        area.action_cut()
        assert len(pilot.app.clipboard) == 100_000
        assert not (await _warnings(pilot))
        area.action_paste()
        assert _text(area) == text
        area.undo()
        area.undo()
        assert _text(area) == text
        area.selection = Selection((0, 0), (1000, 0))
        area.clipboard_cap = 1000
        pilot.app.copy_to_clipboard("keep")
        area.action_cut()
        assert pilot.app.clipboard == "keep"
        assert any("clipboard" in message for message in (await _warnings(pilot)))
        assert _text(area) == "\n".join(text.split("\n")[1000:])
        area.selection = Selection.cursor((0, 0))
        area.action_paste()
        assert _text(area) == text


@pytest.mark.asyncio
async def test_copy_refuses_an_unresolved_selection_like_an_edit(monkeypatch: pytest.MonkeyPatch) -> None:
    area = NovaTextArea(text=_rows(5))
    refused: list[str] = []

    async with HostApp(area).run_test() as pilot:
        await pilot.pause()
        doc = area.document

        def refuse(*_args: object) -> object:
            msg = "column 9 of long row 1 is not resolved yet"
            raise RowUnavailable(msg)

        monkeypatch.setattr(doc, "selection_content", refuse)
        monkeypatch.setattr(area, "_refuse_edit", refused.append)
        area.selection = Selection((0, 0), (1, 2))
        area.action_copy()
        assert refused == ["column 9 of long row 1 is not resolved yet"]
        assert pilot.app.clipboard == ""


def test_selection_content_is_exact_and_needs_resolved_columns() -> None:
    doc = LazyDocument.from_text("héllo\nwörld\nend")
    try:
        content = doc.selection_content((0, 1), (1, 2))
        assert doc.content_text(content) == "éllo\nwö"
        assert content.length == len("éllo\nwö".encode())
        assert doc.selection_content((1, 2), (0, 1)).length == content.length
        assert doc.selection_content((1, 1), (1, 1)).length == 0
    finally:
        doc.close()


def _big_document() -> tuple[RowsSource, LazyDocument]:
    source = RowsSource()
    doc = LazyDocument(source, LazyConfig(stride=4096, scan_block=1 << 24, sync_scan_limit=0), autostart=False)
    doc.start_scan()
    assert doc.wait_indexed(120.0)
    return source, doc


def test_gigabyte_selection_content_and_splice_read_no_data() -> None:
    source, doc = _big_document()
    try:
        source.ui_bytes = 0
        content = doc.selection_content((100, 10), (30_000, 20))
        gigantic = (30_000 - 100) * ROW + 10
        assert content.length == gigantic
        assert len(content.pieces) <= 3
        start = doc.length
        result = doc.splice((5, 0), (5, 0), content)
        assert result.inserted is not None
        assert doc.length == start + gigantic
        assert doc._table.tree.piece_count <= 8
        assert source.ui_bytes < 8 * ROW
    finally:
        doc.close()


@pytest.mark.asyncio
async def test_gigabyte_copy_and_paste_in_the_widget_read_no_data() -> None:
    source, doc = _big_document()
    area = NovaTextArea(text="")
    async with HostApp(area).run_test() as pilot:
        await pilot.pause()
        area._replace_document(doc)
        await pilot.pause()
        source.ui_bytes = 0
        area.selection = Selection((100, 10), (30_000, 20))
        area.action_copy()
        gigantic = (30_000 - 100) * ROW + 10
        assert any("clipboard" in message for message in (await _warnings(pilot)))
        assert pilot.app.clipboard == ""
        area.selection = Selection.cursor((5, 0))
        area.action_paste()
        assert doc.length == ROW * ROWS + gigantic
        area.undo()
        assert doc.length == ROW * ROWS
        area.redo()
        assert doc.length == ROW * ROWS + gigantic
        assert source.ui_bytes < 64 * ROW
