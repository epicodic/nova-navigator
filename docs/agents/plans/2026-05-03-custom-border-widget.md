# Custom Border Widget Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use skills:subagent-driven-development (recommended) or skills:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Add a `CustomBorderMixin` that lets widgets inject `Strip` content into any of the four corners of their Textual border, then use it to display selected-file info and a symlink placeholder in the `DirectoryBrowser` bottom border and to clean up the `PopupWidget` close-button logic. VFS symlink resolution is deferred.

**Architecture:** The mixin overrides `render_lines()`, calls `super()` to get natively-rendered border strips (Textual handles all geometry), then post-processes the top and bottom border rows to splice in caller-supplied `Strip` values. Four hook methods — `render_border_{top,bottom}_{left,right}()` — are the extension points. No CSS changes are needed on consumers; the mixin reads the active `border` style from `self.styles.border_top`.

**Tech Stack:** Python 3.12, pytest

**Coding Conventions:** `docs/coding_conventions.md` — read before implementing

**Spec:** `docs/agents/specs/2026-05-03-custom-border-widget-design.md`

> **Architectural note vs spec §1.3 and §1.7:** The spec proposed setting `border: none; padding: 1` and overriding `get_content_width/height`. This plan uses a simpler approach: keep the native Textual border active, post-process the rendered strips. No CSS changes, no geometry overrides.

---

## File Map

| Action | Path | Responsibility |
|--------|------|----------------|
| Create | `src/nova_widgets/custom_border.py` | `CustomBorderMixin` class + `_inject_border_content()` helper |
| Modify | `src/nova_widgets/__init__.py` | Export `CustomBorderMixin` |
| Modify | `src/nova_navigator/widgets/popup_widget.py` | Adopt mixin; replace `render_lines()` override with `render_border_top_right()` |
| Modify | `src/nova_navigator/widgets/directory_browser.py` | Adopt mixin; implement bottom-left (selected files) and bottom-right (symlink placeholder) slots |
| Create | `tests/nova_widgets/test_custom_border.py` | Unit tests for the mixin |

---

## Task 1: `CustomBorderMixin`

**Files:**
- Create: `src/nova_widgets/custom_border.py`
- Test: `tests/nova_widgets/test_custom_border.py`

- [ ] **Step 1.1: Write the failing tests**

Create `tests/nova_widgets/test_custom_border.py`:

```python
from __future__ import annotations

import pytest
from textual.app import App, ComposeResult
from textual.geometry import Region
from textual.scroll_view import ScrollView
from textual.strip import Strip
from textual.widget import Widget

from nova_widgets.custom_border import CustomBorderMixin


# --- Minimal test widgets ---


class _SimpleWidget(CustomBorderMixin, Widget):
    DEFAULT_CSS = """
    _SimpleWidget {
        width: 20;
        height: 5;
        border: solid white;
    }
    """

    _tl: str = ""
    _tr: str = ""
    _bl: str = ""
    _br: str = ""

    def render_border_top_left(self) -> Strip:
        return Strip.blank(0) if not self._tl else Strip.from_strip(Strip.blank(len(self._tl))).join(
            [Strip([__import__("rich.segment", fromlist=["Segment"]).Segment(self._tl)])]
        )

    def render_border_top_right(self) -> Strip:
        from rich.segment import Segment
        return Strip([Segment(self._tr)]) if self._tr else Strip.blank(0)

    def render_border_bottom_left(self) -> Strip:
        from rich.segment import Segment
        return Strip([Segment(self._bl)]) if self._bl else Strip.blank(0)

    def render_border_bottom_right(self) -> Strip:
        from rich.segment import Segment
        return Strip([Segment(self._br)]) if self._br else Strip.blank(0)


class _BorderTestApp(App[None]):
    def __init__(self, widget: Widget) -> None:
        super().__init__()
        self._widget = widget

    def compose(self) -> ComposeResult:
        yield self._widget


# --- Helpers ---


def _get_strips(widget: Widget) -> list[Strip]:
    w = widget.size.width
    h = widget.size.height
    return widget.render_lines(Region(0, 0, w, h))


# --- Tests ---


@pytest.mark.asyncio
async def test_top_row_starts_and_ends_with_corner_chars() -> None:
    widget: _SimpleWidget = _SimpleWidget()
    app = _BorderTestApp(widget)
    async with app.run_test() as pilot:
        await pilot.pause()
        strips = _get_strips(widget)
        top_text = strips[0].text
        # solid border: corners are ┌ and ┐
        assert top_text[0] == "┌"
        assert top_text[-1] == "┐"


@pytest.mark.asyncio
async def test_bottom_row_starts_and_ends_with_corner_chars() -> None:
    widget: _SimpleWidget = _SimpleWidget()
    app = _BorderTestApp(widget)
    async with app.run_test() as pilot:
        await pilot.pause()
        strips = _get_strips(widget)
        bottom_text = strips[-1].text
        assert bottom_text[0] == "└"
        assert bottom_text[-1] == "┘"


@pytest.mark.asyncio
async def test_top_left_slot_appears_after_left_corner() -> None:
    widget: _SimpleWidget = _SimpleWidget()
    widget._tl = "AB"
    app = _BorderTestApp(widget)
    async with app.run_test() as pilot:
        await pilot.pause()
        strips = _get_strips(widget)
        top_text = strips[0].text
        assert top_text[1:3] == "AB"


@pytest.mark.asyncio
async def test_top_right_slot_appears_before_right_corner() -> None:
    widget: _SimpleWidget = _SimpleWidget()
    widget._tr = "XY"
    app = _BorderTestApp(widget)
    async with app.run_test() as pilot:
        await pilot.pause()
        strips = _get_strips(widget)
        top_text = strips[0].text
        w = widget.size.width
        assert top_text[w - 3 : w - 1] == "XY"


@pytest.mark.asyncio
async def test_bottom_left_slot_appears_after_left_corner() -> None:
    widget: _SimpleWidget = _SimpleWidget()
    widget._bl = "CD"
    app = _BorderTestApp(widget)
    async with app.run_test() as pilot:
        await pilot.pause()
        strips = _get_strips(widget)
        bottom_text = strips[-1].text
        assert bottom_text[1:3] == "CD"


@pytest.mark.asyncio
async def test_bottom_right_slot_appears_before_right_corner() -> None:
    widget: _SimpleWidget = _SimpleWidget()
    widget._br = "PQ"
    app = _BorderTestApp(widget)
    async with app.run_test() as pilot:
        await pilot.pause()
        strips = _get_strips(widget)
        bottom_text = strips[-1].text
        w = widget.size.width
        assert bottom_text[w - 3 : w - 1] == "PQ"


@pytest.mark.asyncio
async def test_slots_clipped_when_combined_width_exceeds_available() -> None:
    """Left slot takes priority; right is clipped first."""
    widget: _SimpleWidget = _SimpleWidget()
    # Widget is 20 wide; available between corners = 18
    # Left slot = 12 chars, right slot = 12 chars (combined 24 > 18)
    widget._tl = "L" * 12
    widget._tr = "R" * 12
    app = _BorderTestApp(widget)
    async with app.run_test() as pilot:
        await pilot.pause()
        strips = _get_strips(widget)
        top_text = strips[0].text
        w = widget.size.width
        assert len(top_text) == w
        # Left slot: all 12 chars fit (12 <= 18)
        assert top_text[1:13] == "L" * 12
        # Right slot: only 18 - 12 = 6 chars fit (rightmost 6)
        assert top_text[13 : w - 1] == "R" * 6


@pytest.mark.asyncio
async def test_no_slots_leaves_native_fill_chars_intact() -> None:
    widget: _SimpleWidget = _SimpleWidget()
    app = _BorderTestApp(widget)
    async with app.run_test() as pilot:
        await pilot.pause()
        strips = _get_strips(widget)
        top_text = strips[0].text
        # solid border fill char is ─
        assert all(c == "─" for c in top_text[1:-1])


@pytest.mark.asyncio
async def test_inner_rows_have_vertical_bar_borders() -> None:
    widget: _SimpleWidget = _SimpleWidget()
    app = _BorderTestApp(widget)
    async with app.run_test() as pilot:
        await pilot.pause()
        strips = _get_strips(widget)
        w = widget.size.width
        # rows 1..h-2 are inner content rows
        for strip in strips[1:-1]:
            text = strip.text
            assert text[0] == "│", f"Expected │ at start, got {text[0]!r}"
            assert text[w - 1] == "│", f"Expected │ at end, got {text[w-1]!r}"
```

- [ ] **Step 1.2: Run tests to verify they fail**

```
uv run pytest tests/nova_widgets/test_custom_border.py -v
```

Expected: FAIL (ImportError — `custom_border` does not exist yet)

- [ ] **Step 1.3: Create `src/nova_widgets/custom_border.py`**

```python
from __future__ import annotations

from rich.color import Color as RichColor
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
        _, color = self.styles.border_top  # type: ignore[attr-defined]
        return RichStyle.from_color(color.rich_color)

    def render_lines(self, crop: Region) -> list[Strip]:
        strips = super().render_lines(crop)  # type: ignore[misc]

        # Skip post-processing when no visible border is active.
        edge_type, _ = self.styles.border_top  # type: ignore[attr-defined]
        if edge_type in INVISIBLE_EDGE_TYPES:
            return strips

        w: int = self.size.width  # type: ignore[attr-defined]
        h: int = self.size.height  # type: ignore[attr-defined]
        if w < 2 or h < 2:
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

    left_end = 1 + left_w          # first fill char
    right_start = w - 1 - right_w  # first right-slot char

    result = strip.crop(0, 1)                        # left corner char
    if left_w > 0:
        result = result + left_slot                  # left slot content
    result = result + strip.crop(left_end, right_start)  # preserved fill chars
    if right_w > 0:
        result = result + right_slot                 # right slot content
    result = result + strip.crop(w - 1, w)           # right corner char
    return result
```

- [ ] **Step 1.4: Fix the test helper — `_SimpleWidget.render_border_top_left` uses a convoluted expression**

Replace the `_SimpleWidget` helper in the test file with a cleaner version:

```python
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
    w = widget.size.width
    h = widget.size.height
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
        assert top_text[1:3] == "AB"


@pytest.mark.asyncio
async def test_top_right_slot_appears_before_right_corner() -> None:
    widget = _SlottedWidget()
    widget.tr = "XY"
    async with _BorderTestApp(widget).run_test() as pilot:
        await pilot.pause()
        strips = _get_strips(widget)
        top_text = strips[0].text
        w = widget.size.width
        assert top_text[w - 3 : w - 1] == "XY"


@pytest.mark.asyncio
async def test_bottom_left_slot_appears_after_left_corner() -> None:
    widget = _SlottedWidget()
    widget.bl = "CD"
    async with _BorderTestApp(widget).run_test() as pilot:
        await pilot.pause()
        bottom_text = _get_strips(widget)[-1].text
        assert bottom_text[1:3] == "CD"


@pytest.mark.asyncio
async def test_bottom_right_slot_appears_before_right_corner() -> None:
    widget = _SlottedWidget()
    widget.br = "PQ"
    async with _BorderTestApp(widget).run_test() as pilot:
        await pilot.pause()
        strips = _get_strips(widget)
        bottom_text = strips[-1].text
        w = widget.size.width
        assert bottom_text[w - 3 : w - 1] == "PQ"


@pytest.mark.asyncio
async def test_slots_clipped_when_combined_width_exceeds_available() -> None:
    """Left slot fits fully; right slot is clipped to remaining space."""
    widget = _SlottedWidget()
    # width=20, available between corners=18; left=12, right=12, combined=24>18
    widget.tl = "L" * 12
    widget.tr = "R" * 12
    async with _BorderTestApp(widget).run_test() as pilot:
        await pilot.pause()
        top_text = _get_strips(widget)[0].text
        w = widget.size.width
        assert len(top_text) == w
        assert top_text[1:13] == "L" * 12       # left: all 12 fit
        assert top_text[13 : w - 1] == "R" * 6  # right: only 6 remain


@pytest.mark.asyncio
async def test_no_slots_leaves_native_fill_chars_intact() -> None:
    widget = _SlottedWidget()
    async with _BorderTestApp(widget).run_test() as pilot:
        await pilot.pause()
        top_text = _get_strips(widget)[0].text
        # solid border fill is ─
        assert all(c == "─" for c in top_text[1:-1])


@pytest.mark.asyncio
async def test_inner_rows_have_vertical_bar_borders() -> None:
    widget = _SlottedWidget()
    async with _BorderTestApp(widget).run_test() as pilot:
        await pilot.pause()
        strips = _get_strips(widget)
        w = widget.size.width
        for strip in strips[1:-1]:
            text = strip.text
            assert text[0] == "│", f"Expected │ at row start, got {text[0]!r}"
            assert text[w - 1] == "│", f"Expected │ at row end, got {text[w-1]!r}"
```

Replace the entire initial draft in Step 1.1 with this version before running.

- [ ] **Step 1.5: Run tests to verify they pass**

```
uv run pytest tests/nova_widgets/test_custom_border.py -v
```

Expected: all PASS

- [ ] **Step 1.6: Coding-guideline follow-up checklist**

- [ ] Conventions file read: `docs/coding_conventions.md`
- [ ] All new functions/methods have full type annotations
- [ ] Naming: `snake_case` for functions/methods, `UpperCamelCase` for class
- [ ] `_inject_border_content` is module-level (not a method) — correct per convention since it is a pure helper not needing `self`
- [ ] Run `uv run ruff check src/nova_widgets/custom_border.py tests/nova_widgets/test_custom_border.py`
- [ ] Run `uv run ty check src/nova_widgets/custom_border.py`

---

## Task 2: Export `CustomBorderMixin`

**Files:**
- Modify: `src/nova_widgets/__init__.py`

- [ ] **Step 2.1: Add export**

In `src/nova_widgets/__init__.py`, the current content is:

```python
from .animated_icon import AnimatedIcon
from .icon import Icon
from .menu import Action, Menu, MenuBar

__all__ = ["Action", "AnimatedIcon", "Icon", "Menu", "MenuBar"]
```

Replace with:

```python
from .animated_icon import AnimatedIcon
from .custom_border import CustomBorderMixin
from .icon import Icon
from .menu import Action, Menu, MenuBar

__all__ = ["Action", "AnimatedIcon", "CustomBorderMixin", "Icon", "Menu", "MenuBar"]
```

- [ ] **Step 2.2: Verify import works**

```
uv run python -c "from nova_widgets import CustomBorderMixin; print('OK')"
```

Expected: `OK`

---

## Task 3: Migrate `PopupWidget` to `CustomBorderMixin`

**Files:**
- Modify: `src/nova_navigator/widgets/popup_widget.py`

- [ ] **Step 3.1: Write a regression test**

Create `tests/nova_widgets/test_popup_widget.py`:

```python
from __future__ import annotations

import pytest
from textual.app import App, ComposeResult
from textual.geometry import Region
from textual.strip import Strip

from nova_navigator.widgets.popup_widget import PopupWidget


class _TestPopup(PopupWidget):
    SHOW_CLOSE_BUTTON = True

    def __init__(self) -> None:
        super().__init__(title="Test", position=(0, 0))


class _PopupTestApp(App[None]):
    def __init__(self, popup: PopupWidget) -> None:
        super().__init__()
        self._popup = popup

    def compose(self) -> ComposeResult:
        yield self._popup


@pytest.mark.asyncio
async def test_close_button_absent_when_show_close_button_false() -> None:
    class _NoBtn(PopupWidget):
        SHOW_CLOSE_BUTTON = False

        def __init__(self) -> None:
            super().__init__(title="X", position=(0, 0))

    popup = _NoBtn()
    async with _PopupTestApp(popup).run_test() as pilot:
        await pilot.pause()
        w = popup.size.width
        strips = popup.render_lines(Region(0, 0, w, popup.size.height))
        top_text = strips[0].text
        # No close glyph 🗙 in the top border
        assert "🗙" not in top_text


@pytest.mark.asyncio
async def test_close_button_present_when_show_close_button_true() -> None:
    popup = _TestPopup()
    async with _PopupTestApp(popup).run_test() as pilot:
        await pilot.pause()
        w = popup.size.width
        strips = popup.render_lines(Region(0, 0, w, popup.size.height))
        top_text = strips[0].text
        assert "🗙" in top_text


@pytest.mark.asyncio
async def test_close_button_at_top_right() -> None:
    popup = _TestPopup()
    async with _PopupTestApp(popup).run_test() as pilot:
        await pilot.pause()
        w = popup.size.width
        strips = popup.render_lines(Region(0, 0, w, popup.size.height))
        top_text = strips[0].text
        # Slot " 🗙 " occupies positions w-4..w-2 (3 cells before the corner)
        # 🗙 is 1 cell wide, surrounded by spaces: top_text[w-3] == "🗙"
        assert top_text[w - 3] == "🗙"
```

Run:

```
uv run pytest tests/nova_widgets/test_popup_widget.py -v
```

Expected: PASS (all three tests pass against the existing implementation before migration, establishing the baseline)

- [ ] **Step 3.2: Migrate `PopupWidget`**

Open `src/nova_navigator/widgets/popup_widget.py`.

**a) Update the import block** — add `CustomBorderMixin` and remove unused module-level imports after migration:

```python
from __future__ import annotations

from enum import Enum, auto
from typing import ClassVar

from rich.segment import Segment
from rich.style import Style as RichStyle
from textual import events, on
from textual.binding import Binding, BindingType
from textual.geometry import Region
from textual.strip import Strip
from textual.widget import Widget

from nova_widgets.custom_border import CustomBorderMixin

_CLOSE_GLYPH = "🗙"  # cross, 1 cell wide
```

Remove the `_MIN_WIDTH_FOR_CLOSE_BTN` constant (no longer needed).

**b) Change class declaration**:

```python
class PopupWidget(CustomBorderMixin, Widget):
```

**c) Remove the `render_lines()` method entirely.**

Delete from `def render_lines(self, crop: Region) -> list[Strip]:` through `return strips` (the last line of that method).

**d) Add `render_border_top_right()` method** in place of the deleted `render_lines()`:

```python
    def render_border_top_right(self) -> Strip:
        if not self.SHOW_CLOSE_BUTTON:
            return Strip.blank(0)
        base_style = self._border_rich_style()
        if self._close_btn_hovered:
            glyph_style = base_style + RichStyle(reverse=True)
        else:
            glyph_style = base_style
        return Strip([Segment(f" {_CLOSE_GLYPH} ", glyph_style)], 3)
```

The `Region` import is no longer used after removing `render_lines`. Remove it from the import block.

- [ ] **Step 3.3: Run the regression tests**

```
uv run pytest tests/nova_widgets/test_popup_widget.py -v
```

Expected: all PASS

Also run all widget tests to ensure no regressions:

```
uv run pytest tests/nova_widgets/ tests/widgets/ -v
```

- [ ] **Step 3.4: Coding-guideline follow-up checklist**

- [ ] Full type annotations on `render_border_top_right`
- [ ] No unused imports remain
- [ ] `uv run ruff check src/nova_navigator/widgets/popup_widget.py`
- [ ] `uv run ty check src/nova_navigator/widgets/popup_widget.py`

---

## Task 4: Migrate `DirectoryBrowser` + add status slots

**Files:**
- Modify: `src/nova_navigator/widgets/directory_browser.py`

- [ ] **Step 4.1: Write the failing integration tests**

Create `tests/widgets/test_directory_browser_border.py`:

```python
from __future__ import annotations

import pytest
from textual.geometry import Region

from tests._utils.mock_filesystem import MockFilesystem
from tests.widgets._directory_browser_fixtures import run_browser


@pytest.mark.asyncio
async def test_bottom_border_empty_with_no_selection() -> None:
    fs = MockFilesystem(files={"/home/user/a.txt": b"hello"})
    async with run_browser(fs) as (pilot, browser, _):
        strips = browser.render_lines(
            Region(0, browser.size.height - 1, browser.size.width, 1)
        )
        bottom_text = strips[0].text
        assert "file" not in bottom_text


@pytest.mark.asyncio
async def test_bottom_border_shows_selected_file_count() -> None:
    fs = MockFilesystem(files={
        "/home/user/a.txt": b"hello",
        "/home/user/b.txt": b"world!",
    })
    async with run_browser(fs) as (pilot, browser, _):
        await pilot.press("down")    # move past ".." to first file
        await pilot.press("insert")  # select it
        await pilot.pause()

        strips = browser.render_lines(
            Region(0, browser.size.height - 1, browser.size.width, 1)
        )
        bottom_text = strips[0].text
        assert "1 file" in bottom_text


@pytest.mark.asyncio
async def test_bottom_right_shows_symlink_placeholder_for_symlink() -> None:
    """Bottom-right shows '(symlink)' placeholder when cursor is on a symlink."""
    # Use a file whose stat reports is_symlink=True.
    # MockFilesystem does not yet support real symlinks, so we patch stat directly.
    fs = MockFilesystem(files={"/home/user/link.txt": b""})
    from nova_navigator.vfs.types import Stat

    original_stat = fs.stat

    def patched_stat(path: object) -> Stat:
        s = original_stat(path)  # type: ignore[arg-type]
        if getattr(path, "name", "") == "link.txt":  # type: ignore[union-attr]
            return Stat(
                size=s.size,
                modified=s.modified,
                is_hidden=s.is_hidden,
                is_symlink=True,
            )
        return s

    fs.stat = patched_stat  # type: ignore[method-assign]

    async with run_browser(fs) as (pilot, browser, _):
        items = browser._shown_items
        symlink_row = next(i for i, p in enumerate(items) if p.name == "link.txt")
        browser.cursor_row = symlink_row
        await pilot.pause()

        strips = browser.render_lines(
            Region(0, browser.size.height - 1, browser.size.width, 1)
        )
        bottom_text = strips[0].text
        assert "symlink" in bottom_text
```

Run:

```
uv run pytest tests/widgets/test_directory_browser_border.py -v
```

Expected: FAIL (slots not yet implemented)

- [ ] **Step 4.2: Migrate `DirectoryBrowser` to `CustomBorderMixin`**

Open `src/nova_navigator/widgets/directory_browser.py`.

**a) Update import block** — add `CustomBorderMixin`:

The existing imports already include `from .popup_widget import PopupWidget`. Add after that:

```python
from nova_widgets.custom_border import CustomBorderMixin
```

**b) Change class declaration** from:

```python
class DirectoryBrowser(ScrollView):
```

to:

```python
class DirectoryBrowser(CustomBorderMixin, ScrollView):
```

- [ ] **Step 4.3: Add `_format_size()` module-level helper**

Add this function near the other module-level formatting helpers (after `column_formatter_modified`):

```python
def _format_size(size: int) -> str:
    """Format *size* bytes as a human-readable decimal magnitude string."""
    for unit in ["B", "K", "M", "G", "T"]:
        if size < 1000:
            return f"{size}{unit}"
        size //= 1000
    return f"{size}P"
```

- [ ] **Step 4.4: Implement the slot overrides on `DirectoryBrowser`**

Add the following two methods inside the `DirectoryBrowser` class (just before `render_line`):

```python
    def render_border_bottom_left(self) -> Strip:
        from rich.segment import Segment

        n = len(self._selected_items)
        if n == 0:
            return Strip.blank(0)
        total = sum(p.stat.size for p in self._selected_items if not p.stat.is_directory)
        text = f" {n} file{'s' if n != 1 else ''}, {_format_size(total)} "
        return Strip([Segment(text, self._border_rich_style())])

    def render_border_bottom_right(self) -> Strip:
        from rich.segment import Segment

        if not self._shown_items:
            return Strip.blank(0)
        item = self._shown_items[self.cursor_row]
        if isinstance(item, UpPath) or not item.stat.is_symlink:
            return Strip.blank(0)
        # TODO: replace placeholder once VPath.symlink_target is implemented
        text = " → (symlink) "
        return Strip([Segment(text, self._border_rich_style())])
```

- [ ] **Step 4.5: Run the integration tests**

```
uv run pytest tests/widgets/test_directory_browser_border.py -v
```

Expected: all PASS

Also run the full widget test suite:

```
uv run pytest tests/widgets/ -v
```

Expected: all PASS

- [ ] **Step 4.6: Coding-guideline follow-up checklist**

- [ ] Full type annotations on all new methods
- [ ] `uv run ruff check src/nova_navigator/widgets/directory_browser.py`
- [ ] `uv run ty check src/nova_navigator/widgets/directory_browser.py`

---

## Task 5: Final QA

- [ ] **Step 5.1: Run the full test suite**

```
uv run pytest
```

Expected: all PASS — no regressions.

- [ ] **Step 5.2: Run full QA pipeline**

```
uv run qa
```

Expected: zero failures (lint, type check, tests).

- [ ] **Step 5.3: Smoke test the running application**

```
uv run nn
```

Verify visually:
- Both directory panes render with rounded borders.
- Selecting a file shows `1 file, <size>` in the bottom-left of the border.
- Moving the cursor to a symlink shows `→ (symlink)` in the bottom-right of the border.
- Popup widgets (e.g. the filter bar) still show the close button at top-right.
