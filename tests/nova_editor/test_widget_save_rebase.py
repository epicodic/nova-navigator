"""Pilot tests of undo, redo and the clipboard across a save from `NovaTextArea` (ACT5 task 14, REQ-10)."""

from __future__ import annotations

from pathlib import Path

import pytest

from nova_editor.core.save import SaveSettings
from nova_editor.document._document import Selection
from nova_editor.widget import NovaTextArea
from tests.nova_editor.save_widget_helpers import SETTINGS, SaveHost, text_of, wait_saved

BODY = "".join(f"row {n} {'x' * (n % 9)}\n" for n in range(60))
BIG_ROWS = 8_000


@pytest.fixture(autouse=True)
def small_chunks(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(NovaTextArea, "save_settings", SETTINGS)


@pytest.mark.asyncio
async def test_type_save_undo_restores_the_pre_save_content_and_redo_returns(tmp_path: Path) -> None:
    path = tmp_path / "doc.txt"
    path.write_text(BODY)
    area = NovaTextArea.open(path)
    host = SaveHost(area)
    async with host.run_test() as pilot:
        await pilot.pause()
        area.focus()
        original = text_of(area)
        await pilot.press("x", "y")
        typed = text_of(area)
        assert area.save() is True
        await wait_saved(pilot, area)
        assert not area.modified
        await pilot.press("ctrl+z")
        assert text_of(area) == original
        assert area.modified
        await pilot.press("ctrl+y")
        assert text_of(area) == typed
        assert not area.modified
        assert path.read_bytes() == typed


@pytest.mark.asyncio
async def test_clipboard_copied_before_a_save_pastes_after_it(tmp_path: Path) -> None:
    path = tmp_path / "doc.txt"
    path.write_text(BODY)
    area = NovaTextArea.open(path)
    host = SaveHost(area)
    async with host.run_test() as pilot:
        await pilot.pause()
        area.focus()
        area.selection = Selection((3, 2), (9, 4))
        area.action_copy()
        rows = BODY.split("\n")
        copied = "\n".join([rows[3][2:], *rows[4:9], rows[9][:4]])
        area.selection = Selection.cursor((0, 0))
        await pilot.press("x")
        assert area.save() is True
        await wait_saved(pilot, area)
        area.selection = Selection.cursor((20, 3))
        area.action_paste()
        expected = "x" + BODY
        offset = sum(len(row) + 1 for row in expected.split("\n")[:20]) + 3
        assert text_of(area).decode() == expected[:offset] + copied + expected[offset:]


@pytest.mark.asyncio
async def test_a_large_deletion_is_undone_after_a_save_when_the_orphans_exceed_the_copy_limit(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr("nova_editor.document._lazy_document.UNDO_COPY_LIMIT", 4)
    path = tmp_path / "doc.txt"
    path.write_text(BODY)
    area = NovaTextArea.open(path)
    host = SaveHost(area)
    async with host.run_test() as pilot:
        await pilot.pause()
        area.focus()
        original = text_of(area)
        area.selection = Selection((5, 0), (40, 3))
        await pilot.press("delete")
        deleted = text_of(area)
        assert deleted != original
        assert area.save() is True
        await wait_saved(pilot, area)
        await pilot.press("ctrl+z")
        assert text_of(area) == original
        await pilot.press("ctrl+y")
        assert text_of(area) == deleted
        assert path.read_bytes() == deleted
        await pilot.press("ctrl+z")
        assert text_of(area) == original


@pytest.mark.asyncio
async def test_a_file_read_through_pread_saves_and_undoes(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(NovaTextArea, "save_settings", SaveSettings(chunk=4096, fsync_every=1 << 20))
    monkeypatch.setattr("nova_editor.widget._text_area.SMALL_FILE_LIMIT", 1024)
    path = tmp_path / "big.txt"
    path.write_text("".join(f"row {n} {'y' * (n % 11)}\n" for n in range(BIG_ROWS)))
    area = NovaTextArea.open(path)
    host = SaveHost(area)
    async with host.run_test() as pilot:
        await pilot.pause()
        area.focus()
        assert area.document.wait_indexed(10)
        original = text_of(area)
        await pilot.press("x")
        typed = text_of(area)
        assert area.save() is True
        await wait_saved(pilot, area)
        assert path.read_bytes() == typed
        await pilot.press("ctrl+z")
        assert text_of(area) == original
