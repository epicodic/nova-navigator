"""Quitting nova_edit with unsaved changes asks first (REQ-16)."""

from __future__ import annotations

import threading
from pathlib import Path

import pytest

from nova_editor.app import ConfirmBar, NovaEditApp
from nova_editor.widget import NovaTextArea
from tests.nova_editor.helpers_view import wait_until
from tests.nova_editor.save_widget_helpers import Gate, wait_saved
from tests.nova_editor.test_app_search import Throttle

DISCARD = "Discard the unsaved changes and quit?"
RUNNING = "A save is still running."


def make_file(tmp_path: Path) -> Path:
    path = tmp_path / "f.txt"
    path.write_text("one\ntwo\n")
    return path


def question(app: NovaEditApp) -> str:
    return str(app.query_one(ConfirmBar).render())


@pytest.mark.asyncio
async def test_an_unmodified_document_quits_at_once(tmp_path: Path) -> None:
    app = NovaEditApp(file_path=make_file(tmp_path))
    async with app.run_test() as pilot:
        await pilot.pause()
        await pilot.press("ctrl+q")
        await pilot.pause()
        assert app._exit


@pytest.mark.asyncio
async def test_a_modified_document_asks_and_keeps_running(tmp_path: Path) -> None:
    app = NovaEditApp(file_path=make_file(tmp_path))
    async with app.run_test() as pilot:
        await pilot.pause()
        await pilot.press("x", "ctrl+q")
        await pilot.pause()
        bar = app.query_one(ConfirmBar)
        assert bar.display
        assert DISCARD in question(app)
        assert not app._exit


@pytest.mark.asyncio
async def test_escape_keeps_every_edit_and_the_next_ctrl_q_asks_again(tmp_path: Path) -> None:
    app = NovaEditApp(file_path=make_file(tmp_path))
    async with app.run_test() as pilot:
        await pilot.pause()
        await pilot.press("x", "ctrl+q")
        await pilot.press("escape")
        await pilot.pause()
        assert app.editor is not None
        assert not app.query_one(ConfirmBar).display
        assert app.editor.modified
        assert app.editor.text.startswith("xone")
        assert app.focused is app.editor
        await pilot.press("ctrl+q")
        await pilot.pause()
        assert app.query_one(ConfirmBar).display


@pytest.mark.asyncio
@pytest.mark.parametrize("key", ["q", "Q"])
async def test_q_quits_and_leaves_the_file_unchanged(tmp_path: Path, key: str) -> None:
    path = make_file(tmp_path)
    app = NovaEditApp(file_path=path)
    async with app.run_test() as pilot:
        await pilot.pause()
        await pilot.press("x", "ctrl+q")
        await pilot.press(key)
        await pilot.pause()
        assert app._exit
    assert path.read_bytes() == b"one\ntwo\n"


@pytest.mark.asyncio
async def test_a_running_save_that_cancels_in_time_then_asks(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    path = make_file(tmp_path)
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
            assert app.editor is not None
            await wait_saved(pilot, app.editor)
            await wait_until(pilot, lambda: DISCARD in question(app))
            assert not app._exit
            assert path.read_bytes() == b"one\ntwo\n"
    finally:
        gate.release()


@pytest.mark.asyncio
async def test_a_save_that_cannot_be_cancelled_in_time_asks_the_second_question(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
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
            await wait_until(pilot, lambda: RUNNING in question(app))
            assert not app._exit
            await pilot.press("escape")
            assert not app.query_one(ConfirmBar).display
            assert app.editor is not None
            assert app.editor.saving
    finally:
        gate.release()


@pytest.mark.asyncio
async def test_a_running_search_is_cancelled_before_the_question(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    app = NovaEditApp(file_path=make_file(tmp_path))
    async with app.run_test() as pilot:
        await pilot.pause()
        assert app.editor is not None
        throttle = Throttle(app.editor, monkeypatch)
        await pilot.press("x", "f7")
        await pilot.press(*"two", "enter")
        await wait_until(pilot, lambda: app.editor is not None and app.editor.searching)
        await pilot.press("ctrl+q")
        await wait_until(pilot, lambda: DISCARD in question(app))
        throttle.give()
        await wait_until(pilot, lambda: app.editor is not None and not app.editor.searching)
        assert DISCARD in question(app)


@pytest.mark.asyncio
async def test_a_missing_path_quits_at_once_until_something_is_typed(tmp_path: Path) -> None:
    path = tmp_path / "new.txt"
    app = NovaEditApp(file_path=path)
    async with app.run_test() as pilot:
        await pilot.pause()
        await pilot.press("ctrl+q")
        await pilot.pause()
        assert app._exit
    typing = NovaEditApp(file_path=path)
    async with typing.run_test() as pilot:
        await pilot.pause()
        await pilot.press("h", "ctrl+q")
        await pilot.pause()
        assert DISCARD in question(typing)
        await pilot.press("q")
        await pilot.pause()
        assert typing._exit
    assert not path.exists()


@pytest.mark.asyncio
async def test_a_file_that_failed_to_load_quits_at_once(tmp_path: Path) -> None:
    app = NovaEditApp(file_path=tmp_path)  # a directory
    async with app.run_test() as pilot:
        await pilot.pause()
        await pilot.press("ctrl+q")
        await pilot.pause()
        assert app._exit
