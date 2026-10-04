"""The questions of the editor screen (REQ-19): their texts, their buttons and their answers."""

from __future__ import annotations

from collections.abc import Callable
from pathlib import Path

import pytest

from nova_editor.core.byte_source import ChangeKind
from nova_editor.decisions import failure_box, file_question, open_question, quit_question, reload_question
from nova_widgets.flat_widgets import Button
from nova_widgets.message_box import MessageBox
from nova_widgets.response import Response
from tests.nova_editor.dialog_helpers import BoxHost, answer, box_buttons, box_message, box_title, open_box

TARGET = Path("/work/notes.txt")
SIZE = (80, 24)

Case = tuple[Callable[[], MessageBox], str, str, list[str]]

CASES: dict[str, Case] = {
    "quit": (
        lambda: quit_question(save_running=False, standalone=True),
        "Quit",
        "Discard the unsaved changes and quit?",
        ["Cancel", "Discard & Quit"],
    ),
    "close": (
        lambda: quit_question(save_running=False, standalone=False),
        "Close",
        "Discard the unsaved changes and close?",
        ["Cancel", "Discard & Close"],
    ),
    "quit-running": (
        lambda: quit_question(save_running=True, standalone=True),
        "Quit",
        "A save is still running. Quit anyway?",
        ["Cancel", "Quit Anyway"],
    ),
    "close-running": (
        lambda: quit_question(save_running=True, standalone=False),
        "Close",
        "A save is still running. Close anyway?",
        ["Cancel", "Close Anyway"],
    ),
    "open": (
        lambda: open_question("other.txt"),
        "Open",
        "Discard the unsaved changes and open other.txt?",
        ["Cancel", "Discard & Open"],
    ),
    "reload": (reload_question, "Reload", "Discard the edits and reload?", ["Cancel", "Discard & Reload"]),
    "exists": (
        lambda: file_question(ChangeKind.EXISTS, TARGET, overwrite=True, save_as=True, reload=False, modified=True),
        "Overwrite",
        "The file already exists\n/work/notes.txt",
        ["Cancel", "Save As…", "Overwrite"],
    ),
    "created": (
        lambda: file_question(ChangeKind.CREATED, TARGET, overwrite=True, save_as=True, reload=False, modified=True),
        "Overwrite",
        "The file was created since the editor started\n/work/notes.txt",
        ["Cancel", "Save As…", "Overwrite"],
    ),
    "changed": (
        lambda: file_question(ChangeKind.MODIFIED, TARGET, overwrite=True, save_as=True, reload=True, modified=True),
        "File changed",
        "The file changed on disk\n/work/notes.txt\nReloading discards your edits.",
        ["Keep", "Reload", "Save As…", "Overwrite"],
    ),
    "changed-unmodified": (
        lambda: file_question(ChangeKind.TRUNCATED, TARGET, overwrite=False, save_as=True, reload=True, modified=False),
        "File changed",
        "The file was truncated on disk\n/work/notes.txt",
        ["Keep", "Reload", "Save As…"],
    ),
    "deleted-no-file": (
        lambda: file_question(ChangeKind.DELETED, None, overwrite=True, save_as=True, reload=False, modified=True),
        "File changed",
        "The file was deleted on disk",
        ["Keep", "Save As…", "Overwrite"],
    ),
}


@pytest.mark.asyncio
@pytest.mark.parametrize("name", list(CASES))
async def test_each_question_has_its_title_text_and_buttons(name: str) -> None:
    factory, title, message, buttons = CASES[name]
    app = BoxHost(factory())
    async with app.run_test(size=SIZE) as pilot:
        await pilot.pause()
        box = open_box(app)
        assert box is not None
        assert box_title(box) == title
        assert box_message(box) == message
        assert box_buttons(box) == buttons
        screen_region = app.screen.region
        for button in box.query(Button):
            assert screen_region.contains_region(button.region), button.label
            assert button.size.height == 1, f"the label {button.label} wraps"  # a 16 character label is the longest that fits


@pytest.mark.asyncio
@pytest.mark.parametrize("name", list(CASES))
async def test_enter_and_escape_answer_the_harmless_first_button(name: str) -> None:
    factory, _, _, _ = CASES[name]
    for key in ("enter", "escape"):
        app = BoxHost(factory())
        async with app.run_test(size=SIZE) as pilot:
            await pilot.pause()
            await pilot.press(key)
            await pilot.pause()
        assert app.answers == [Response.CANCEL], (name, key)


@pytest.mark.asyncio
@pytest.mark.parametrize(
    ("name", "label", "expected"),
    [
        ("quit", "Discard & Quit", Response.DISCARD),
        ("close", "Discard & Close", Response.DISCARD),
        ("quit-running", "Quit Anyway", Response.DISCARD),
        ("open", "Discard & Open", Response.DISCARD),
        ("reload", "Discard & Reload", Response.DISCARD),
        ("exists", "Save As…", Response.SAVE),
        ("exists", "Overwrite", Response.OVERWRITE),
        ("changed", "Reload", Response.DISCARD),
        ("changed", "Save As…", Response.SAVE),
        ("changed", "Overwrite", Response.OVERWRITE),
    ],
)
async def test_the_other_buttons_answer_their_own_response(name: str, label: str, expected: Response) -> None:
    factory, _, _, _ = CASES[name]
    app = BoxHost(factory())
    async with app.run_test(size=SIZE) as pilot:
        await pilot.pause()
        await answer(pilot, app, label)
        await pilot.pause()
    assert app.answers == [expected]


@pytest.mark.asyncio
async def test_a_failure_box_has_one_ok_button_and_escape_answers_none() -> None:
    app = BoxHost(failure_box("Save failed", "Save failed (replace): no way"))
    async with app.run_test(size=SIZE) as pilot:
        await pilot.pause()
        box = open_box(app)
        assert box is not None
        assert box_title(box) == "Save failed"
        assert box_message(box) == "Save failed (replace): no way"
        assert box_buttons(box) == ["OK"]
        await pilot.press("escape")
        await pilot.pause()
    assert app.answers == [None]
    again = BoxHost(failure_box("Reload failed", "Reload failed: gone"))
    async with again.run_test(size=SIZE) as pilot:
        await pilot.pause()
        await pilot.press("enter")
        await pilot.pause()
    assert again.answers == [Response.OK]
