"""Esc closes the embedded editor; Ctrl+Home and Ctrl+End move the cursor to the ends of the document."""

from __future__ import annotations

from pathlib import Path

import pytest

from .helpers_view import wait_until
from .screen_host import EditorScreenHost


@pytest.mark.asyncio
async def test_escape_closes_the_embedded_editor(tmp_path: Path) -> None:
    path = tmp_path / "a.txt"
    path.write_text("one\n")
    host = EditorScreenHost(path, standalone=False)
    async with host.run_test() as pilot:
        await pilot.pause()
        await pilot.press("escape")
        await wait_until(pilot, lambda: host.closed == 1)


@pytest.mark.asyncio
async def test_escape_does_not_close_the_standalone_editor(tmp_path: Path) -> None:
    path = tmp_path / "a.txt"
    path.write_text("one\n")
    host = EditorScreenHost(path, standalone=True)
    async with host.run_test() as pilot:
        await pilot.pause()
        await pilot.press("escape")
        await pilot.pause(delay=0.2)
        assert host.closed == 0


@pytest.mark.asyncio
async def test_escape_closes_the_find_popup_not_the_editor(tmp_path: Path) -> None:
    path = tmp_path / "a.txt"
    path.write_text("one\n")
    host = EditorScreenHost(path, standalone=False)
    async with host.run_test() as pilot:
        await pilot.pause()
        screen = host.screen_instance
        assert screen is not None
        screen.action_find()
        await pilot.pause()
        assert screen.find_popup.display
        await pilot.press("escape")
        await pilot.pause(delay=0.2)
        assert not screen.find_popup.display
        assert host.closed == 0


@pytest.mark.asyncio
async def test_ctrl_end_and_ctrl_home_move_to_the_ends(tmp_path: Path) -> None:
    path = tmp_path / "a.txt"
    path.write_text("one\ntwo\nthree\n")
    host = EditorScreenHost(path, standalone=True)
    async with host.run_test() as pilot:
        await pilot.pause()
        screen = host.screen_instance
        assert screen is not None
        editor = screen.document.editor
        await pilot.press("ctrl+end")
        await wait_until(pilot, lambda: editor.cursor_location == (3, 0))
        await pilot.press("ctrl+home")
        await wait_until(pilot, lambda: editor.cursor_location == (0, 0))
