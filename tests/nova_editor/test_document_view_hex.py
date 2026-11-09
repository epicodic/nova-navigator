"""`DocumentView` owns the hex view beside the text widget and shows exactly one of them (S0003 ACT4 task 5, design 7.1)."""

from __future__ import annotations

import dataclasses
from pathlib import Path
from unittest.mock import patch

import pytest
from textual.app import App, ComposeResult

from nova_editor.document_view import DocumentView
from nova_editor.timed_text_area import TimedNovaTextArea
from nova_editor.widget import HexView
from tests.nova_editor.helpers_view import wait_until


class ViewHost(App[None]):
    """Mounts both widgets of one `DocumentView`, as the editor screen does."""

    CSS = "#editor, #hex_view { width: 1fr; height: 1fr; }"

    def __init__(self, view: DocumentView) -> None:
        super().__init__()
        self.view = view

    def compose(self) -> ComposeResult:
        yield self.view.editor
        yield self.view.hex_view


def _open(path: Path | None) -> DocumentView:
    view, error = DocumentView.open(path, editor_class=TimedNovaTextArea, soft_wrap=False, show_line_numbers=True, config=None, timing_file=None)
    assert error is None
    return view


def test_hex_view_is_an_attribute_not_a_field() -> None:
    view = _open(None)
    assert isinstance(view.hex_view, HexView)
    assert view.hex_view.id == "hex_view"
    assert "hex_view" not in {field.name for field in dataclasses.fields(DocumentView)}


def test_a_new_view_shows_the_text_widget() -> None:
    view = _open(None)
    assert view.shown is view.editor
    assert view.hex_mode is False
    assert view.hex_view.display is False
    assert view.editor.display is True


def test_two_views_do_not_share_the_hex_widget() -> None:
    first = _open(None)
    second = _open(None)
    assert first.hex_view is not second.hex_view


@pytest.mark.asyncio
async def test_show_hex_flips_the_displays_and_is_idempotent(tmp_path: Path) -> None:
    path = tmp_path / "f.txt"
    path.write_text("one\ntwo\n")
    view = _open(path)
    async with ViewHost(view).run_test(size=(80, 24)) as pilot:
        await pilot.pause()
        for _ in range(2):
            view.show_hex(True)
            await pilot.pause()
            assert view.hex_mode is True
            assert view.shown is view.hex_view
            assert view.hex_view.display is True
            assert view.editor.display is False
            assert pilot.app.focused is view.hex_view
        for _ in range(2):
            view.show_hex(False)
            await pilot.pause()
            assert view.hex_mode is False
            assert view.shown is view.editor
            assert view.hex_view.display is False
            assert view.editor.display is True
            assert pilot.app.focused is view.editor


@pytest.mark.asyncio
async def test_show_hex_calls_checkpoint_first(tmp_path: Path) -> None:
    path = tmp_path / "f.txt"
    path.write_text("one\n")
    view = _open(path)
    async with ViewHost(view).run_test(size=(80, 24)) as pilot:
        await pilot.pause()
        history = view.editor.document.history
        modes: list[bool] = []
        real = history.checkpoint

        def spy() -> None:
            modes.append(view.hex_mode)
            real()

        with patch.object(history, "checkpoint", side_effect=spy):
            view.show_hex(True)
            await pilot.pause()
            view.show_hex(False)
            await pilot.pause()
        assert modes[:1] == [False]  # before the displays flip
        assert True in modes  # the way back checkpoints too, again before the flip


@pytest.mark.asyncio
async def test_focus_shown_focuses_the_visible_widget(tmp_path: Path) -> None:
    path = tmp_path / "f.txt"
    path.write_text("one\n")
    view = _open(path)
    async with ViewHost(view).run_test(size=(80, 24)) as pilot:
        await pilot.pause()
        view.show_hex(True)
        await pilot.pause()
        pilot.app.set_focus(None)
        view.focus_shown()
        await pilot.pause()
        assert pilot.app.focused is view.hex_view


@pytest.mark.asyncio
async def test_hex_view_reads_the_documents_current_document(tmp_path: Path) -> None:
    path = tmp_path / "f.txt"
    path.write_text("one\n")
    view = _open(path)
    async with ViewHost(view).run_test(size=(80, 24)) as pilot:
        await pilot.pause()
        view.show_hex(True)
        await pilot.pause()
        old = view.editor.document
        view.editor.load_text("a longer document\n")  # the widget replaces its `LazyDocument`
        assert view.editor.document is not old
        view.hex_view.follow_document()
        await wait_until(pilot, lambda: view.hex_view.length == len("a longer document\n"))
        assert view.hex_view.render_line(0).text.startswith("00000000  61 20 6C 6F")


@pytest.mark.asyncio
async def test_a_switch_that_raises_leaves_no_pending_transfer(tmp_path: Path) -> None:
    """A failing call inside `show_hex` must not leave the offset of the aborted switch for the next one."""
    path = tmp_path / "f.bin"
    path.write_bytes(b"a" * 4096)
    view = _open(path)
    async with ViewHost(view).run_test(size=(80, 24)) as pilot:
        await pilot.pause()
        view.show_hex(True)
        await pilot.pause()
        view.hex_view.set_cursor(1000)
        with patch.object(view.editor, "refresh_if_changed", side_effect=RuntimeError("boom")), pytest.raises(RuntimeError):
            view.show_hex(False)
        assert view.hex_mode is True
        view.hex_view.set_cursor(2000)
        view.show_hex(False)
        await wait_until(pilot, lambda: pilot.app.focused is view.editor and view.editor.pending_progress is None)
        assert view.editor.cursor_byte_offset == 2000


@pytest.mark.asyncio
async def test_the_hex_view_follows_the_read_only_flag_of_the_text_widget(tmp_path: Path) -> None:
    path = tmp_path / "f.bin"
    path.write_bytes(b"abcd")
    view = _open(path)
    async with ViewHost(view).run_test(size=(80, 24)) as pilot:
        await pilot.pause()
        assert view.hex_view.is_read_only() is False
        view.editor.read_only = True
        assert view.hex_view.is_read_only() is True
        view.show_hex(True)
        await pilot.pause()
        await pilot.press("1", "delete")
        await pilot.pause()
        assert view.editor.document.read_bytes(0, 4) == b"abcd"
        view.editor.read_only = False
        await pilot.press("1")
        await pilot.pause()
        assert view.editor.document.read_bytes(0, 4) == b"\x11bcd"
