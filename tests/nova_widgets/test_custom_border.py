from __future__ import annotations

import pytest
from rich.segment import Segment
from textual.app import App, ComposeResult
from textual.geometry import Region
from textual.strip import Strip
from textual.widget import Widget

from nova_widgets.custom_border import CustomBorderMixin


class _SlottedWidget(CustomBorderMixin, Widget):
    """Test widget with settable slot strings."""

    DEFAULT_CSS = """
    _SlottedWidget {
        width: 20;
        height: 5;
        border: solid white;
    }
    """

    def __init__(self) -> None:
        super().__init__()
        self.tl = ""
        self.tr = ""
        self.bl = ""
        self.br = ""

    def render_border_top_left(self) -> Strip:
        return Strip([Segment(self.tl)]) if self.tl else Strip.blank(0)

    def render_border_top_right(self) -> Strip:
        return Strip([Segment(self.tr)]) if self.tr else Strip.blank(0)

    def render_border_bottom_left(self) -> Strip:
        return Strip([Segment(self.bl)]) if self.bl else Strip.blank(0)

    def render_border_bottom_right(self) -> Strip:
        return Strip([Segment(self.br)]) if self.br else Strip.blank(0)


class _BorderTestApp(App[None]):
    def __init__(self, widget: Widget) -> None:
        super().__init__()
        self._widget = widget

    def compose(self) -> ComposeResult:
        yield self._widget


def _get_strips(widget: Widget) -> list[Strip]:
    w = widget.outer_size.width
    h = widget.outer_size.height
    return widget.render_lines(Region(0, 0, w, h))


@pytest.mark.asyncio
async def test_top_row_starts_and_ends_with_corner_chars() -> None:
    widget = _SlottedWidget()
    async with _BorderTestApp(widget).run_test() as pilot:
        await pilot.pause()
        top_text = _get_strips(widget)[0].text
        assert top_text[0] == "┌"
        assert top_text[-1] == "┐"


@pytest.mark.asyncio
async def test_bottom_row_starts_and_ends_with_corner_chars() -> None:
    widget = _SlottedWidget()
    async with _BorderTestApp(widget).run_test() as pilot:
        await pilot.pause()
        bottom_text = _get_strips(widget)[-1].text
        assert bottom_text[0] == "└"
        assert bottom_text[-1] == "┘"


@pytest.mark.asyncio
async def test_top_left_slot_appears_after_left_corner() -> None:
    widget = _SlottedWidget()
    widget.tl = "AB"
    async with _BorderTestApp(widget).run_test() as pilot:
        await pilot.pause()
        top_text = _get_strips(widget)[0].text
        assert top_text[2:4] == "AB"


@pytest.mark.asyncio
async def test_top_right_slot_appears_before_right_corner() -> None:
    widget = _SlottedWidget()
    widget.tr = "XY"
    async with _BorderTestApp(widget).run_test() as pilot:
        await pilot.pause()
        strips = _get_strips(widget)
        top_text = strips[0].text
        w = widget.outer_size.width
        assert top_text[w - 4 : w - 2] == "XY"


@pytest.mark.asyncio
async def test_bottom_left_slot_appears_after_left_corner() -> None:
    widget = _SlottedWidget()
    widget.bl = "CD"
    async with _BorderTestApp(widget).run_test() as pilot:
        await pilot.pause()
        bottom_text = _get_strips(widget)[-1].text
        assert bottom_text[2:4] == "CD"


@pytest.mark.asyncio
async def test_bottom_right_slot_appears_before_right_corner() -> None:
    widget = _SlottedWidget()
    widget.br = "PQ"
    async with _BorderTestApp(widget).run_test() as pilot:
        await pilot.pause()
        strips = _get_strips(widget)
        bottom_text = strips[-1].text
        w = widget.outer_size.width
        assert bottom_text[w - 4 : w - 2] == "PQ"


@pytest.mark.asyncio
async def test_slots_clipped_when_combined_width_exceeds_available() -> None:
    """Left slot fits fully; right slot is clipped to remaining space."""
    widget = _SlottedWidget()
    # width=20, available between slots=16 (w - 4); left=12 fits, right gets 4
    widget.tl = "L" * 12
    widget.tr = "R" * 12
    async with _BorderTestApp(widget).run_test() as pilot:
        await pilot.pause()
        top_text = _get_strips(widget)[0].text
        w = widget.outer_size.width
        assert len(top_text) == w
        assert top_text[2:14] == "L" * 12  # left: all 12 fit (starts after corner+pad)
        assert top_text[14 : w - 2] == "R" * 4  # right: only 4 remain (ends before pad+corner)


@pytest.mark.asyncio
async def test_no_slots_leaves_native_fill_chars_intact() -> None:
    widget = _SlottedWidget()
    async with _BorderTestApp(widget).run_test() as pilot:
        await pilot.pause()
        top_text = _get_strips(widget)[0].text
        # solid border fill is ─ (including the 1 padding char on each side)
        assert all(c == "─" for c in top_text[1:-1])


@pytest.mark.asyncio
async def test_inner_rows_have_vertical_bar_borders() -> None:
    widget = _SlottedWidget()
    async with _BorderTestApp(widget).run_test() as pilot:
        await pilot.pause()
        strips = _get_strips(widget)
        w = widget.outer_size.width
        for strip in strips[1:-1]:
            text = strip.text
            assert text[0] == "│", f"Expected │ at row start, got {text[0]!r}"
            assert text[w - 1] == "│", f"Expected │ at row end, got {text[w - 1]!r}"
