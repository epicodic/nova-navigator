"""What the gutter draws for rows whose number is not known yet and for the continuation rows of a wrapped row (REQ-11)."""

from __future__ import annotations

from pathlib import Path

import pytest

from nova_editor import LazyConfig
from nova_editor.widget import NovaTextArea
from tests.nova_editor.helpers_view import (
    LOWERED_OPTIONS,
    HostApp,
    await_first_layout,
    gated_source,
    lazy_wrapped,
    open_with_gated_line_scan,
    release_line_scan,
    wait_until,
)

SIZE = (40, 12)


def screen_lines(area: NovaTextArea) -> list[tuple[int, str]]:
    """`(absolute row of the screen line, its stripped text)` for every screen line of a widget without wrapped rows."""
    top = area.scroll_offset.y
    return [(top + y, area.render_line(y).text.strip()) for y in range(area.size.height)]


def scrolled_to(area: NovaTextArea, y: int) -> bool:
    """Scroll to `y` and say whether the widget stayed there (the first layouts and the size estimate may move the offset)."""
    area.scroll_to(y=y, animate=False)
    return area.scroll_offset.y == y


def expect_numbered_rows(area: NovaTextArea) -> None:
    """Every known row shows its own number, the open row of the scan and everything past the scan show nothing (no placeholder)."""
    known_rows = area.line_count if area.line_count_exact else area.line_count - 1
    seen_known = 0
    for row, text in screen_lines(area):
        if row < known_rows:
            assert text == f"{row + 1}  row {row}", (row, text)
            seen_known += 1
        else:
            assert text == "", (row, text)
    assert seen_known > 0


@pytest.mark.asyncio
async def test_rows_that_the_scan_has_not_resolved_have_a_blank_gutter_and_the_known_ones_the_right_number(tmp_path: Path) -> None:
    area, _ = open_with_gated_line_scan(tmp_path, rows=1000, threshold=500, show_line_numbers=True)
    async with HostApp(area).run_test(size=SIZE) as pilot:
        await pilot.pause()
        await wait_until(pilot, gated_source(area).blocked.is_set)  # the scan is held: the row count is a lower bound that does not move
        await await_first_layout(pilot, area)
        assert not area.line_count_exact
        open_row = area.line_count - 1
        await wait_until(pilot, lambda: scrolled_to(area, open_row - 9))  # the last known rows and the open row are on the screen
        assert screen_lines(area)[9] == (open_row, "")  # the open row: no number and no placeholder
        expect_numbered_rows(area)
        release_line_scan(area)
        await wait_until(pilot, lambda: area.line_count_exact)
        await wait_until(pilot, lambda: screen_lines(area)[9] == (open_row, f"{open_row + 1}  row {open_row}"))  # it gets its number once it is known
        expect_numbered_rows(area)


@pytest.mark.asyncio
@pytest.mark.parametrize(("chars", "options"), [(100, {}), (300, LOWERED_OPTIONS)], ids=["short row", "medium row"])
async def test_the_continuation_rows_of_a_wrapped_row_have_a_blank_gutter(tmp_path: Path, chars: int, options: dict[str, int]) -> None:
    # With the default thresholds a row of 100 characters is short (drawn by `_render_line`); with `LOWERED_OPTIONS` (word_wrap_limit 128) a row of 300 is medium (drawn by `_render_window`).
    path = tmp_path / "wrapped.txt"
    path.write_text("a" * chars + "\nsecond\n")
    area = NovaTextArea.open(path, soft_wrap=True, show_line_numbers=True, config=LazyConfig(**options))
    async with HostApp(area).run_test(size=SIZE) as pilot:
        await pilot.pause()
        await await_first_layout(pilot, area)
        second_row_y = lazy_wrapped(area).y_of_row(1)
        assert second_row_y >= 3  # the first row needs several screen lines
        gutter = area.gutter_width
        assert area.render_line(0).crop(0, gutter).text.strip() == "1"
        for y in range(1, second_row_y):
            assert area.render_line(y).crop(0, gutter).text.strip() == ""
            assert area.render_line(y).crop(gutter).text.strip() != ""  # the continuation shows text
        assert area.render_line(second_row_y).crop(0, gutter).text.strip() == "2"
