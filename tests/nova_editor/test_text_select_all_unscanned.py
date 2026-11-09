"""Select all in the text view of a document whose scan has not completed (S0003 ACT8 review concern)."""

from __future__ import annotations

from collections.abc import Iterator

import pytest

from nova_editor.document._document import Selection
from nova_editor.document._lazy_config import LazyConfig
from nova_editor.document._lazy_document import LazyDocument
from nova_editor.widget import NovaTextArea
from tests.nova_editor.core.reference import StepSource
from tests.nova_editor.helpers_view import HostApp

BLOCK = 16
ROWS = 60
DATA = b"".join(b"row %02d\n" % i for i in range(ROWS))
SCANNED_BLOCKS = 6


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


@pytest.mark.asyncio
@pytest.mark.parametrize("where", ["first", "middle", "last"])
@pytest.mark.parametrize("key", ["ctrl+shift+a", "f8"])
async def test_select_all_on_a_partly_scanned_document(stepped: tuple[StepSource, LazyDocument], where: str, key: str) -> None:
    _source, doc = stepped
    area = NovaTextArea(text="")
    host = HostApp(area)
    async with host.run_test(size=(120, 30)) as pilot:
        await pilot.pause()
        area._replace_document(doc)
        await pilot.pause()
        assert not doc.snapshot().complete
        last = doc.last_reachable_row
        assert 0 < last < ROWS - 1
        row = {"first": 0, "middle": last // 2, "last": last}[where]
        area.selection = Selection((row, 0), (row, 0))
        area.focus()
        await pilot.press(key)
        await pilot.pause()
        assert area.selection.start == (0, 0)
        assert area.selection.end[0] == last
        assert not host.source_changed
