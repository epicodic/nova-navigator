"""A hex copy survives an in-place and a streaming save: paste and undo give correct bytes (S0003 ACT5 commit 5, design 8)."""

from __future__ import annotations

import os
from pathlib import Path

import pytest
from textual.app import ComposeResult

from nova_editor.core.save import SaveSettings
from nova_editor.widget import HexView, NovaTextArea
from tests.nova_editor.save_widget_helpers import Gate, SaveHost, wait_saved

ORIGINAL = bytes(range(256)) * 4200
"""More than `SMALL_FILE_LIMIT`: such a file is read through a `PreadSource`, so a patched range really changes what an old piece reads."""


class BothHost(SaveHost):
    """Hosts a text widget and a hex view over its document, wired the way `DocumentView` wires them."""

    def __init__(self, area: NovaTextArea) -> None:
        super().__init__(area)
        self.view = HexView(lambda: area.document)
        self.view.clipboard_owner = lambda: area

    def compose(self) -> ComposeResult:
        yield from super().compose()
        yield self.view


@pytest.fixture(autouse=True)
def small_chunks(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(NovaTextArea, "save_settings", SaveSettings(chunk=1 << 16, fsync_every=64))


def _data(area: NovaTextArea) -> bytes:
    return area.document.read_bytes(0, area.document.length)


def _select(view: HexView, start: int, end: int) -> None:
    view.set_cursor(start)
    view.set_cursor(end, select=True)


@pytest.mark.asyncio
async def test_paste_and_undo_after_an_in_place_save_give_the_original_bytes(tmp_path: Path) -> None:
    path = tmp_path / "doc.bin"
    path.write_bytes(ORIGINAL)
    inode = os.stat(path).st_ino
    area = NovaTextArea.open(path)
    host = BothHost(area)
    async with host.run_test(size=(120, 30)) as pilot:
        await pilot.pause()
        view = host.view
        view.focus()
        _select(view, 0, 4)
        await pilot.press("ctrl+c")
        assert host.clipboard == "00 01 02 03"
        assert area._clipboard_record is not None
        view.set_cursor(0)
        await pilot.press("e", "e", "e", "e", "e", "e", "e", "e")  # overwrite [0, 4) by EE EE EE EE; the clipboard stays
        overwritten = bytes([0xEE] * 4) + ORIGINAL[4:]
        assert _data(area) == overwritten
        assert area.save() is True
        await wait_saved(pilot, area)
        assert path.read_bytes() == overwritten
        assert os.stat(path).st_ino == inode  # in place: the lengths are equal
        record = area._clipboard_record
        assert record is not None  # redirected with the history, not dropped
        patched = area.document._patched
        assert patched
        for piece in record.content.pieces:  # no piece of the hex copy still names a range the save overwrote
            assert piece.src != 0 or not any(piece.a < b and a < piece.b for a, b in patched)
        assert not any(a < b2 and a2 < b for a, b in area.history.original_ranges() for a2, b2 in patched)
        view.set_cursor(len(overwritten))
        await pilot.press("ctrl+v")
        assert _data(area) == overwritten + ORIGINAL[:4]  # the bytes of the first copy, read back through the redirect
        await pilot.press("ctrl+z", "ctrl+z")
        assert _data(area) == ORIGINAL
        assert path.read_bytes() == overwritten
        await pilot.press("ctrl+y", "ctrl+y")
        assert _data(area) == overwritten + ORIGINAL[:4]


@pytest.mark.asyncio
async def test_paste_after_a_streaming_save_gives_the_original_bytes(tmp_path: Path) -> None:
    path = tmp_path / "doc.bin"
    path.write_bytes(ORIGINAL)
    area = NovaTextArea.open(path)
    host = BothHost(area)
    async with host.run_test(size=(120, 30)) as pilot:
        await pilot.pause()
        view = host.view
        view.focus()
        _select(view, 0, 4)
        await pilot.press("ctrl+c")
        view.set_cursor(0)
        await pilot.press("insert", "e", "e")  # inserts EE: the length changes, so the save streams
        inode = os.stat(path).st_ino
        assert area.save() is True
        await wait_saved(pilot, area)
        assert os.stat(path).st_ino != inode
        assert path.read_bytes() == b"\xee" + ORIGINAL
        assert area._clipboard_record is not None
        view.set_cursor(0)
        await pilot.press("ctrl+v")
        assert _data(area) == ORIGINAL[:4] + b"\xee" + ORIGINAL
        await pilot.press("ctrl+z", "ctrl+z")
        assert _data(area) == ORIGINAL


@pytest.mark.asyncio
async def test_a_copy_made_during_an_in_place_save_is_not_recorded(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    gate = Gate()
    monkeypatch.setattr(NovaTextArea, "save_io", gate.inplace_io("pwrite"))
    path = tmp_path / "doc.bin"
    path.write_bytes(ORIGINAL)
    area = NovaTextArea.open(path)
    host = BothHost(area)
    async with host.run_test(size=(120, 30)) as pilot:
        await pilot.pause()
        view = host.view
        view.focus()
        view.set_cursor(0)
        await pilot.press("e", "e")
        assert area.save() is True
        assert gate.reached.wait(10)
        _select(view, 4, 8)
        await pilot.press("ctrl+c")
        assert host.clipboard == "04 05 06 07"
        assert area._clipboard_record is None  # a record made now would name the old bytes
        gate.release()
        await wait_saved(pilot, area)
        view.set_cursor(len(ORIGINAL))
        await pilot.press("ctrl+v")  # falls back to the system text
        assert _data(area) == bytes([0xEE]) + ORIGINAL[1:] + ORIGINAL[4:8]
