import pytest
from textual.app import App, ComposeResult
from textual.widgets import Button

from nova_widgets.button_box import ButtonBox


class _TestApp(App[None]):
    def __init__(self, widget: ButtonBox) -> None:
        super().__init__()
        self._widget = widget

    def compose(self) -> ComposeResult:
        yield self._widget


@pytest.mark.asyncio
async def test_compose_multi_row() -> None:
    """ButtonBox with two rows renders two Horizontal containers."""
    from textual.containers import Horizontal

    row1 = [Button("A"), Button("B")]
    row2 = [Button("C")]
    box = ButtonBox([row1, row2])
    app = _TestApp(box)
    async with app.run_test() as pilot:
        await pilot.pause()
        rows = list(app.query(Horizontal))
        assert len(rows) == 2


@pytest.mark.asyncio
async def test_compose_flat_list_is_single_row() -> None:
    """ButtonBox constructed with a flat list behaves like a single-row grid."""
    from textual.containers import Horizontal

    buttons = [Button("X"), Button("Y"), Button("Z")]
    box = ButtonBox(buttons)
    app = _TestApp(box)
    async with app.run_test() as pilot:
        await pilot.pause()
        rows = list(app.query(Horizontal))
        assert len(rows) == 1


@pytest.mark.asyncio
async def test_right_moves_focus_within_row() -> None:
    btns = [Button("A", id="a"), Button("B", id="b"), Button("C", id="c")]
    box = ButtonBox(btns)
    app = _TestApp(box)
    async with app.run_test() as pilot:
        await pilot.pause()
        app.query_one("#a", Button).focus()
        await pilot.pause()
        await pilot.press("right")
        await pilot.pause()
        assert app.focused is app.query_one("#b", Button)


@pytest.mark.asyncio
async def test_left_moves_focus_within_row() -> None:
    btns = [Button("A", id="a"), Button("B", id="b")]
    box = ButtonBox(btns)
    app = _TestApp(box)
    async with app.run_test() as pilot:
        await pilot.pause()
        app.query_one("#b", Button).focus()
        await pilot.pause()
        await pilot.press("left")
        await pilot.pause()
        assert app.focused is app.query_one("#a", Button)


@pytest.mark.asyncio
async def test_right_does_not_wrap_at_end() -> None:
    btns = [Button("A", id="a"), Button("B", id="b")]
    box = ButtonBox(btns)
    app = _TestApp(box)
    async with app.run_test() as pilot:
        await pilot.pause()
        app.query_one("#b", Button).focus()
        await pilot.pause()
        await pilot.press("right")
        await pilot.pause()
        assert app.focused is app.query_one("#b", Button)


@pytest.mark.asyncio
async def test_left_does_not_wrap_at_start() -> None:
    btns = [Button("A", id="a"), Button("B", id="b")]
    box = ButtonBox(btns)
    app = _TestApp(box)
    async with app.run_test() as pilot:
        await pilot.pause()
        app.query_one("#a", Button).focus()
        await pilot.pause()
        await pilot.press("left")
        await pilot.pause()
        assert app.focused is app.query_one("#a", Button)


@pytest.mark.asyncio
async def test_down_moves_to_same_column_in_next_row() -> None:
    row1 = [Button("A", id="a"), Button("B", id="b")]
    row2 = [Button("C", id="c"), Button("D", id="d")]
    box = ButtonBox([row1, row2])
    app = _TestApp(box)
    async with app.run_test() as pilot:
        await pilot.pause()
        app.query_one("#b", Button).focus()
        await pilot.pause()
        await pilot.press("down")
        await pilot.pause()
        assert app.focused is app.query_one("#d", Button)


@pytest.mark.asyncio
async def test_up_moves_to_same_column_in_prev_row() -> None:
    row1 = [Button("A", id="a"), Button("B", id="b")]
    row2 = [Button("C", id="c"), Button("D", id="d")]
    box = ButtonBox([row1, row2])
    app = _TestApp(box)
    async with app.run_test() as pilot:
        await pilot.pause()
        app.query_one("#c", Button).focus()
        await pilot.pause()
        await pilot.press("up")
        await pilot.pause()
        assert app.focused is app.query_one("#a", Button)


@pytest.mark.asyncio
async def test_down_clamps_column_when_target_row_is_shorter() -> None:
    row1 = [Button("A", id="a"), Button("B", id="b"), Button("C", id="c")]
    row2 = [Button("D", id="d")]
    box = ButtonBox([row1, row2])
    app = _TestApp(box)
    async with app.run_test() as pilot:
        await pilot.pause()
        app.query_one("#c", Button).focus()
        await pilot.pause()
        await pilot.press("down")
        await pilot.pause()
        assert app.focused is app.query_one("#d", Button)


@pytest.mark.asyncio
async def test_up_does_not_move_on_first_row() -> None:
    row1 = [Button("A", id="a")]
    row2 = [Button("B", id="b")]
    box = ButtonBox([row1, row2])
    app = _TestApp(box)
    async with app.run_test() as pilot:
        await pilot.pause()
        app.query_one("#a", Button).focus()
        await pilot.pause()
        await pilot.press("up")
        await pilot.pause()
        assert app.focused is app.query_one("#a", Button)


@pytest.mark.asyncio
async def test_down_does_not_move_on_last_row() -> None:
    row1 = [Button("A", id="a")]
    row2 = [Button("B", id="b")]
    box = ButtonBox([row1, row2])
    app = _TestApp(box)
    async with app.run_test() as pilot:
        await pilot.pause()
        app.query_one("#b", Button).focus()
        await pilot.pause()
        await pilot.press("down")
        await pilot.pause()
        assert app.focused is app.query_one("#b", Button)


@pytest.mark.asyncio
async def test_arrow_ignored_when_no_button_focused() -> None:
    """Arrow keys do nothing when no button in the box is focused."""
    row1 = [Button("A", id="a"), Button("B", id="b")]
    box = ButtonBox(row1)
    app = _TestApp(box)
    async with app.run_test() as pilot:
        await pilot.pause()
        # Textual auto-focuses the first button; explicitly clear all focus so
        # the ButtonBox bindings cannot fire.
        app.screen.set_focus(None)
        await pilot.pause()
        await pilot.press("right")
        await pilot.press("down")
        await pilot.pause()
        # Neither button should be focused as a result of the key presses
        focused = app.focused
        assert focused is not app.query_one("#a", Button)
        assert focused is not app.query_one("#b", Button)


@pytest.mark.asyncio
async def test_button_box_arrow_navigation_in_two_rows() -> None:
    """Integration: two rows, up/down navigates between them at the same column."""
    row1 = [Button("Yes", id="yes"), Button("No", id="no")]
    row2 = [Button("Yes to all", id="yes_all"), Button("No to all", id="no_all")]
    box = ButtonBox([row1, row2])
    app = _TestApp(box)
    async with app.run_test() as pilot:
        await pilot.pause()
        app.query_one("#yes", Button).focus()
        await pilot.pause()
        await pilot.press("down")
        await pilot.pause()
        assert app.focused is app.query_one("#yes_all", Button)
        await pilot.press("right")
        await pilot.pause()
        assert app.focused is app.query_one("#no_all", Button)
        await pilot.press("up")
        await pilot.pause()
        assert app.focused is app.query_one("#no", Button)
