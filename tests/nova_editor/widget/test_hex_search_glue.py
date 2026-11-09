"""The hex view side of searching: `select_range`, `search_origin`, Escape and the `DocumentView` attributes (S0003 ACT6 commit 2)."""

from __future__ import annotations

import dataclasses
from pathlib import Path

import pytest

from nova_editor.document_view import DocumentView
from nova_editor.timed_text_area import TimedNovaTextArea
from nova_editor.widget import HexView
from nova_editor.widget.hex_view import HexCursor
from tests.nova_editor.test_document_view_hex import ViewHost
from tests.nova_editor.widget.hex_helpers import HexHost, opened, write_file

DATA = bytes(range(256)) * 64
"""16 KiB: 1024 rows of 16 bytes."""


@pytest.mark.asyncio
async def test_select_range_selects_bytes_and_scrolls_a_far_target(tmp_path: Path) -> None:
    with opened(write_file(tmp_path, DATA)) as document:
        view = HexView(lambda: document)
        async with HexHost(view).run_test(size=(80, 24)) as pilot:
            await pilot.pause()
            view.select_range(8000, 8004)
            await pilot.pause()
            selection = view.selection
            assert selection is not None
            assert (selection.start, selection.end) == (8000, 8004)
            assert view.cursor == HexCursor(8004, 0, "hex")
            row = 8004 // view.hex_layout.bytes_per_row
            top = view.scroll_offset.y
            assert top <= row < top + view.scrollable_content_region.height
            assert top > 0


@pytest.mark.asyncio
async def test_select_range_backward_puts_the_cursor_at_the_start(tmp_path: Path) -> None:
    with opened(write_file(tmp_path, DATA)) as document:
        view = HexView(lambda: document)
        async with HexHost(view).run_test(size=(80, 24)) as pilot:
            await pilot.pause()
            view.select_range(100, 104, cursor_at_start=True)
            selection = view.selection
            assert selection is not None
            assert (selection.start, selection.end) == (100, 104)
            assert view.cursor.offset == 100


@pytest.mark.asyncio
async def test_select_range_at_the_end_of_the_document(tmp_path: Path) -> None:
    with opened(write_file(tmp_path, DATA)) as document:
        view = HexView(lambda: document)
        async with HexHost(view).run_test(size=(80, 24)) as pilot:
            await pilot.pause()
            view.select_range(len(DATA) - 2, len(DATA))
            selection = view.selection
            assert selection is not None
            assert (selection.start, selection.end) == (len(DATA) - 2, len(DATA))
            assert view.cursor == HexCursor(len(DATA), 0, "hex")
            view.select_range(len(DATA), len(DATA))
            assert view.selection is None
            assert view.cursor.offset == len(DATA)


@pytest.mark.asyncio
async def test_select_range_posts_one_moved_and_one_checkpoint(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    with opened(write_file(tmp_path, DATA)) as document:
        view = HexView(lambda: document)
        host = HexHost(view)
        async with host.run_test(size=(80, 24)) as pilot:
            await pilot.pause()
            calls: list[int] = []
            real = document.history.checkpoint

            def spy() -> None:
                calls.append(1)
                real()

            monkeypatch.setattr(document.history, "checkpoint", spy)
            host.moved.clear()
            view.select_range(50, 60)
            await pilot.pause()
            assert len(host.moved) == 1
            assert len(calls) == 1


@pytest.mark.asyncio
async def test_select_range_clamps(tmp_path: Path) -> None:
    with opened(write_file(tmp_path, DATA)) as document:
        view = HexView(lambda: document)
        async with HexHost(view).run_test(size=(80, 24)) as pilot:
            await pilot.pause()
            view.select_range(-5, len(DATA) + 50)
            selection = view.selection
            assert selection is not None
            assert (selection.start, selection.end) == (0, len(DATA))


@pytest.mark.asyncio
async def test_search_origin_uses_the_selection_ends_and_the_cursor(tmp_path: Path) -> None:
    with opened(write_file(tmp_path, DATA)) as document:
        view = HexView(lambda: document)
        async with HexHost(view).run_test(size=(80, 24)) as pilot:
            await pilot.pause()
            view.set_cursor(33)
            assert view.search_origin(False) == 33
            assert view.search_origin(True) == 33
            view.select_range(40, 48)
            assert view.search_origin(False) == 48
            assert view.search_origin(True) == 40


@pytest.mark.asyncio
async def test_escape_cancels_a_running_search_and_is_inactive_otherwise(tmp_path: Path) -> None:
    with opened(write_file(tmp_path, DATA)) as document:
        view = HexView(lambda: document)
        cancelled: list[int] = []
        running = [False]
        view.searching = lambda: running[0]
        view.cancel_search = lambda: cancelled.append(1)
        async with HexHost(view).run_test(size=(80, 24)) as pilot:
            await pilot.pause()
            assert view.check_action("cancel_search", ()) is False
            await pilot.press("escape")
            assert cancelled == []
            running[0] = True
            assert view.check_action("cancel_search", ()) is True
            await pilot.press("escape")
            assert cancelled == [1]


@pytest.mark.asyncio
async def test_document_view_show_hex_false_cancels_a_running_search(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    path = tmp_path / "f.bin"
    path.write_bytes(b"x" * 4096)
    view, error = DocumentView.open(path, editor_class=TimedNovaTextArea, soft_wrap=False, show_line_numbers=True, config=None, timing_file=None)
    assert error is None
    cancelled: list[int] = []
    async with ViewHost(view).run_test(size=(80, 24)) as pilot:
        await pilot.pause()
        view.show_hex(True)
        await pilot.pause()
        monkeypatch.setattr(view.editor, "cancel_search_now", lambda: cancelled.append(1))
        monkeypatch.setattr(type(view.editor), "searching", property(lambda _self: True))
        view.show_hex(False)
        await pilot.pause()
        assert cancelled == [1]


def test_needle_hex_is_not_a_dataclass_field() -> None:
    view, _ = DocumentView.open(None, editor_class=TimedNovaTextArea, soft_wrap=False, show_line_numbers=True, config=None, timing_file=None)
    assert "needle_hex" not in {field.name for field in dataclasses.fields(DocumentView)}
    assert view.needle_hex is False
