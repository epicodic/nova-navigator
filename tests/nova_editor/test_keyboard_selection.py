"""Keyboard selection with Shift+PageUp, Shift+PageDown, Shift+Ctrl+Home and Shift+Ctrl+End."""

from __future__ import annotations

from pathlib import Path

import pytest
from textual.widgets.text_area import Selection

from nova_editor.widget import NovaTextArea
from tests.nova_editor.helpers_view import (
    HostApp,
    gated_source,
    open_with_gated_line_scan,
    release_line_scan,
    wait_until,
)

_ROWS = 60


def _text() -> str:
    return "".join(f"row {n}\n" for n in range(_ROWS))


@pytest.mark.asyncio
async def test_shift_pagedown_selects_one_page_down() -> None:
    area = NovaTextArea(text=_text())
    async with HostApp(area).run_test(size=(40, 10)) as pilot:
        await pilot.pause()
        await pilot.press("down", "right")
        await pilot.press("shift+pagedown")
        await pilot.pause()
        assert area.selection.start == (1, 1)
        assert area.selection.end == (1 + area.content_size.height, 1)


@pytest.mark.asyncio
async def test_shift_pageup_selects_one_page_up() -> None:
    area = NovaTextArea(text=_text())
    async with HostApp(area).run_test(size=(40, 10)) as pilot:
        await pilot.pause()
        area.move_cursor((30, 2))
        await pilot.press("shift+pageup")
        await pilot.pause()
        assert area.selection.start == (30, 2)
        assert area.selection.end == (30 - area.content_size.height, 2)


@pytest.mark.asyncio
async def test_plain_pagedown_still_collapses_the_selection() -> None:
    area = NovaTextArea(text=_text())
    async with HostApp(area).run_test(size=(40, 10)) as pilot:
        await pilot.pause()
        await pilot.press("shift+right", "shift+right", "pagedown")
        await pilot.pause()
        assert area.selection.is_empty


@pytest.mark.asyncio
async def test_shift_ctrl_home_selects_to_the_document_start() -> None:
    area = NovaTextArea(text=_text())
    async with HostApp(area).run_test(size=(40, 10)) as pilot:
        await pilot.pause()
        area.move_cursor((5, 3))
        await pilot.press("ctrl+shift+home")
        await wait_until(pilot, lambda: area.selection == Selection((5, 3), (0, 0)))


@pytest.mark.asyncio
async def test_shift_ctrl_end_selects_to_the_document_end() -> None:
    area = NovaTextArea(text=_text())
    async with HostApp(area).run_test(size=(40, 10)) as pilot:
        await pilot.pause()
        area.move_cursor((5, 3))
        await pilot.press("ctrl+shift+end")
        await wait_until(pilot, lambda: area.selection == Selection((5, 3), (_ROWS, 0)))


@pytest.mark.asyncio
async def test_shift_ctrl_home_extends_an_existing_selection_from_its_anchor() -> None:
    area = NovaTextArea(text=_text())
    async with HostApp(area).run_test(size=(40, 10)) as pilot:
        await pilot.pause()
        area.move_cursor((4, 0))
        await pilot.press("shift+down", "shift+down")
        await pilot.press("ctrl+shift+home")
        await wait_until(pilot, lambda: area.selection == Selection((4, 0), (0, 0)))


@pytest.mark.asyncio
async def test_plain_ctrl_end_still_collapses_the_selection() -> None:
    area = NovaTextArea(text=_text())
    async with HostApp(area).run_test(size=(40, 10)) as pilot:
        await pilot.pause()
        await pilot.press("shift+down", "ctrl+end")
        await wait_until(pilot, lambda: area.selection == Selection.cursor((_ROWS, 0)))


@pytest.mark.asyncio
async def test_shift_ctrl_end_stays_pending_until_the_scan_completes(tmp_path: Path) -> None:
    area, _ = open_with_gated_line_scan(tmp_path, rows=3000)
    async with HostApp(area).run_test(size=(40, 10)) as pilot:
        await wait_until(pilot, gated_source(area).blocked.is_set)
        area.move_cursor((2, 1))
        await pilot.press("ctrl+shift+end")
        await pilot.pause()
        assert area.pending_progress is not None
        assert area.selection == Selection.cursor((2, 1))
        release_line_scan(area)
        await wait_until(pilot, lambda: area.pending_progress is None)
        await wait_until(pilot, lambda: area.indexing_complete)
        assert area.selection == Selection((2, 1), (area.line_count - 1, 0))


@pytest.mark.asyncio
async def test_escape_cancels_a_pending_shift_ctrl_end(tmp_path: Path) -> None:
    area, _ = open_with_gated_line_scan(tmp_path, rows=3000)
    async with HostApp(area).run_test(size=(40, 10)) as pilot:
        await wait_until(pilot, gated_source(area).blocked.is_set)
        area.move_cursor((2, 1))
        await pilot.press("ctrl+shift+end")
        await pilot.pause()
        assert area.pending_progress is not None
        area.cancel_pending()
        await pilot.pause()
        assert area.pending_progress is None
        release_line_scan(area)
        await wait_until(pilot, lambda: area.indexing_complete)
        assert area.selection == Selection.cursor((2, 1))
