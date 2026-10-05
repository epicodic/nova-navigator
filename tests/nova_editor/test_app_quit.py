"""Quitting nova_edit with unsaved changes asks first (REQ-16, REQ-19)."""

from __future__ import annotations

import threading
from pathlib import Path

import pytest
from textual.app import App
from textual.pilot import Pilot
from textual.widgets import Input

from nova_editor.app import NovaEditApp
from nova_editor.core.save import SaveIo
from nova_editor.widget import NovaTextArea
from nova_widgets.key_types import KeySequence
from tests.nova_editor.dialog_helpers import answer, box_message, box_title, has_dialog, open_box, open_file_dialog, wait_box
from tests.nova_editor.helpers_view import wait_until
from tests.nova_editor.save_widget_helpers import Gate, wait_saved
from tests.nova_editor.screen_host import write_keys
from tests.nova_editor.test_app_search import Throttle

DISCARD = "Discard the unsaved changes and quit?"
RUNNING = "A save is still running. Quit anyway?"


def make_file(tmp_path: Path) -> Path:
    path = tmp_path / "f.txt"
    path.write_text("one\ntwo\n")
    return path


def count_exits(monkeypatch: pytest.MonkeyPatch) -> list[int]:
    """Record every `exit()` call of the app; the app keeps running, the test context closes it."""
    calls: list[int] = []

    def counting() -> None:
        calls.append(1)

    monkeypatch.setattr(NovaEditApp, "exit", staticmethod(counting))
    return calls


async def question(pilot: Pilot[None], app: App[None]) -> str:
    """Wait for the question box and return its message."""
    box = await wait_box(pilot, app)
    return box_message(box)


@pytest.mark.asyncio
async def test_an_unmodified_document_quits_at_once(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    exits = count_exits(monkeypatch)
    app = NovaEditApp(file_path=make_file(tmp_path))
    async with app.run_test() as pilot:
        await pilot.pause()
        await pilot.press("ctrl+q")
        await wait_until(pilot, lambda: exits == [1])
        assert not has_dialog(app)


@pytest.mark.asyncio
async def test_a_modified_document_asks_and_keeps_running(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    exits = count_exits(monkeypatch)
    app = NovaEditApp(file_path=make_file(tmp_path))
    async with app.run_test() as pilot:
        await pilot.pause()
        await pilot.press("x", "ctrl+q")
        assert await question(pilot, app) == DISCARD
        box = open_box(app)
        assert box is not None
        assert box_title(box) == "Quit"
        assert exits == []


@pytest.mark.asyncio
async def test_escape_keeps_every_edit_and_the_next_ctrl_q_asks_again(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    exits = count_exits(monkeypatch)
    app = NovaEditApp(file_path=make_file(tmp_path))
    async with app.run_test() as pilot:
        await pilot.pause()
        await pilot.press("x", "ctrl+q")
        await question(pilot, app)
        await pilot.press("escape")
        await wait_until(pilot, lambda: not has_dialog(app))
        assert app.editor.modified
        assert app.editor.text.startswith("xone")
        assert app.focused is app.editor
        assert exits == []
        await pilot.press("ctrl+q")
        assert await question(pilot, app) == DISCARD


@pytest.mark.asyncio
async def test_discard_quits_and_leaves_the_file_unchanged(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    exits = count_exits(monkeypatch)
    path = make_file(tmp_path)
    app = NovaEditApp(file_path=path)
    async with app.run_test() as pilot:
        await pilot.pause()
        await pilot.press("x", "ctrl+q")
        await question(pilot, app)
        await answer(pilot, app, "Discard & Quit")
        await wait_until(pilot, lambda: exits == [1])
    assert path.read_bytes() == b"one\ntwo\n"


@pytest.mark.asyncio
async def test_a_running_save_that_cancels_in_time_then_asks(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    path = make_file(tmp_path)
    exits = count_exits(monkeypatch)
    gate = Gate()
    monkeypatch.setattr(NovaTextArea, "save_io", gate.io())
    app = NovaEditApp(file_path=path)
    try:
        async with app.run_test() as pilot:
            await pilot.pause()
            await pilot.press("x", "ctrl+s")
            await wait_until(pilot, gate.reached.is_set)
            threading.Timer(0.1, gate.release).start()
            await pilot.press("ctrl+q")
            await wait_saved(pilot, app.editor)
            assert await question(pilot, app) == DISCARD
            assert exits == []
            assert path.read_bytes() == b"one\ntwo\n"
    finally:
        gate.release()


@pytest.mark.asyncio
async def test_a_save_that_cannot_be_cancelled_in_time_asks_the_second_question(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    exits = count_exits(monkeypatch)
    gate = Gate()
    monkeypatch.setattr(NovaTextArea, "save_io", gate.io())
    monkeypatch.setattr(NovaEditApp, "QUIT_WAIT_SECONDS", 0.1)
    app = NovaEditApp(file_path=make_file(tmp_path))
    try:
        async with app.run_test() as pilot:
            await pilot.pause()
            await pilot.press("x", "ctrl+s")
            await wait_until(pilot, gate.reached.is_set)
            await pilot.press("ctrl+q")
            assert await question(pilot, app) == RUNNING
            assert exits == []
            await pilot.press("escape")
            await wait_until(pilot, lambda: not has_dialog(app))
            assert app.editor.saving
    finally:
        gate.release()


@pytest.mark.asyncio
async def test_a_running_search_is_cancelled_before_the_question(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    app = NovaEditApp(file_path=make_file(tmp_path))
    async with app.run_test() as pilot:
        await pilot.pause()
        throttle = Throttle(app.editor, monkeypatch)
        await pilot.press("x", "ctrl+f")
        await pilot.press(*"two", "enter")
        await wait_until(pilot, lambda: app.editor.searching)
        await pilot.press("ctrl+q")
        assert await question(pilot, app) == DISCARD
        throttle.give()
        await wait_until(pilot, lambda: not app.editor.searching)
        assert open_box(app) is not None


@pytest.mark.asyncio
async def test_a_missing_path_quits_at_once_until_something_is_typed(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    exits = count_exits(monkeypatch)
    path = tmp_path / "new.txt"
    app = NovaEditApp(file_path=path)
    async with app.run_test() as pilot:
        await pilot.pause()
        await pilot.press("ctrl+q")
        await wait_until(pilot, lambda: exits == [1])
    exits.clear()
    typing = NovaEditApp(file_path=path)
    async with typing.run_test() as pilot:
        await pilot.pause()
        await pilot.press("h", "ctrl+q")
        assert await question(pilot, typing) == DISCARD
        await answer(pilot, typing, "Discard & Quit")
        await wait_until(pilot, lambda: exits == [1])
    assert not path.exists()


@pytest.mark.asyncio
async def test_a_file_that_failed_to_load_quits_at_once(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    exits = count_exits(monkeypatch)
    app = NovaEditApp(file_path=tmp_path)  # a directory
    async with app.run_test() as pilot:
        await pilot.pause()
        await pilot.press("ctrl+q")
        await wait_until(pilot, lambda: exits == [1])


@pytest.mark.asyncio
async def test_two_ctrl_q_in_a_row_ask_once(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    exits = count_exits(monkeypatch)
    app = NovaEditApp(file_path=make_file(tmp_path))
    async with app.run_test() as pilot:
        await pilot.pause()
        await pilot.press("x", "ctrl+q", "ctrl+q")
        assert await question(pilot, app) == DISCARD
        await pilot.press("escape")
        await wait_until(pilot, lambda: not has_dialog(app))
        await pilot.pause()
        assert not has_dialog(app)  # the second Ctrl+Q was ignored, not queued
        assert exits == []


@pytest.mark.asyncio
async def test_discard_after_two_ctrl_q_exits_once(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    exits = count_exits(monkeypatch)
    app = NovaEditApp(file_path=make_file(tmp_path))
    async with app.run_test() as pilot:
        await pilot.pause()
        await pilot.press("x", "ctrl+q", "ctrl+q")
        await question(pilot, app)
        await answer(pilot, app, "Discard & Quit")
        await wait_until(pilot, lambda: exits == [1])
        await pilot.pause()
        assert exits == [1]
        assert app.editor.text.startswith("xone")


@pytest.mark.asyncio
async def test_ctrl_q_while_a_dialog_is_open_does_not_exit_and_does_not_discard(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    exits = count_exits(monkeypatch)
    app = NovaEditApp(file_path=make_file(tmp_path))
    async with app.run_test() as pilot:
        await pilot.pause()
        await pilot.press("x", "ctrl+shift+s")
        await wait_until(pilot, lambda: open_file_dialog(app) is not None)
        await pilot.press("ctrl+q")
        await pilot.pause()
        assert exits == []
        assert open_file_dialog(app) is not None
        await pilot.press("escape")
        await wait_until(pilot, lambda: not has_dialog(app))
        assert app.editor.modified
        assert app.editor.text.startswith("xone")
        await pilot.press("ctrl+q")
        assert await question(pilot, app) == DISCARD


@pytest.mark.asyncio
async def test_a_save_that_finishes_in_time_quits_without_asking(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    path = make_file(tmp_path)
    exits = count_exits(monkeypatch)
    reached = threading.Event()
    opened = threading.Event()
    real = SaveIo()

    def fsync_dir(name: str) -> None:
        reached.set()  # the replace happened: a cancel no longer takes effect
        assert opened.wait(10.0)
        real.fsync_dir(name)

    monkeypatch.setattr(NovaTextArea, "save_io", SaveIo(fsync_dir=fsync_dir))
    original_cancel = NovaTextArea.cancel_save

    def cancel_then_release(self: NovaTextArea) -> None:
        original_cancel(self)
        opened.set()

    monkeypatch.setattr(NovaTextArea, "cancel_save", cancel_then_release)
    app = NovaEditApp(file_path=path)
    try:
        async with app.run_test() as pilot:
            await pilot.pause()
            await pilot.press("x", "ctrl+s")
            await wait_until(pilot, reached.is_set)
            await pilot.press("ctrl+q")
            await wait_until(pilot, lambda: exits == [1])
            assert not has_dialog(app)
            assert path.read_bytes() == b"xone\ntwo\n"
    finally:
        opened.set()


@pytest.mark.asyncio
@pytest.mark.parametrize("key", ["ctrl+g", "ctrl+f"])
async def test_ctrl_q_from_a_popup_input_asks_and_escape_returns_to_the_editor(tmp_path: Path, monkeypatch: pytest.MonkeyPatch, key: str) -> None:
    exits = count_exits(monkeypatch)
    app = NovaEditApp(file_path=make_file(tmp_path))
    async with app.run_test() as pilot:
        await pilot.pause()
        await pilot.press("x", key)
        await pilot.pause()
        assert isinstance(app.focused, Input)
        await pilot.press("ctrl+q")
        assert await question(pilot, app) == DISCARD
        assert exits == []
        await pilot.press("escape")
        await wait_until(pilot, lambda: not has_dialog(app))
        assert app.focused is app.editor
        assert app.editor.text.startswith("xone")


@pytest.mark.asyncio
async def test_ctrl_q_asks_when_quit_is_remapped_to_another_key(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    exits = count_exits(monkeypatch)
    keys = write_keys(tmp_path / "cfg", {"editor.quit": KeySequence.parse("ctrl+e")})
    app = NovaEditApp(file_path=make_file(tmp_path), keybindings=keys)
    async with app.run_test() as pilot:
        await pilot.pause()
        await pilot.press("x", "ctrl+q")
        assert await question(pilot, app) == DISCARD
        assert exits == []


@pytest.mark.asyncio
async def test_a_direct_action_quit_asks_when_modified(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    exits = count_exits(monkeypatch)
    app = NovaEditApp(file_path=make_file(tmp_path))
    async with app.run_test() as pilot:
        await pilot.pause()
        await pilot.press("x")
        await app.action_quit()
        assert await question(pilot, app) == DISCARD
        assert exits == []
