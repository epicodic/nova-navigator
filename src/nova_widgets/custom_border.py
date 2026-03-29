from __future__ import annotations

from typing import cast

from rich.segment import Segment
from rich.style import Style as RichStyle
from textual._border import BORDER_CHARS, INVISIBLE_EDGE_TYPES, EdgeType
from textual.geometry import Region
from textual.strip import Strip
from textual.style import Style as TextualStyle


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
        """Return content to display at the top-left of the border (after the corner).

        The default implementation renders ``self.border_title`` when set.
        """
        title = self._border_title  # type: ignore
        if not title:
            return Strip.blank(0)
        _, fg_color = self.styles.border_top  # type: ignore
        _, bg_color = self.background_colors  # type: ignore
        base_style = TextualStyle(background=bg_color, foreground=fg_color)
        segments = title.render_segments(base_style)
        return Strip(segments)

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
        """Return the Rich style for border slot content (border fg + widget bg)."""
        _, fg_color = self.styles.border_top  # type: ignore
        _, bg_color = self.background_colors  # type: ignore
        return RichStyle(color=fg_color.rich_color, bgcolor=bg_color.rich_color)

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
        _, bg_color = self.background_colors  # type: ignore
        style = RichStyle(color=color.rich_color, bgcolor=bg_color.rich_color)  # type: ignore

        # Keep 1 fill character as padding on each side (inside the corner chars).
        available = w - 4  # 2 corners + 2 padding fill chars
        if available <= 0:
            return Strip([Segment(corner_l + fill_char * max(0, w - 2) + corner_r, style)])

        left_w = min(left_slot.cell_length, available)
        right_w = min(right_slot.cell_length, available - left_w)
        fill_w = available - left_w - right_w

        if left_w < left_slot.cell_length:
            left_slot = left_slot.crop(0, left_w)
        if right_w < right_slot.cell_length:
            right_slot = right_slot.crop(right_slot.cell_length - right_w, right_slot.cell_length)

        result = Strip([Segment(corner_l + fill_char, style)])  # corner + 1 pad
        if left_w > 0:
            result = result + left_slot
        if fill_w > 0:
            result = result + Strip([Segment(fill_char * fill_w, style)])
        if right_w > 0:
            result = result + right_slot
        result = result + Strip([Segment(fill_char + corner_r, style)])  # 1 pad + corner
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
        # Skip custom border when no visible border is active.
        edge_type, _ = self.styles.border_top  # type: ignore
        if edge_type in INVISIBLE_EDGE_TYPES:
            return super().render_lines(crop)  # type: ignore

        w: int = self.outer_size.width  # type: ignore
        h: int = self.outer_size.height  # type: ignore
        if w < 2 or h < 2:  # noqa: PLR2004
            return super().render_lines(crop)  # type: ignore

        crop_end = crop.y + crop.height  # exclusive

        # Call super() only for inner rows (1 .. h-2), skipping the border rows.
        inner_y_start = max(crop.y, 1)
        inner_y_end = min(crop_end, h - 1)
        if inner_y_start < inner_y_end:
            inner_crop = Region(crop.x, inner_y_start, crop.width, inner_y_end - inner_y_start)
            inner_strips = super().render_lines(inner_crop)  # type: ignore
        else:
            inner_strips = []

        # Pre-build border rows only if they fall within the crop.
        top_edge_type, top_color = self.styles.border_top  # type: ignore
        bottom_edge_type, bottom_color = self.styles.border_bottom  # type: ignore

        top_strip = (
            self._build_border_row(
                0,
                top_edge_type,
                top_color,
                self.render_border_top_left(),
                self.render_border_top_right(),
                w,
            )
            if crop.y == 0
            else None
        )
        bottom_strip = (
            self._build_border_row(
                2,
                bottom_edge_type,
                bottom_color,
                self.render_border_bottom_left(),
                self.render_border_bottom_right(),
                w,
            )
            if crop_end >= h
            else None
        )

        result: list[Strip] = []
        for i in range(crop.height):
            outer_y = crop.y + i
            if outer_y == 0:
                assert top_strip is not None
                result.append(top_strip)
            elif outer_y == h - 1:
                assert bottom_strip is not None
                result.append(bottom_strip)
            else:
                result.append(inner_strips[outer_y - inner_y_start])
        return result
