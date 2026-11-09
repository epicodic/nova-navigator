"""Binary detection at open (S0003 ACT7, REQ-3, REQ-4): `DocumentView.open`, the screen start and the Open flow."""

from __future__ import annotations

import threading
from pathlib import Path
from unittest.mock import patch

import pytest
from textual.pilot import Pilot

from nova_editor.app import NovaEditApp
from nova_editor.core.binary_detect import HEAD_LIMIT
from nova_editor.document._lazy_document import LazyDocument
from nova_editor.document_view import DocumentView
from nova_editor.screen import EditorScreen
from nova_editor.timed_text_area import TimedNovaTextArea
from tests.nova_editor.dialog_helpers import HomeProvider, open_file_dialog
from tests.nova_editor.helpers_view import wait_until
from tests.nova_editor.test_open_flow import pick
from tests.nova_editor.view_menu import view_marks

SMALL = HEAD_LIMIT + 4096
"""A file this long is read into memory (a `BytesSource`)."""
LARGE = 2 * 1024 * 1024
"""A file this long is read by blocks (a `PreadSource`)."""
SIZE = (100, 30)


@pytest.fixture(autouse=True)
def quiet(monkeypatch: pytest.MonkeyPatch) -> None:
    """No poll during a test."""
    monkeypatch.setattr(NovaEditApp, "POLL_SECONDS", 3600.0)


def make(tmp_path: Path, length: int, nul_at: int | None, name: str = "f.dat") -> Path:
    """A file of `length` bytes of `a` with a NUL byte at `nul_at` (none for `None`)."""
    data = bytearray(b"a" * length)
    if nul_at is not None:
        data[nul_at] = 0
    path = tmp_path / name
    path.write_bytes(bytes(data))
    return path


def open_view(path: Path | None, *, force_hex: bool = False) -> DocumentView:
    view, error = DocumentView.open(path, editor_class=TimedNovaTextArea, soft_wrap=False, show_line_numbers=True, config=None, timing_file=None, force_hex=force_hex)
    assert error is None or force_hex or path is not None
    return view


def open_plain(path: Path | None) -> DocumentView:
    view, _error = DocumentView.open(path, editor_class=TimedNovaTextArea, soft_wrap=False, show_line_numbers=True, config=None, timing_file=None)
    return view


def app_for(tmp_path: Path, path: Path | None) -> NovaEditApp:
    home = tmp_path / "home"
    home.mkdir(exist_ok=True)
    return NovaEditApp(path=path, file_provider=HomeProvider(home))


def screen_of(app: NovaEditApp) -> EditorScreen:
    screen = app.screen
    assert isinstance(screen, EditorScreen)
    return screen


@pytest.mark.parametrize("length", [SMALL, LARGE])
@pytest.mark.parametrize("nul_at", [0, HEAD_LIMIT - 1])
def test_nul_in_the_first_64_kib_opens_in_hex(tmp_path: Path, length: int, nul_at: int) -> None:
    view = open_plain(make(tmp_path, length, nul_at))
    assert view.hex_mode
    assert view.hex_view.display
    assert not view.editor.display


@pytest.mark.parametrize("length", [HEAD_LIMIT + 1, LARGE])
def test_first_nul_at_65536_opens_in_text(tmp_path: Path, length: int) -> None:
    view = open_plain(make(tmp_path, length, HEAD_LIMIT))
    assert not view.hex_mode
    assert view.editor.display


def test_empty_and_plain_text_open_in_text(tmp_path: Path) -> None:
    empty = tmp_path / "empty"
    empty.write_bytes(b"")
    text = tmp_path / "t.txt"
    text.write_text("hello\nworld\n")
    assert not open_plain(empty).hex_mode
    assert not open_plain(text).hex_mode
    assert not open_plain(None).hex_mode


def test_force_hex_opens_a_text_file_in_hex(tmp_path: Path) -> None:
    text = tmp_path / "t.txt"
    text.write_text("hello\n")
    view = open_view(text, force_hex=True)
    assert view.hex_mode
    assert not view.editor.display


def test_force_hex_without_a_path_and_for_a_failed_load(tmp_path: Path) -> None:
    assert open_view(None, force_hex=True).hex_mode
    view, error = DocumentView.open(tmp_path / "missing", editor_class=TimedNovaTextArea, soft_wrap=False, show_line_numbers=True, config=None, timing_file=None, force_hex=True)
    assert error is not None
    assert view.load_state == "new"
    assert view.hex_mode
    view, error = DocumentView.open(tmp_path, editor_class=TimedNovaTextArea, soft_wrap=False, show_line_numbers=True, config=None, timing_file=None, force_hex=True)
    assert error is not None
    assert view.load_state == "failed"
    assert view.hex_mode


def _opening_thread_reads(path: Path, *, force_hex: bool) -> list[tuple[int, int]]:
    calls: list[tuple[int, int]] = []
    opener = threading.get_ident()
    original = LazyDocument.read_bytes

    def recording(self: LazyDocument, offset: int, size: int, *, cache: bool = True) -> bytes:
        if threading.get_ident() == opener:
            calls.append((offset, size))
        return original(self, offset, size, cache=cache)

    with patch.object(LazyDocument, "read_bytes", recording):
        view = open_view(path, force_hex=force_hex)
    assert view.editor.document is not None
    return calls


@pytest.mark.parametrize("length", [SMALL, LARGE])
def test_the_head_is_read_once_from_the_opening_thread(tmp_path: Path, length: int) -> None:
    assert _opening_thread_reads(make(tmp_path, length, None), force_hex=False) == [(0, HEAD_LIMIT)]


def test_force_hex_reads_no_head(tmp_path: Path) -> None:
    assert _opening_thread_reads(make(tmp_path, SMALL, None), force_hex=True) == []


@pytest.mark.asyncio
async def test_a_started_in_hex_view_holds_the_hidden_text_widget(tmp_path: Path) -> None:
    app = app_for(tmp_path, make(tmp_path, SMALL, 0))
    async with app.run_test(size=SIZE) as pilot:
        await pilot.pause()
        view = screen_of(app).document
        assert view.hex_mode
        assert view.editor.is_attached
        assert not view.editor.display
        assert view.hex_view.length == SMALL


@pytest.mark.asyncio
async def test_a_binary_file_starts_in_hex_with_the_hex_view_focused(tmp_path: Path) -> None:
    app = app_for(tmp_path, make(tmp_path, SMALL, 5))
    async with app.run_test(size=SIZE) as pilot:
        await pilot.pause()
        screen = screen_of(app)
        await wait_until(pilot, lambda: screen.focused is screen.document.hex_view)
        assert screen.document.hex_mode


@pytest.mark.asyncio
async def test_a_text_file_starts_in_text(tmp_path: Path) -> None:
    path = tmp_path / "t.txt"
    path.write_text("one\ntwo\n")
    app = app_for(tmp_path, path)
    async with app.run_test(size=SIZE) as pilot:
        await pilot.pause()
        screen = screen_of(app)
        assert not screen.document.hex_mode
        await wait_until(pilot, lambda: screen.focused is screen.document.editor)


@pytest.mark.asyncio
async def test_f4_from_a_binary_start_shows_the_text_view_and_back(tmp_path: Path) -> None:
    app = app_for(tmp_path, make(tmp_path, SMALL, 5))
    async with app.run_test(size=SIZE) as pilot:
        await pilot.pause()
        screen = screen_of(app)
        await pilot.press("f4")
        await wait_until(pilot, lambda: not screen.document.hex_mode and screen.focused is screen.document.editor)
        await pilot.press("f4")
        await wait_until(pilot, lambda: screen.document.hex_mode and screen.focused is screen.document.hex_view)


@pytest.mark.asyncio
async def test_the_hex_mode_item_is_checked_at_a_binary_start(tmp_path: Path) -> None:
    app = app_for(tmp_path, make(tmp_path, SMALL, 5))
    async with app.run_test(size=(80, 24)) as pilot:
        await pilot.pause()
        marks = await view_marks(pilot, screen_of(app))
        assert marks["Hex Mode"] is True


async def open_through_the_dialog(pilot: Pilot[None], app: NovaEditApp, directory: Path, name: str) -> None:
    await pilot.press("ctrl+o")
    await wait_until(pilot, lambda: open_file_dialog(app) is not None)
    await pick(pilot, directory, name)


@pytest.mark.asyncio
async def test_ctrl_o_detects_in_both_directions(tmp_path: Path) -> None:
    first = tmp_path / "a_first.txt"
    first.write_text("one\n")
    make(tmp_path, SMALL, 3, name="b_binary.dat")
    app = app_for(tmp_path, first)
    async with app.run_test(size=SIZE) as pilot:
        await pilot.pause()
        screen = screen_of(app)
        assert not screen.document.hex_mode
        await open_through_the_dialog(pilot, app, tmp_path, "b_binary.dat")
        await wait_until(pilot, lambda: screen.document.hex_mode and screen.focused is screen.document.hex_view)
        await open_through_the_dialog(pilot, app, tmp_path, "a_first.txt")
        await wait_until(pilot, lambda: not screen.document.hex_mode and screen.focused is screen.document.editor)


@pytest.mark.asyncio
async def test_ctrl_o_after_a_hex_flag_start_uses_detection(tmp_path: Path) -> None:
    first = tmp_path / "a_first.txt"
    first.write_text("one\n")
    second = tmp_path / "b_second.txt"
    second.write_text("two\n")
    app = NovaEditApp(path=first, file_provider=HomeProvider(tmp_path), start_hex=True)
    async with app.run_test(size=SIZE) as pilot:
        await pilot.pause()
        screen = screen_of(app)
        assert screen.document.hex_mode
        await open_through_the_dialog(pilot, app, tmp_path, "b_second.txt")
        await wait_until(pilot, lambda: screen.document.file_path == second and not screen.document.hex_mode)
