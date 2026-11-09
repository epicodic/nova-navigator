"""A hex copy of a not-yet-scanned region (a `Content` with unknown pieces) pasted into the text view (S0003 ACT5 review concern)."""

from __future__ import annotations

from collections.abc import Iterator

import pytest
from textual.app import ComposeResult
from textual.pilot import Pilot

from nova_editor.document import RowUnavailable
from nova_editor.document._document import Selection
from nova_editor.document._lazy_config import LazyConfig
from nova_editor.document._lazy_document import LazyDocument
from nova_editor.widget import HexView, NovaTextArea
from tests.nova_editor.core.reference import StepSource
from tests.nova_editor.helpers_view import HostApp

BLOCK = 16
ROWS = 60
DATA = b"".join(b"row %02d\n" % i for i in range(ROWS))
SCANNED_BLOCKS = 6


class BothHost(HostApp):
    """Hosts a text widget and a hex view over its document, wired the way `DocumentView` wires them."""

    def __init__(self, area: NovaTextArea) -> None:
        super().__init__(area)
        self.refused: list[NovaTextArea.EditRefused] = []
        self.view = HexView(lambda: area.document)
        self.view.clipboard_owner = lambda: area

    def compose(self) -> ComposeResult:
        yield from super().compose()
        yield self.view

    def on_nova_text_area_edit_refused(self, message: NovaTextArea.EditRefused) -> None:
        self.refused.append(message)


@pytest.fixture
def stepped() -> Iterator[tuple[StepSource, LazyDocument]]:
    source = StepSource(DATA, BLOCK)
    doc = LazyDocument(source, LazyConfig(stride=2, scan_block=BLOCK), autostart=False)
    source.attach(doc._line_index)
    for _ in range(SCANNED_BLOCKS):
        source.step()
    yield source, doc
    source.stop()
    doc.close()


def _data(doc: LazyDocument) -> bytes:
    return doc.read_bytes(0, doc.length)


async def _copy_unscanned(host: BothHost, area: NovaTextArea, doc: LazyDocument, pilot: Pilot) -> bytes:
    """Load `doc` into the text widget, copy a range beyond the scan frontier in the hex view and return its bytes."""
    area._replace_document(doc)
    await pilot.pause()
    begin, end = 300, 340
    assert doc.snapshot().scanned_bytes < begin
    view = host.view
    view.focus()
    view.set_cursor(begin)
    view.set_cursor(end, select=True)
    await pilot.press("ctrl+c")
    record = area._clipboard_record
    assert record is not None
    assert record.content.unknown > 0  # the premise: the copy holds pieces the scan has not classified
    area.focus()
    return DATA[begin:end]


@pytest.mark.asyncio
async def test_paste_at_a_resolved_position_is_exact_and_undoable(stepped: tuple[StepSource, LazyDocument]) -> None:
    source, doc = stepped
    area = NovaTextArea(text="")
    host = BothHost(area)
    async with host.run_test(size=(120, 30)) as pilot:
        await pilot.pause()
        copied = await _copy_unscanned(host, area, doc, pilot)
        area.selection = Selection((2, 0), (2, 0))
        await pilot.press("ctrl+v")
        assert not host.source_changed
        expected = DATA[:14] + copied + DATA[14:]
        assert _data(doc) == expected
        assert area.selection.start == area.selection.end
        await pilot.press("ctrl+z")
        assert _data(doc) == DATA
        await pilot.press("ctrl+y")
        assert _data(doc) == expected
        await pilot.press("ctrl+z")
        assert _data(doc) == DATA
        await pilot.press("ctrl+y")
        while not source.finished:  # the scan reaches the edit: the rows of the pasted bytes are classified like any others
            source.step()
        assert doc.wait_indexed(10.0)
        assert doc.line_count == expected.count(b"\n") + 1
        assert doc.row_byte_length(2) == len(expected.split(b"\n")[2])


@pytest.mark.asyncio
async def test_paste_over_a_resolved_selection(stepped: tuple[StepSource, LazyDocument]) -> None:
    _source, doc = stepped
    area = NovaTextArea(text="")
    host = BothHost(area)
    async with host.run_test(size=(120, 30)) as pilot:
        await pilot.pause()
        copied = await _copy_unscanned(host, area, doc, pilot)
        area.selection = Selection((1, 0), (3, 0))
        await pilot.press("ctrl+v")
        assert _data(doc) == DATA[:7] + copied + DATA[21:]
        await pilot.press("ctrl+z")
        assert _data(doc) == DATA


@pytest.mark.asyncio
async def test_paste_while_the_cursor_waits_for_the_scan(stepped: tuple[StepSource, LazyDocument]) -> None:
    _source, doc = stepped
    area = NovaTextArea(text="")
    host = BothHost(area)
    async with host.run_test(size=(120, 30)) as pilot:
        await pilot.pause()
        copied = await _copy_unscanned(host, area, doc, pilot)
        area.selection = Selection((1, 0), (1, 0))
        area.place_cursor_at_byte(400)  # beyond the frontier: a pending jump; the paste goes to the cursor that is still shown
        assert area.pending_progress is not None
        assert area.selection == Selection((1, 0), (1, 0))
        await pilot.press("ctrl+v")
        assert not host.source_changed
        after = _data(doc)
        assert len(after) == len(DATA) + len(copied)
        assert after.replace(copied, b"", 1) == DATA
        assert after[:7] == DATA[:7]  # the paste went to the cursor that is still shown (row 1, column 0), not to the pending jump
        assert after[7 : 7 + len(copied)] == copied
        assert after[7 + len(copied) :] == DATA[7:]
        await pilot.press("ctrl+z")
        assert _data(doc) == DATA


Sel = tuple[tuple[int, int], tuple[int, int]]

FIRST_ROW_SELECTIONS = [
    pytest.param(((0, 0), (0, 0)), id="cursor-col-0"),
    pytest.param(((0, 3), (0, 3)), id="cursor-mid-row-0"),
    pytest.param(((0, 1), (0, 4)), id="forward-in-row-0"),
    pytest.param(((0, 4), (0, 1)), id="backward-in-row-0"),
    pytest.param(((0, 2), (2, 1)), id="forward-from-row-0"),
    pytest.param(((2, 1), (0, 2)), id="backward-to-row-0"),
    pytest.param(((5, 2), (0, 0)), id="backward-to-row-0-col-0"),
]


def _byte_of(doc: LazyDocument, location: tuple[int, int]) -> int:
    """The byte offset of a resolved `location` of the text view."""
    offset = doc.byte_offset(*location)
    assert offset is not None
    return offset


def _row_resolved(doc: LazyDocument, row: int) -> bool:
    try:
        doc.row_byte_length(row)
    except (RowUnavailable, IndexError):
        return False
    return True


def _on_resolved_row(doc: LazyDocument, area: NovaTextArea) -> bool:
    """Whether the cursor and the selection ends sit on rows that resolve."""
    return all(_row_resolved(doc, row) for row, _column in (area.selection.start, area.selection.end))


def _last_resolved(doc: LazyDocument) -> int:
    """The last row that resolves (the rows before the first unknown piece), found by scanning from the top."""
    row = 0
    while _row_resolved(doc, row + 1):
        row += 1
    return row


@pytest.mark.asyncio
@pytest.mark.parametrize("wrap", [False, True], ids=["nowrap", "wrap"])
@pytest.mark.parametrize("selection", FIRST_ROW_SELECTIONS)
async def test_paste_reaching_the_first_row_is_refused_and_changes_nothing(stepped: tuple[StepSource, LazyDocument], selection: Sel, wrap: bool) -> None:
    """The refusal looks at the lower end of the selection: a backward selection that reaches row 0 has its anchor on a later row."""
    _source, doc = stepped
    area = NovaTextArea(text="")
    host = BothHost(area)
    async with host.run_test(size=(120, 30)) as pilot:
        await pilot.pause()
        await _copy_unscanned(host, area, doc, pilot)
        area.soft_wrap = wrap
        await pilot.pause()
        area.selection = Selection(*selection)
        await pilot.press("ctrl+v")
        await pilot.pause()
        assert _data(doc) == DATA
        assert not area.modified
        assert len(host.refused) == 1
        assert "first row" in host.refused[0].reason
        assert area.selection == Selection(*selection)
        await pilot.press("ctrl+z", "ctrl+y")  # nothing was recorded
        assert _data(doc) == DATA
        assert not host.source_changed


MID_ROW_SELECTIONS = [
    pytest.param(((2, 3), (2, 3)), id="mid-row-2"),
    pytest.param(((2, 3), (3, 2)), id="across-rows"),
    pytest.param(((5, 6), (5, 6)), id="mid-row-5"),
    pytest.param(((1, 0), (1, 0)), id="row-start"),
    pytest.param(((3, 4), (1, 2)), id="backwards"),
    pytest.param(((4, 0), (2, 3)), id="backwards-to-mid-row"),
    pytest.param(((1, 2), (1, 5)), id="forward-in-row"),
]


@pytest.mark.asyncio
@pytest.mark.parametrize("wrap", [False, True], ids=["nowrap", "wrap"])
@pytest.mark.parametrize("selection", MID_ROW_SELECTIONS)
async def test_blind_paste_undo_redo_parks_the_cursor_on_the_row_before_the_edit(stepped: tuple[StepSource, LazyDocument], selection: Sel, wrap: bool) -> None:
    """The row of the lower selection end holds the pasted unknown bytes, so the cursor waits at the start of the row before it (a pending jump)."""
    source, doc = stepped
    area = NovaTextArea(text="")
    host = BothHost(area)
    async with host.run_test(size=(120, 30)) as pilot:
        await pilot.pause()
        copied = await _copy_unscanned(host, area, doc, pilot)
        area.soft_wrap = wrap
        await pilot.pause()
        area.selection = Selection(*selection)
        low = min(selection)
        begin = _byte_of(doc, low)
        finish = _byte_of(doc, max(selection))
        expected = DATA[:begin] + copied + DATA[finish:]
        parked = Selection.cursor((low[0] - 1, 0))
        await pilot.press("ctrl+v")
        for _ in range(2):  # paste, undo, redo, undo
            assert _data(doc) == expected
            assert area.modified
            assert _last_resolved(doc) == low[0] - 1
            assert area.selection == parked
            assert area.pending_progress is not None
            await pilot.press("ctrl+z")
            assert _data(doc) == DATA
            assert not area.modified
            assert _on_resolved_row(doc, area)
            await pilot.press("ctrl+y")
        assert not host.source_changed
        while not source.finished:  # the scan reaches the edit: the pending jump completes behind the insertion
            source.step()
        assert doc.wait_indexed(10.0)
        await pilot.pause(0.2)
        assert area.pending_progress is None
        assert _byte_of(doc, area.selection.end) == begin + len(copied)
        assert area.selection.is_empty


NAVIGATION_KEYS = [
    "up",
    "down",
    "left",
    "right",
    "home",
    "end",
    "pageup",
    "pagedown",
    "shift+up",
    "shift+down",
    "shift+left",
    "shift+right",
    "shift+home",
    "shift+end",
    "shift+pageup",
    "shift+pagedown",
    "ctrl+left",
    "ctrl+right",
]

NAVIGATION_PASTES = [
    pytest.param((2, 3), id="paste-mid-row-2"),
    pytest.param((1, 0), id="paste-row-1"),
    pytest.param((4, 2), id="paste-mid-row-4"),
]


@pytest.mark.asyncio
@pytest.mark.parametrize("wrap", [False, True], ids=["nowrap", "wrap"])
@pytest.mark.parametrize("key", NAVIGATION_KEYS)
@pytest.mark.parametrize("paste_at", NAVIGATION_PASTES)
async def test_navigation_after_a_blind_paste_stays_on_resolved_rows(stepped: tuple[StepSource, LazyDocument], paste_at: tuple[int, int], key: str, wrap: bool) -> None:
    """Rows from the open row on cannot be resolved, so no key may move the cursor or the selection end onto them (and none may raise)."""
    source, doc = stepped
    area = NovaTextArea(text="")
    host = BothHost(area)
    async with host.run_test(size=(120, 30)) as pilot:
        await pilot.pause()
        copied = await _copy_unscanned(host, area, doc, pilot)
        area.soft_wrap = wrap
        await pilot.pause()
        area.selection = Selection.cursor(paste_at)
        begin = _byte_of(doc, paste_at)
        expected = DATA[:begin] + copied + DATA[begin:]
        await pilot.press("ctrl+v")
        parked = paste_at[0] - 1
        assert area.selection == Selection.cursor((parked, 0))
        for _ in range(3):  # repeated, so that a key that creeps onto the open row on its second press is caught too
            await pilot.press(key)
            await pilot.pause()
            assert _on_resolved_row(doc, area), (key, area.selection)
            assert area.selection.end[0] <= parked
            assert area.selection.start[0] <= parked
        assert _data(doc) == expected
        assert not host.source_changed
        while not source.finished:
            source.step()
        assert doc.wait_indexed(10.0)
        await pilot.pause(0.2)
        assert _data(doc) == expected
        await pilot.press("ctrl+z", "ctrl+y")
        assert _data(doc) == expected


@pytest.mark.asyncio
@pytest.mark.parametrize("wrap", [False, True], ids=["nowrap", "wrap"])
async def test_down_from_the_parked_row_goes_to_its_end_and_stays(stepped: tuple[StepSource, LazyDocument], wrap: bool) -> None:
    """Down on the last resolved row behaves like down on the last row of a document: it goes to the end of the row."""
    _source, doc = stepped
    area = NovaTextArea(text="")
    host = BothHost(area)
    async with host.run_test(size=(120, 30)) as pilot:
        await pilot.pause()
        await _copy_unscanned(host, area, doc, pilot)
        area.soft_wrap = wrap
        await pilot.pause()
        area.selection = Selection.cursor((2, 3))
        await pilot.press("ctrl+v")
        assert area.selection == Selection.cursor((1, 0))
        await pilot.press("down")
        assert area.selection == Selection.cursor((1, len("row 01")))
        await pilot.press("up")
        assert area.selection.end[0] == 0
        await pilot.press("down", "down")
        assert area.selection.end == (1, len("row 01"))


@pytest.mark.asyncio
async def test_navigation_at_the_scan_frontier_of_an_unedited_document_stays_on_resolved_rows(stepped: tuple[StepSource, LazyDocument]) -> None:
    """The open row at the scan frontier of an unedited document cannot be resolved either: down, page down and shift+down stop on the row before it."""
    _source, doc = stepped
    area = NovaTextArea(text="")
    host = BothHost(area)
    async with host.run_test(size=(120, 30)) as pilot:
        await pilot.pause()
        area._replace_document(doc)
        await pilot.pause()
        last = _last_resolved(doc)
        assert doc.snapshot().count == last + 2  # the premise: the count includes the open row
        for key in ("down", "pagedown", "shift+down", "right"):
            area.selection = Selection.cursor((last - 1, 0))
            for _ in range(3):
                await pilot.press(key)
                await pilot.pause()
                assert _on_resolved_row(doc, area), (key, area.selection)


@pytest.mark.asyncio
async def test_a_paste_behind_the_cursor_row_is_placed_after_the_insertion_when_the_scan_arrives(stepped: tuple[StepSource, LazyDocument]) -> None:
    """A paste of unknown content at the start of row 2 ends with the cursor behind the insertion once the scan has classified it."""
    source, doc = stepped
    area = NovaTextArea(text="")
    host = BothHost(area)
    async with host.run_test(size=(120, 30)) as pilot:
        await pilot.pause()
        copied = await _copy_unscanned(host, area, doc, pilot)
        row_start = len(b"row 00\n") + len(b"row 01\n")  # byte offset of the start of row 2
        area.selection = Selection((2, 0), (2, 0))
        await pilot.press("ctrl+v")
        assert area.modified
        while not source.finished:
            source.step()
        assert doc.wait_indexed(10.0)
        await pilot.pause(0.2)
        assert _byte_of(doc, area.selection.end) == row_start + len(copied)
        await pilot.press("ctrl+z")
        assert not area.modified
        await pilot.press("ctrl+y")
        assert area.modified
        assert _data(doc) == DATA[:row_start] + copied + DATA[row_start:]


@pytest.mark.asyncio
@pytest.mark.parametrize("selection", [pytest.param(((0, 2), (0, 2)), id="row-0"), pytest.param(((2, 1), (0, 2)), id="backward-to-row-0")])
async def test_hex_paste_at_offset_zero_then_text_view_undo_and_redo_keep_the_cursor_safe(stepped: tuple[StepSource, LazyDocument], selection: Sel) -> None:
    """A hex paste of unknown content at offset 0 leaves no resolved row: undo and redo in the text view must neither raise nor leave the cursor on an unresolved row."""
    source, doc = stepped
    area = NovaTextArea(text="")
    host = BothHost(area)
    async with host.run_test(size=(120, 30)) as pilot:
        await pilot.pause()
        copied = await _copy_unscanned(host, area, doc, pilot)
        area.selection = Selection(*selection)
        view = host.view
        view.focus()
        view.insert_mode = True  # overwrite mode would replace as many bytes as were copied
        view.set_cursor(0)
        await pilot.press("ctrl+v")  # the hex view pastes the internal clipboard at offset 0
        await pilot.pause()
        assert _data(doc) == copied + DATA
        area.focus()
        await pilot.press("ctrl+z")
        await pilot.pause()
        assert _data(doc) == DATA
        assert _on_resolved_row(doc, area)
        await pilot.press("ctrl+y")
        await pilot.pause()
        assert _data(doc) == copied + DATA
        assert area.selection == Selection.cursor((0, 0))
        for key in ("down", "pagedown", "shift+down", "up", "right", "end", "home", "ctrl+z", "ctrl+y"):
            await pilot.press(key)
            await pilot.pause()
        assert _data(doc) == copied + DATA
        while not source.finished:
            source.step()
        assert doc.wait_indexed(10.0)
        await pilot.pause(0.2)
        assert _data(doc) == copied + DATA
        assert not host.source_changed


STRANDED_KEYS = [
    "x",
    "enter",
    "tab",
    "backspace",
    "delete",
    "ctrl+backspace",
    "ctrl+delete",
    "ctrl+k",
    "ctrl+x",
    "ctrl+v",
    "ctrl+a",
    "ctrl+end",
    "left",
    "shift+right",
    "ctrl+left",
]


@pytest.mark.asyncio
@pytest.mark.parametrize("key", STRANDED_KEYS)
async def test_keys_and_clicks_with_no_resolved_row_change_nothing_and_do_not_raise(stepped: tuple[StepSource, LazyDocument], key: str) -> None:
    """With row 0 holding unknown bytes the text view has no row to stand on: keys and clicks are ignored until the scan arrives."""
    source, doc = stepped
    area = NovaTextArea(text="")
    host = BothHost(area)
    async with host.run_test(size=(120, 30)) as pilot:
        await pilot.pause()
        copied = await _copy_unscanned(host, area, doc, pilot)
        view = host.view
        view.focus()
        view.insert_mode = True
        view.set_cursor(0)
        await pilot.press("ctrl+v")
        await pilot.pause()
        area.focus()
        area.selection = Selection.cursor((0, 0))
        await pilot.pause()
        assert not _row_resolved(doc, 0)  # the premise
        before = _data(doc)
        assert before == copied + DATA
        await pilot.press(key)
        await pilot.click(area, offset=(8, 2))
        await pilot.pause()
        assert _data(doc) == before
        while not source.finished:
            source.step()
        assert doc.wait_indexed(10.0)
        await pilot.pause(0.2)
        assert _row_resolved(doc, 0)
        assert _data(doc) == before
        await pilot.press(key)  # and the key works again once row 0 resolves
        await pilot.pause()
