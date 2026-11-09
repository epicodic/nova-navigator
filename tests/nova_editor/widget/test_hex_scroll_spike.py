"""Spike (R1): Textual's ScrollView and scrollbar maths with billions of virtual rows."""

from __future__ import annotations

import pytest
from rich.segment import Segment
from textual.app import App, ComposeResult
from textual.geometry import Size
from textual.scroll_view import ScrollView
from textual.strip import Strip

LENGTH = 8 * 1024**3
BPR = 16
ROWS = LENGTH // BPR + 1  # 536_870_913


class _Probe(ScrollView):
    DEFAULT_CSS = "_Probe { width: 1fr; height: 1fr; overflow-x: hidden; overflow-y: scroll; }"

    def on_mount(self) -> None:
        self.virtual_size = Size(78, ROWS)

    def render_line(self, y: int) -> Strip:
        row = self.scroll_offset.y + y
        return Strip.blank(78) if row >= ROWS else Strip([Segment(f"{row * BPR:08X}")])


class _Host(App[None]):
    def compose(self) -> ComposeResult:
        yield _Probe()


@pytest.mark.asyncio
async def test_virtual_height_of_billions_of_rows_is_kept() -> None:
    async with _Host().run_test(size=(80, 24)) as pilot:
        await pilot.pause()
        probe = pilot.app.query_one(_Probe)
        assert probe.virtual_size.height == ROWS
        # `size` includes the scrollbar; the drawable width is the scrollable content region (R2)
        assert probe.size.width == 80
        assert probe.scrollable_content_region.width == 78  # 80 columns minus the 2-cell scrollbar
        assert probe.max_scroll_y == ROWS - probe.scrollable_content_region.height


@pytest.mark.asyncio
async def test_scroll_to_the_last_row_and_render_it() -> None:
    async with _Host().run_test(size=(80, 24)) as pilot:
        await pilot.pause()
        probe = pilot.app.query_one(_Probe)
        height = probe.scrollable_content_region.height
        probe.scroll_to(y=ROWS - 1, animate=False, immediate=True)  # clamped to the last full page
        await pilot.pause()
        assert probe.scroll_offset.y == probe.max_scroll_y == ROWS - height
        assert probe.render_line(height - 1).text == f"{(ROWS - 1) * BPR:08X}"


@pytest.mark.asyncio
async def test_scrollbar_thumb_geometry_stays_finite_and_ordered() -> None:
    async with _Host().run_test(size=(80, 24)) as pilot:
        await pilot.pause()
        probe = pilot.app.query_one(_Probe)
        bar = probe.vertical_scrollbar
        assert bar.window_virtual_size == ROWS
        top = probe.scroll_y
        probe.scroll_to(y=ROWS // 2, animate=False, immediate=True)
        await pilot.pause()
        assert probe.scroll_y == ROWS // 2  # exact: far below 2**53
        assert top < probe.scroll_y < probe.max_scroll_y
        await pilot.click(bar, offset=(0, 12))  # a click in the bar must not raise or overflow
        await pilot.pause()
        assert 0 <= probe.scroll_y <= probe.max_scroll_y
