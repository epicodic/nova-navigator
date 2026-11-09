"""The three public `NovaTextArea` methods the view switch uses: byte placement, the stale refresh and the source-changed report (S0003 ACT4 task 4)."""

from __future__ import annotations

from pathlib import Path

import pytest
from textual.widget import Widget

from nova_editor.core import ChangeKind, SourceChanged
from nova_editor.document._errors import EditsLocked
from nova_editor.widget import NovaTextArea
from tests.nova_editor.helpers_view import HostApp, gated_source, open_with_gated_line_scan, release_line_scan, wait_until

STALE = "file changed on disk"


class ChangedHost(HostApp):
    """`HostApp` that also counts the `Changed` messages of the widget."""

    def __init__(self, widget: Widget) -> None:
        super().__init__(widget)
        self.changed = 0

    def on_nova_text_area_changed(self, message: NovaTextArea.Changed) -> None:
        self.changed += 1


@pytest.mark.asyncio
async def test_place_cursor_at_byte_moves_the_cursor(tmp_path: Path) -> None:
    data = "héllo\nworld\n".encode()
    path = tmp_path / "doc.txt"
    path.write_bytes(data)
    area = NovaTextArea.open(path)
    host = HostApp(area)
    async with host.run_test() as pilot:
        await pilot.pause()
        assert area.document.wait_indexed(10)
        area.place_cursor_at_byte(7)
        await wait_until(pilot, lambda: area.cursor_byte_offset == 7)
        assert area.cursor_location == (1, 0)
        area.place_cursor_at_byte(2)  # the second byte of `é`
        await wait_until(pilot, lambda: area.cursor_byte_offset == 1)
        assert area.cursor_location == (0, 1)
        area.place_cursor_at_byte(len(data))  # the end of the document is accepted
        await wait_until(pilot, lambda: area.cursor_byte_offset == len(data))
        assert area.cursor_location == (2, 0)


@pytest.mark.asyncio
async def test_place_cursor_at_byte_waits_for_the_scan(tmp_path: Path) -> None:
    area, path = open_with_gated_line_scan(tmp_path, rows=3000)
    data = path.read_bytes()
    target = data.index(b"row 2500")
    host = HostApp(area)
    async with host.run_test(size=(40, 10)) as pilot:
        try:
            await wait_until(pilot, gated_source(area).blocked.is_set)
            area.place_cursor_at_byte(target)
            await pilot.pause()
            progress = area.pending_progress
            assert progress is not None
            assert 0.0 <= progress < 1.0
            assert area.cursor_location == (0, 0)
            release_line_scan(area)
            await wait_until(pilot, lambda: area.pending_progress is None and area.cursor_byte_offset == target)
            assert area.cursor_location == (data[:target].count(b"\n"), 0)
            assert "JumpCompleted" in host.names()
        finally:
            release_line_scan(area)


@pytest.mark.asyncio
async def test_refresh_if_changed_is_false_without_a_foreign_change() -> None:
    area = NovaTextArea("abc\n")
    host = ChangedHost(area)
    async with host.run_test() as pilot:
        await pilot.pause()
        area.insert("x", (0, 0))  # an own edit does not mark the widget stale
        await pilot.pause()
        before = host.changed
        assert area.refresh_if_changed() is False
        await pilot.pause()
        assert host.changed == before


@pytest.mark.asyncio
async def test_refresh_if_changed_consumes_the_stale_flag() -> None:
    area = NovaTextArea("abc\ndef\n")
    host = ChangedHost(area)
    async with host.run_test() as pilot:
        await pilot.pause()
        before = host.changed
        area.document.splice_bytes(4, 4, area.document._table.add(b"NEW\n"))
        assert area._stale is True
        assert area.refresh_if_changed() is True
        assert area._stale is False
        assert area.refresh_if_changed() is False
        await pilot.pause()
        assert area.text == "abc\nNEW\ndef\n"
        assert area.document.get_line(1) == "NEW"
        assert area.wrapped_document.height == 4
        assert host.changed == before + 1


@pytest.mark.asyncio
async def test_report_source_changed_uses_the_failed_state() -> None:
    area = NovaTextArea("abc\n")
    host = HostApp(area)
    async with host.run_test() as pilot:
        await pilot.pause()
        area.report_source_changed(SourceChanged("gone", ChangeKind.MODIFIED))
        await pilot.pause()
        assert host.source_changed == ["gone"]
        assert area._stale_kind is ChangeKind.MODIFIED
        with pytest.raises(EditsLocked, match=STALE):
            area.document.splice_bytes(0, 0, area.document._table.add(b"Q"))
        area.focus()
        await pilot.press("z")
        assert area.text == "abc\n"
        area.report_source_changed(SourceChanged("gone again", ChangeKind.TRUNCATED))
        await pilot.pause()
        assert host.source_changed == ["gone"]
        assert area._stale_kind is ChangeKind.TRUNCATED
