"""The Go to and Find popups on their own: their messages, texts and controls (REQ-16, REQ-17)."""

from __future__ import annotations

import pytest
from textual.app import App, ComposeResult
from textual.message import Message
from textual.widgets import Button, Input, Static

from nova_editor.find_popup import FindPopup
from nova_editor.goto_popup import HINT, INVALID, GotoPopup, GotoTarget, parse_goto
from nova_widgets.flat_widgets import Checkbox


class PopupHost(App[None]):
    """Hosts both popups and records their messages."""

    def __init__(self) -> None:
        super().__init__()
        self.goto = GotoPopup()
        self.find = FindPopup()
        self.messages: list[Message] = []

    def compose(self) -> ComposeResult:
        yield self.goto
        yield self.find

    def on_goto_popup_requested(self, message: GotoPopup.Requested) -> None:
        self.messages.append(message)

    def on_goto_popup_dismissed(self, message: GotoPopup.Dismissed) -> None:
        self.messages.append(message)

    def on_find_popup_requested(self, message: FindPopup.Requested) -> None:
        self.messages.append(message)

    def on_find_popup_dismissed(self, message: FindPopup.Dismissed) -> None:
        self.messages.append(message)


def test_parse_goto() -> None:
    assert parse_goto("120") == GotoTarget("line", 120)
    assert parse_goto(" @4096 ") == GotoTarget("byte", 4096)
    assert parse_goto("0") == GotoTarget("line", 0)
    assert parse_goto("abc") is None
    assert parse_goto("") is None
    assert parse_goto("@-1") is None
    assert parse_goto("-3") is None


@pytest.mark.asyncio
async def test_a_goto_popup_starts_hidden_and_open_shows_and_focuses_it() -> None:
    app = PopupHost()
    async with app.run_test(size=(80, 24)) as pilot:
        await pilot.pause()
        assert not app.goto.display
        app.goto.open((32, 1))
        await pilot.pause()
        assert app.goto.display
        assert app.focused is app.goto.input
        assert app.goto.shown == HINT
        assert (app.goto.region.x, app.goto.region.y) == (32, 1)


@pytest.mark.asyncio
async def test_goto_text_that_is_no_target_is_reported_and_posts_nothing() -> None:
    app = PopupHost()
    async with app.run_test(size=(80, 24)) as pilot:
        await pilot.pause()
        app.goto.open((32, 1))
        for text in ("abc", "@-1", ""):
            app.goto.input.value = text
            await pilot.press("enter")
            await pilot.pause()
            assert app.goto.shown == INVALID, text
            assert not app.goto.awaiting
            assert app.goto.input.has_class("-invalid")
        assert app.messages == []


@pytest.mark.asyncio
async def test_goto_enter_on_a_target_posts_it_and_waits_for_the_answer() -> None:
    app = PopupHost()
    async with app.run_test(size=(80, 24)) as pilot:
        await pilot.pause()
        app.goto.open((32, 1))
        await pilot.press("1", "2", "0", "enter")
        await pilot.pause()
        assert app.messages == [GotoPopup.Requested(GotoTarget("line", 120))]
        assert app.goto.awaiting
        app.goto.reject("line 120 is beyond the last line (42)")
        assert app.goto.shown == "line 120 is beyond the last line (42)"
        assert not app.goto.awaiting
        assert app.focused is app.goto.input


@pytest.mark.asyncio
async def test_goto_escape_posts_dismissed() -> None:
    app = PopupHost()
    async with app.run_test(size=(80, 24)) as pilot:
        await pilot.pause()
        app.goto.open((32, 1))
        await pilot.press("escape")
        await pilot.pause()
        assert app.messages == [GotoPopup.Dismissed()]


@pytest.mark.asyncio
async def test_find_enter_next_and_previous_post_the_request_with_the_case_option() -> None:
    app = PopupHost()
    async with app.run_test(size=(80, 24)) as pilot:
        await pilot.pause()
        app.find.open((24, 1), "needle")
        await pilot.pause()
        assert app.find.input.value == "needle"
        assert app.focused is app.find.input
        await pilot.press("enter")
        await pilot.pause()
        assert app.messages == [FindPopup.Requested("needle", False, True)]
        await pilot.press("alt+c", "enter")
        await pilot.pause()
        assert not app.find.case_sensitive
        assert app.messages[-1] == FindPopup.Requested("needle", False, False)
        await pilot.click(app.find.query_one("#find_previous", Button))
        await pilot.pause()
        assert app.messages[-1] == FindPopup.Requested("needle", True, False)
        await pilot.click(app.find.query_one("#find_next", Button))
        await pilot.pause()
        assert app.messages[-1] == FindPopup.Requested("needle", False, False)


@pytest.mark.asyncio
async def test_find_with_an_empty_needle_posts_nothing_and_escape_posts_dismissed() -> None:
    app = PopupHost()
    async with app.run_test(size=(80, 24)) as pilot:
        await pilot.pause()
        app.find.open((24, 1), None)
        await pilot.press("enter")
        await pilot.pause()
        assert app.messages == []
        await pilot.press("escape")
        await pilot.pause()
        assert app.messages == [FindPopup.Dismissed()]


@pytest.mark.asyncio
async def test_find_status_shows_the_text() -> None:
    app = PopupHost()
    async with app.run_test(size=(80, 24)) as pilot:
        await pilot.pause()
        app.find.open((24, 1), None)
        app.find.set_status("Not found: zzz")
        await pilot.pause()
        assert app.find.status == "Not found: zzz"
        assert str(app.find.query_one("#find_status", Static).content) == "Not found: zzz"
        app.find.open((24, 1), None)  # a new opening starts with an empty status row
        assert app.find.status == ""


@pytest.mark.asyncio
async def test_the_find_popup_has_only_the_find_controls_and_fits_its_box() -> None:
    app = PopupHost()
    async with app.run_test(size=(80, 24)) as pilot:
        await pilot.pause()
        app.find.open((24, 1), None)
        await pilot.pause()
        assert len(app.find.query(Input)) == 1
        checkboxes = list(app.find.query(Checkbox))
        assert [str(box.label) for box in checkboxes] == ["Match case"]
        assert checkboxes[0].value is True
        assert [(button.id, str(button.label)) for button in app.find.query(Button)] == [("find_previous", "Previous"), ("find_next", "Next")]
        for control in app.find.query("Input, Checkbox, Button"):
            assert app.find.region.contains_region(control.region)
