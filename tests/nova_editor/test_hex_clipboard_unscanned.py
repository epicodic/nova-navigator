"""A hex copy of a not-yet-scanned region (a `Content` with unknown pieces) pasted into the text view (S0003 ACT5 review concern)."""

from __future__ import annotations

from collections.abc import Iterator

import pytest
from textual.app import ComposeResult
from textual.pilot import Pilot

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
        self.view = HexView(lambda: area.document)
        self.view.clipboard_owner = lambda: area

    def compose(self) -> ComposeResult:
        yield from super().compose()
        yield self.view


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
        await pilot.press("ctrl+z")
        assert _data(doc) == DATA


@pytest.mark.asyncio
async def test_paste_in_the_first_row_is_refused_and_changes_nothing(stepped: tuple[StepSource, LazyDocument]) -> None:
    _source, doc = stepped
    area = NovaTextArea(text="")
    host = BothHost(area)
    async with host.run_test(size=(120, 30)) as pilot:
        await pilot.pause()
        await _copy_unscanned(host, area, doc, pilot)
        area.selection = Selection((0, 0), (0, 0))
        await pilot.press("ctrl+v")
        assert _data(doc) == DATA
        assert not area.modified
