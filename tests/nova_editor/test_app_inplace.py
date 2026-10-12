"""Pilot tests of the in-place save in the nova_edit screen: the partial-patch message, the progress note and the plain `Saved` note (ACT3 design 4)."""

from __future__ import annotations

import errno
import os
from pathlib import Path

import pytest

from nova_editor.app import NovaEditApp
from nova_editor.status_line import StatusLine
from nova_editor.widget import NovaTextArea
from tests.nova_editor.core.save_helpers import failing_io
from tests.nova_editor.dialog_helpers import box_message, box_title, wait_box
from tests.nova_editor.helpers_view import wait_until
from tests.nova_editor.save_widget_helpers import SETTINGS, Gate, wait_saved

TEXT = "one\ntwo\nthree\n"
OVERWRITES = ("shift+right", "x", "down", "shift+right", "x")
"""Replace one character in each of two rows: two ranges of one byte."""


@pytest.fixture(autouse=True)
def small_chunks(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(NovaTextArea, "save_settings", SETTINGS)


def make_file(tmp_path: Path) -> Path:
    path = tmp_path / "f.txt"
    path.write_text(TEXT)
    return path


def status_of(app: NovaEditApp) -> StatusLine:
    return app.query_one(StatusLine)


@pytest.mark.asyncio
async def test_a_plain_in_place_save_shows_the_saved_note(tmp_path: Path) -> None:
    path = make_file(tmp_path)
    inode = os.stat(path).st_ino
    app = NovaEditApp(file_path=path)
    async with app.run_test() as pilot:
        await pilot.pause()
        await pilot.press(*OVERWRITES, "ctrl+s")
        assert app.editor is not None
        await wait_until(pilot, lambda: path.read_text() == "xne\ntxo\nthree\n")
        await wait_saved(pilot, app.editor)
        status = status_of(app)
        await wait_until(pilot, lambda: status.note is not None and status.note.startswith("Saved"))
        assert status.note is not None
        assert status.note.startswith("Saved  f.txt  ")
        assert os.stat(path).st_ino == inode
        assert not app.editor.modified


@pytest.mark.asyncio
async def test_a_partial_failure_shows_the_partial_text_and_not_the_committed_hint(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(NovaTextArea, "save_io", failing_io("pwrite", 2, errno.EIO))
    path = make_file(tmp_path)
    app = NovaEditApp(file_path=path)
    async with app.run_test() as pilot:
        await pilot.pause()
        await pilot.press(*OVERWRITES, "ctrl+s")
        box = await wait_box(pilot, app)
        assert box_title(box) == "Save failed"
        message = box_message(box)
        assert "(write)" in message
        assert "f.txt may now be partially patched. Your edits are kept; save again to finish." in message
        assert "Reload to read it again" not in message
        assert app.editor is not None
        assert app.editor.modified


@pytest.mark.asyncio
async def test_the_progress_note_drops_esc_cancels_after_the_first_write(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    gate = Gate()
    monkeypatch.setattr(NovaTextArea, "save_io", gate.inplace_io("pwrite", at=2))
    path = make_file(tmp_path)
    app = NovaEditApp(file_path=path)
    try:
        async with app.run_test() as pilot:
            await pilot.pause()
            await pilot.press(*OVERWRITES, "ctrl+s")
            await wait_until(pilot, gate.reached.is_set)  # the first range is written, the second waits
            status = status_of(app)
            await wait_until(pilot, lambda: status.note is not None and status.note.startswith("Saving") and "Esc cancels" not in status.note)
            assert status.note is not None
            assert status.note.startswith("Saving  ")
            gate.release()
            assert app.editor is not None
            await wait_saved(pilot, app.editor)
    finally:
        gate.release()


@pytest.mark.asyncio
async def test_the_progress_note_keeps_esc_cancels_before_the_first_write(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    gate = Gate()
    monkeypatch.setattr(NovaTextArea, "save_io", gate.inplace_io("pwrite", at=1))
    path = make_file(tmp_path)
    app = NovaEditApp(file_path=path)
    try:
        async with app.run_test() as pilot:
            await pilot.pause()
            await pilot.press(*OVERWRITES, "ctrl+s")
            await wait_until(pilot, gate.reached.is_set)
            status = status_of(app)
            await wait_until(pilot, lambda: status.note is not None and status.note.startswith("Saving"))
            assert status.note is not None
            assert status.note.endswith("Esc cancels")
            gate.release()
            assert app.editor is not None
            await wait_saved(pilot, app.editor)
    finally:
        gate.release()
