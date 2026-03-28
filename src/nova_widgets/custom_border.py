from __future__ import annotations

from rich.style import Style as RichStyle
from textual._border import INVISIBLE_EDGE_TYPES
from textual.geometry import Region
from textual.strip import Strip


class CustomBorderMixin:
    """Mixin that adds content slots to the four corners of a widget's border.

    Mix in before ``Widget`` (or any ``Widget`` subclass) in the MRO::

        class MyWidget(CustomBorderMixin, Widget): ...
        class MyScrollable(CustomBorderMixin, ScrollView): ...

    The widget's CSS ``border`` style is used as-is for character selection and
    colour.  The native Textual border is drawn first; this mixin then
    post-processes the top and bottom border rows to splice in slot content.

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

        # Top border row lives at widget y=0; its index in the strips list is 0 - crop.y.
        top_idx = 0 - crop.y
        if 0 <= top_idx < len(strips):
            strips[top_idx] = _inject_border_content(
                strips[top_idx],
                self.render_border_top_left(),
                self.render_border_top_right(),
                w,
            )

        # Bottom border row lives at widget y=h-1.
        bottom_idx = (h - 1) - crop.y
        if 0 <= bottom_idx < len(strips):
            strips[bottom_idx] = _inject_border_content(
                strips[bottom_idx],
                self.render_border_bottom_left(),
                self.render_border_bottom_right(),
                w,
            )

        return strips


def _inject_border_content(
    strip: Strip,
    left_slot: Strip,
    right_slot: Strip,
    w: int,
) -> Strip:
    """Splice *left_slot* and *right_slot* into the interior of a border row *strip*.

    *strip* is a fully-rendered border row of total width *w*, with corner
    characters at positions 0 and w-1.  Slots are placed immediately after and
    before the corners respectively.  If the combined slot width exceeds the
    available space (w-2), the right slot is clipped first, then the left.
    The native fill characters between the two slots are preserved.
    """
    available = w - 2  # cells between the two corner characters
    if available <= 0:
        return strip

    left_w = min(left_slot.cell_length, available)
    right_w = min(right_slot.cell_length, available - left_w)

    if left_w < left_slot.cell_length:
        left_slot = left_slot.crop(0, left_w)
    if right_w < right_slot.cell_length:
        # Keep the rightmost right_w characters of the right slot.
        right_slot = right_slot.crop(right_slot.cell_length - right_w, right_slot.cell_length)

    left_end = 1 + left_w  # first fill char
    right_start = w - 1 - right_w  # first right-slot char

    result = strip.crop(0, 1)  # left corner char
    if left_w > 0:
        result = result + left_slot  # left slot content
    result = result + strip.crop(left_end, right_start)  # preserved fill chars
    if right_w > 0:
        result = result + right_slot  # right slot content
    result = result + strip.crop(w - 1, w)  # right corner char
    return result
