from __future__ import annotations

from typing import cast

from rich.segment import Segment
from rich.style import Style as RichStyle
from textual._border import BORDER_CHARS, INVISIBLE_EDGE_TYPES, EdgeType
from textual.geometry import Region
from textual.strip import Strip


class CustomBorderMixin:
    """Mixin that adds content slots to the four corners of a widget's border.

    Mix in before ``Widget`` (or any ``Widget`` subclass) in the MRO::

        class MyWidget(CustomBorderMixin, Widget): ...
        class MyScrollable(CustomBorderMixin, ScrollView): ...

    Both the top and bottom border rows are built entirely from scratch using
    ``BORDER_CHARS``.  Textual's own border pipeline is still called first (so
    that padding and content lines are rendered correctly), but the border rows
    are then replaced wholesale.

    Override any of the four slot methods to inject content.  Return
    ``Strip.blank(0)`` (the default) to leave a corner empty.
    """

    def render_border_top_left(self) -> Strip:
        """Return content to display at the top-left of the border (after the corner)."""
        return Strip.blank(0)

    def render_border_top_right(self) -> Strip:
        """Return content to display at the top-right of the border (before the corner)."""
        return Strip.blank(0)

    def render_border_bottom_left(self) -> Strip:
        """Return content to display at the bottom-left of the border (after the corner)."""
        return Strip.blank(0)

    def render_border_bottom_right(self) -> Strip:
        """Return content to display at the bottom-right of the border (before the corner)."""
        return Strip.blank(0)

    def _border_rich_style(self) -> RichStyle:
        """Return the Rich style corresponding to this widget's border colour."""
        _, color = self.styles.border_top  # type: ignore
        return RichStyle.from_color(color.rich_color)

    def _build_border_row(
        self,
        char_row_idx: int,
        edge_type: str,
        color: object,
        left_slot: Strip,
        right_slot: Strip,
        w: int,
    ) -> Strip:
        """Build a single border row strip from scratch.

        Args:
            char_row_idx: 0 for the top row, 2 for the bottom row in ``BORDER_CHARS``.
            edge_type: The CSS border edge type string (e.g. ``"round"``, ``"solid"``).
            color: The Textual ``Color`` from ``styles.border_top/bottom``.
            left_slot: Strip to place after the left corner character.
            right_slot: Strip to place before the right corner character.
            w: Total width of the border row in terminal cells.
        """
        chars = BORDER_CHARS.get(cast("EdgeType", edge_type))  # type: ignore[arg-type]
        if chars is None:
            return Strip.blank(w)
        corner_l, fill_char, corner_r = chars[char_row_idx]
        style = RichStyle.from_color(color.rich_color)  # type: ignore

        available = w - 2
        if available <= 0:
            return Strip([Segment(corner_l + corner_r, style)])

        left_w = min(left_slot.cell_length, available)
        right_w = min(right_slot.cell_length, available - left_w)
        fill_w = available - left_w - right_w

        if left_w < left_slot.cell_length:
            left_slot = left_slot.crop(0, left_w)
        if right_w < right_slot.cell_length:
            right_slot = right_slot.crop(right_slot.cell_length - right_w, right_slot.cell_length)

        result = Strip([Segment(corner_l, style)])
        if left_w > 0:
            result = result + left_slot
        if fill_w > 0:
            result = result + Strip([Segment(fill_char * fill_w, style)])
        if right_w > 0:
            result = result + right_slot
        result = result + Strip([Segment(corner_r, style)])
        return result

    def refresh_border(self) -> None:
        """Refresh only the border rows without repainting widget content.

        More efficient than ``self.refresh()`` when only border slot content has
        changed (e.g. after a cursor move that changes what a slot displays).

        ``self.refresh(region)`` is not suitable here because it translates the
        region by ``content_offset`` (to convert content-space coords to
        outer-widget space), which pushes border-row coordinates out of bounds.
        Instead we write directly to ``_dirty_regions`` in outer-widget space
        (row 0 = top border, row h-1 = bottom border).
        """
        w: int = self.outer_size.width  # type: ignore
        h: int = self.outer_size.height  # type: ignore
        if h < 2 or w < 2:  # noqa: PLR2004
            return
        top_region = Region(0, 0, w, 1)
        bottom_region = Region(0, h - 1, w, 1)
        self._dirty_regions.update({top_region, bottom_region})  # type: ignore
        self._repaint_regions.update({top_region, bottom_region})  # type: ignore
        self._styles_cache.set_dirty(top_region, bottom_region)  # type: ignore
        self._repaint_required = True
        self.check_idle()  # type: ignore

    def render_lines(self, crop: Region) -> list[Strip]:
        strips = super().render_lines(crop)  # type: ignore

        # Skip post-processing when no visible border is active.
        edge_type, _ = self.styles.border_top  # type: ignore
        if edge_type in INVISIBLE_EDGE_TYPES:
            return strips

        w: int = self.outer_size.width  # type: ignore
        h: int = self.outer_size.height  # type: ignore
        if w < 2 or h < 2:  # noqa: PLR2004
            return strips

        top_idx = 0 - crop.y
        if 0 <= top_idx < len(strips):
            top_edge_type, top_color = self.styles.border_top  # type: ignore
            strips[top_idx] = self._build_border_row(
                0,
                top_edge_type,
                top_color,
                self.render_border_top_left(),
                self.render_border_top_right(),
                w,
            )

        bottom_idx = (h - 1) - crop.y
        if 0 <= bottom_idx < len(strips):
            bottom_edge_type, bottom_color = self.styles.border_bottom  # type: ignore
            strips[bottom_idx] = self._build_border_row(
                2,
                bottom_edge_type,
                bottom_color,
                self.render_border_bottom_left(),
                self.render_border_bottom_right(),
                w,
            )

        return strips
