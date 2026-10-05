"""Races between the questions of the editor screen and changes of the file (design D9)."""

from __future__ import annotations

from pathlib import Path

import pytest

from nova_editor.app import NovaEditApp
from nova_editor.core.byte_source import ChangeKind
from nova_editor.screen import EditorScreen
from nova_editor.widget import NovaTextArea
from tests.nova_editor.dialog_helpers import HomeProvider, answer, box_title, has_dialog, open_box, open_file_dialog, open_title
from tests.nova_editor.helpers_view import wait_until
from tests.nova_editor.test_open_flow import pick

SIZE = (100, 30)
CHANGED = "changed on disk, longer\n"


def make_file(tmp_path: Path) -> Path:
    path = tmp_path / "f.txt"
    path.write_text("one\ntwo\n")
    return path


@pytest.mark.asyncio
async def test_saving_a_changed_file_shows_exactly_one_dialog(tmp_path: Path) -> None:
    path = make_file(tmp_path)
    app = NovaEditApp(file_path=path)
    async with app.run_test(size=SIZE) as pilot:
        await pilot.pause()
        await pilot.press("x")
        path.write_text(CHANGED)
        await pilot.press("ctrl+s")  # `save()` posts SourceChanged and SaveNeedsConfirmation for one cause
        await wait_until(pilot, lambda: open_box(app) is not None)
        await pilot.press("escape")
        await wait_until(pilot, lambda: not has_dialog(app))
        await pilot.pause()
        await pilot.pause()
        assert not has_dialog(app)  # no second question was queued behind the first
        screen = app.screen
        assert isinstance(screen, EditorScreen)
        assert screen.document.change_question is False
        await pilot.press("ctrl+s")  # an explicit save of the still stale file asks again
        await wait_until(pilot, lambda: open_box(app) is not None)


@pytest.mark.asyncio
async def test_a_change_that_arrives_while_a_question_is_open_is_asked_afterwards(tmp_path: Path) -> None:
    path = make_file(tmp_path)
    other = tmp_path / "other.txt"
    other.write_text("keep me\n")
    app = NovaEditApp(file_path=path)
    app.POLL_SECONDS = 0.05
    async with app.run_test(size=SIZE) as pilot:
        await pilot.pause()
        await pilot.press("x", "ctrl+shift+s")
        await wait_until(pilot, lambda: open_file_dialog(app) is not None)
        await pilot.press("ctrl+e", "ctrl+u", *"other.txt", "enter")
        await wait_until(pilot, lambda: open_box(app) is not None)
        path.write_text(CHANGED)  # the file of the open document changes under the open question
        await pilot.pause(0.3)  # several polls
        box = open_box(app)
        assert box is not None
        assert box_title(box) == "Overwrite"  # the open question was not replaced
        await pilot.press("escape")
        await wait_until(pilot, lambda: open_title(app) == "File changed")
        await answer(pilot, app, "Keep")
        await wait_until(pilot, lambda: not has_dialog(app))
        assert other.read_text() == "keep me\n"


@pytest.mark.asyncio
async def test_a_message_of_another_editor_is_ignored(tmp_path: Path) -> None:
    app = NovaEditApp(file_path=make_file(tmp_path))
    async with app.run_test(size=SIZE) as pilot:
        await pilot.pause()
        screen = app.screen
        assert isinstance(screen, EditorScreen)
        stranger = NovaTextArea(text="")
        screen.post_message(NovaTextArea.SourceChanged("the file changed on disk (modified)", stranger, ChangeKind.MODIFIED))
        await pilot.pause()
        await pilot.pause()
        assert not has_dialog(app)
        assert screen.document.change_question is False
        assert screen.document.deferred_change is None


@pytest.mark.asyncio
async def test_a_change_while_the_open_dialog_is_shown_is_asked_after_cancel(tmp_path: Path) -> None:
    path = make_file(tmp_path)
    (tmp_path / "home").mkdir()
    app = NovaEditApp(file_path=path, file_provider=HomeProvider(tmp_path / "home"))
    app.POLL_SECONDS = 0.05
    async with app.run_test(size=SIZE) as pilot:
        await pilot.pause()
        await pilot.press("ctrl+o")
        await wait_until(pilot, lambda: open_file_dialog(app) is not None)
        path.write_text(CHANGED)
        await pilot.pause(0.3)  # several polls
        assert open_file_dialog(app) is not None  # the dialog is not replaced
        await pilot.press("escape")
        await wait_until(pilot, lambda: open_title(app) == "File changed")


@pytest.mark.asyncio
async def test_a_change_while_the_open_dialog_is_shown_is_dropped_after_a_successful_open(tmp_path: Path) -> None:
    path = make_file(tmp_path)
    other = tmp_path / "other.txt"
    other.write_text("other\n")
    (tmp_path / "home").mkdir()
    app = NovaEditApp(file_path=path, file_provider=HomeProvider(tmp_path / "home"))
    app.POLL_SECONDS = 0.05
    async with app.run_test(size=SIZE) as pilot:
        await pilot.pause()
        screen = app.screen
        assert isinstance(screen, EditorScreen)
        await pilot.press("ctrl+o")
        await wait_until(pilot, lambda: open_file_dialog(app) is not None)
        path.write_text(CHANGED)  # the open document's file changes while the dialog is shown
        await pilot.pause(0.3)
        await pick(pilot, tmp_path, "other.txt")
        await wait_until(pilot, lambda: app.editor.text == "other\n")
        await pilot.pause(0.3)
        assert not has_dialog(app)  # the question about the replaced document is silently dropped
        assert screen.document.change_question is False
