# Custom Border Widget — Design Spec

Date: 2026-05-03

## Overview

Replace Textual's CSS-driven border with a custom-drawn border mixin that exposes four content slots: top-left, top-right, bottom-left, and bottom-right.
The mixin lives in `nova_widgets/` so it is reusable across the project.
The first consumers are `DirectoryBrowser` (status information at bottom) and `PopupWidget` (close button at top-right).

---

## Decisions Summary

| Topic | Decision |
|---|---|
| Implementation form | Mixin class `CustomBorderMixin` in `nova_widgets/` |
| Border drawing | `render_lines()` override; `border: none` via CSS |
| Inner content | `super().render_lines()` called with crop contracted by 1 on all sides |
| Slot API | Four overridable methods returning `Strip` |
| Fill characters | Read from Textual's `_border.BORDER_CHARS` using the widget's CSS `border` style name |
| Border style | Follows the widget's `styles.border` CSS value — compatible with all Textual border styles |
| PopupWidget migration | Migrate close-button logic from `render_lines()` override to `render_border_top_right()` slot |
| VFS changes | Deferred — symlink target slot uses a `"TODO"` placeholder until VFS `readlink()` is implemented |

---

## 1. `CustomBorderMixin`

### 1.1 Location

New file: `src/nova_widgets/custom_border.py`

### 1.2 Class declaration

```python
class CustomBorderMixin:
    """Mixin that replaces Textual's CSS border with a custom-drawn border.

    Mix in before Widget (or any Widget subclass) in the MRO.
    Set `border: <style> <color>` in CSS as usual — the mixin reads the style to pick characters.
    Do NOT set padding to compensate for the border; the mixin handles the geometry.

    Override any of the four slot methods to inject content into the border corners.
    Return Strip.blank(0) (the default) to leave a section empty.
    """
```

MRO example:
```python
class DirectoryBrowser(CustomBorderMixin, ScrollView): ...
class PopupWidget(CustomBorderMixin, Widget): ...
```

### 1.3 CSS requirement

The mixin adds to `DEFAULT_CSS` via class-level concatenation:

```css
CustomBorderMixin {
    border: none;
}
```

The `border: none` declaration removes Textual's native border so there is no double-draw.
The widget's own CSS sets the desired visual style, e.g. `border: round $accent;`.
The mixin reads `self.styles.border_top` at render time to obtain both the edge type (`"round"`, `"solid"`, etc.) and the border color.

### 1.4 Slot API

```python
def render_border_top_left(self) -> Strip:
    return Strip.blank(0)

def render_border_top_right(self) -> Strip:
    return Strip.blank(0)

def render_border_bottom_left(self) -> Strip:
    return Strip.blank(0)

def render_border_bottom_right(self) -> Strip:
    return Strip.blank(0)
```

Each method returns a `Strip` of any width.
The mixin clips the strip if it would overflow its allocated half of the border row.

### 1.5 `render_lines()` implementation

```python
def render_lines(self, crop: Region) -> list[Strip]:
    ...
```

**Step 1 — Determine geometry.**
Full widget size: `w, h = self.size`.
Border is always exactly 1 cell wide on all four sides, so:
- Inner region: `Region(1, 1, w - 2, h - 2)`
- Outer region (full widget): `Region(0, 0, w, h)`

**Step 2 — Call super() for inner content.**
Contract the `crop` argument to only the inner rows/columns, call `super().render_lines(contracted_crop)`, then re-index the resulting strips back to full widget coordinates.
Rows outside the inner region (i.e. y == 0 and y == h-1 in widget space) are built from scratch by the mixin.

**Step 3 — Build left/right vertical bars.**
For each inner content row (y in 1..h-2), prepend a `│` segment (border color) and append a `│` segment.

**Step 4 — Build top border row.**
Layout:
```
╭  [top_left_strip]  ──────────────────  [top_right_strip]  ╮
```

Procedure:
1. Render corner chars `╭` and `╮` (1 cell each) from `BORDER_CHARS`.
2. Call `self.render_border_top_left()` → `left_strip`.
3. Call `self.render_border_top_right()` → `right_strip`.
4. Available fill width = `w - 2 - left_strip.cell_length - right_strip.cell_length`.
5. If fill width < 0, clip the longer strip first (right, then left) until fill width ≥ 0.
6. Fill = `Strip([Segment("─" * fill_width, border_style)])`.
7. Assemble: `corner_left + left_strip + fill + right_strip + corner_right`.

**Step 5 — Build bottom border row.**
Symmetric to step 4 using `╰`/`╯` corners and `render_border_bottom_left()` / `render_border_bottom_right()`.

**Step 6 — Apply crop.**
Return only the strips that fall within the requested `crop` region.

### 1.6 Border style resolution

Helper `_get_border_chars(self) -> tuple[str, str, str, str, str, RichStyle]`:
Returns `(top_left, top_right, bot_left, bot_right, fill_char, border_rich_style)`.

- Read `edge_type, color = self.styles.border_top` (Textual `BorderDefinition`).
- Look up `BORDER_CHARS[edge_type]` — a 3×3 tuple of box-drawing chars.
- Convert color to `RichStyle` for use in `Segment`.
- Fall back to `"solid"` chars if `edge_type` is unrecognised.

### 1.7 Size contract

`get_content_width()` and `get_content_height()` are **not** overridden in the mixin.
Textual already accounts for the native border in content size.
Since we disable the native border (`border: none`) and draw our own, the mixin must override these to add back the 2-cell deduction:

```python
def get_content_width(self, container: Size, viewport: Size) -> int:
    return super().get_content_width(container, viewport) - 2

def get_content_height(self, container: Size, viewport: Size, width: int) -> int:
    return super().get_content_height(container, viewport, width) - 2
```

This ensures that children laid out inside the widget see the correct available space.
Note: with `border: none`, Textual computes `content_width = widget_width` (no deduction).
The override re-introduces the 2-cell deduction that the mixin's own border occupies.

---

## 2. `DirectoryBrowser` changes

### 2.1 Mixin adoption

Change class declaration:

```python
class DirectoryBrowser(CustomBorderMixin, ScrollView):
```

Remove the CSS `border:` rules from `DirectoryBrowser.DEFAULT_CSS`; add them back via `CustomBorderMixin`-compatible CSS in the application stylesheet (`nn.tcss`) exactly as before — the mixin reads the CSS value at render time.

### 2.2 Bottom-left slot — selected files info

Override `render_border_bottom_left()`:

```python
def render_border_bottom_left(self) -> Strip:
    n = len(self._selected_items)
    if n == 0:
        return Strip.blank(0)
    total = sum(p.stat.size for p in self._selected_items if not p.stat.is_directory)
    text = f" {n} file{'s' if n != 1 else ''}, {_format_size(total)} "
    return Strip([Segment(text, border_style)])
```

`border_style` is obtained by calling the mixin's `_get_border_chars()` helper.
`_format_size` reuses the existing `column_formatter_size` magnitude logic.

### 2.3 Bottom-right slot — symlink target (placeholder)

Override `render_border_bottom_right()`:

```python
def render_border_bottom_right(self) -> Strip:
    item = self._shown_items[self.cursor_row] if self._shown_items else None
    if item is None or isinstance(item, UpPath):
        return Strip.blank(0)
    if not item.stat.is_symlink:
        return Strip.blank(0)
    # TODO: replace with item.symlink_target once VPath.symlink_target is implemented
    text = " → (symlink) "
    return Strip([Segment(text, border_style)])
```

`border_style` is obtained by calling the mixin's `_get_border_chars()` helper.
The VFS `readlink()` method and `VPath.symlink_target` property are deferred to a future task.

### 2.4 Refresh triggering

The bottom slots depend on `_selected_items` and `cursor_row`.
Both of these already call `self.refresh()` when they change (cursor_row is a `Reactive`; `_selected_items` is mutated only in methods that call `refresh()`).
No additional refresh logic is needed.

---

## 3. `PopupWidget` changes

### 3.1 Mixin adoption

Change class declaration:

```python
class PopupWidget(CustomBorderMixin, Widget):
```

### 3.2 Close button migration

Remove the existing `render_lines()` override and the `_close_btn_hovered` field.
Override `render_border_top_right()` instead:

```python
def render_border_top_right(self) -> Strip:
    if not self.SHOW_CLOSE_BUTTON:
        return Strip.blank(0)
    style = RichStyle(reverse=True) if self._close_btn_hovered else border_style
    return Strip([Segment(f" {_CLOSE_GLYPH} ", style)], 3)
```

`border_style` is obtained by calling the mixin's `_get_border_chars()` helper.
Mouse hit-testing for the close button must also be updated.
The close button occupies `[w-4, w-1)` in the top border row (same position as before).
`_on_mouse_move` checks `event.y == 0` and `w - 4 <= event.x < w - 1`.

---

## 4. `nova_widgets` export

Add `CustomBorderMixin` to `src/nova_widgets/__init__.py`:

```python
from nova_widgets.custom_border import CustomBorderMixin
```

---

## 5. Testing

### 5.1 Unit tests — `CustomBorderMixin`

New file: `tests/nova_widgets/test_custom_border.py`

Test cases (using `App.run_test()`):
- Border characters are drawn at the correct positions (corners, fill, verticals).
- Top-left and top-right slots appear in the correct positions.
- Bottom-left and bottom-right slots appear in the correct positions.
- Slots are clipped when combined length exceeds available width.
- Inner content rows have `│` prepended and appended.
- Changing a slot value and calling `refresh()` produces updated output.

### 5.2 Integration — `DirectoryBrowser`

Extend existing `tests/widgets/` tests:
- Selected-files count and size appear in the bottom border after selecting items.
- Bottom border is empty when no items are selected.
- Symlink target appears in bottom-right when cursor is on a symlink.

### 5.3 Integration — `PopupWidget`

Extend existing tests or add to `tests/nova_widgets/`:
- Close button appears in top-right when `SHOW_CLOSE_BUTTON = True`.
- Close button is absent when `SHOW_CLOSE_BUTTON = False`.
- Hovering the close button area applies reverse style.
