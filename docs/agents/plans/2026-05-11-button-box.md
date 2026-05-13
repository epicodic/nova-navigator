# ButtonBox Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use skills:subagent-driven-development (recommended) or skills:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Implement a `ButtonBox` widget in `nova_widgets` that arranges `Button` widgets in a grid with arrow-key navigation, and use it in `DecisionDialog`.

**Architecture:** `ButtonBox` is a single `Widget` subclass that holds a normalised `list[list[Button]]` grid, composes one `Horizontal` row per list entry inside a `Vertical`, and intercepts arrow keys to move focus within the grid. `DecisionDialog` is updated to use `ButtonBox` in place of its two manual `Horizontal` containers and hand-rolled navigation code.

**Tech Stack:** Python 3.12, pytest, Textual

**Coding Conventions:** `docs/coding_conventions.md` — read before implementing

---

## File Map

| Action | Path | Purpose |
|--------|------|---------|
| Create | `src/nova_widgets/button_box.py` | `ButtonBox` widget |
| Modify | `src/nova_widgets/__init__.py` | export `ButtonBox` |
| Create | `tests/nova_widgets/test_button_box.py` | tests for `ButtonBox` |
| Modify | `src/nova_navigator/dialogs/decision_dialog.py` | use `ButtonBox` |

---

### Task 1: `ButtonBox` widget — skeleton + compose

**Files:**
- Create: `src/nova_widgets/button_box.py`

- [ ] **Step 1: Write the failing test — compose produces correct rows**

```python
# tests/nova_widgets/test_button_box.py
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
```

- [ ] **Step 2: Run tests to verify they fail**

```
uv run pytest tests/nova_widgets/test_button_box.py::test_compose_multi_row tests/nova_widgets/test_button_box.py::test_compose_flat_list_is_single_row -v
```

Expected: `ModuleNotFoundError` or `ImportError` — file not yet created.

- [ ] **Step 3: Implement the skeleton**

Create `src/nova_widgets/button_box.py`:

```python
from __future__ import annotations

from typing import Any, ClassVar

from textual.binding import Binding
from textual.containers import Horizontal, Vertical
from textual.widget import Widget
from textual.widgets import Button


class ButtonBox(Widget):
    """A grid of buttons with arrow-key navigation.

    Args:
        rows: Either a list of rows (each row is a list of Button widgets),
            or a flat list of Button widgets (treated as a single row).
    """

    DEFAULT_CSS = """
    ButtonBox {
        height: auto;

        .button-box-row {
            height: auto;
            align-horizontal: center;
        }

        Button {
            width: auto;
            margin: 0 1;
        }
    }
    """

    BINDINGS: ClassVar = [
        Binding("left", "focus_left", show=False),
        Binding("right", "focus_right", show=False),
        Binding("up", "focus_up", show=False),
        Binding("down", "focus_down", show=False),
    ]

    def __init__(
        self,
        rows: list[list[Button]] | list[Button],
        *,
        id: str | None = None,
        classes: str | None = None,
        **kwargs: Any,
    ) -> None:
        super().__init__(id=id, classes=classes, **kwargs)
        if rows and isinstance(rows[0], Button):
            self._rows: list[list[Button]] = [list(rows)]  # type: ignore[arg-type]
        else:
            self._rows = [list(row) for row in rows]  # type: ignore[arg-type]

    def compose(self):
        with Vertical():
            for row in self._rows:
                with Horizontal(classes="button-box-row"):
                    for btn in row:
                        yield btn
```

- [ ] **Step 4: Run tests to verify they pass**

```
uv run pytest tests/nova_widgets/test_button_box.py::test_compose_multi_row tests/nova_widgets/test_button_box.py::test_compose_flat_list_is_single_row -v
```

Expected: PASS

- [ ] **Step 5: Coding-guideline follow-up checklist**

- [ ] `docs/coding_conventions.md` read
- [ ] Full type annotations on all functions/methods (`compose` return type: `ComposeResult`)
- [ ] Naming: `snake_case` members, `UpperCamelCase` class — confirmed
- [ ] `uv run ruff check src/nova_widgets/button_box.py` — zero issues
- [ ] Fix any violations before proceeding

  Note: fix the `compose` return type annotation to `ComposeResult` and add the import.

---

### Task 2: Left / right navigation

**Files:**
- Modify: `src/nova_widgets/button_box.py`
- Modify: `tests/nova_widgets/test_button_box.py`

- [ ] **Step 1: Write the failing tests**

Append to `tests/nova_widgets/test_button_box.py`:

```python
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
```

- [ ] **Step 2: Run tests to verify they fail**

```
uv run pytest tests/nova_widgets/test_button_box.py::test_right_moves_focus_within_row tests/nova_widgets/test_button_box.py::test_left_moves_focus_within_row tests/nova_widgets/test_button_box.py::test_right_does_not_wrap_at_end tests/nova_widgets/test_button_box.py::test_left_does_not_wrap_at_start -v
```

Expected: FAIL — actions not implemented yet.

- [ ] **Step 3: Implement left/right actions**

Add a helper and two action methods to `ButtonBox`:

```python
def _focused_position(self) -> tuple[int, int] | None:
    """Return (row_idx, col_idx) of the focused button, or None."""
    focused = self.app.focused
    for r, row in enumerate(self._rows):
        for c, btn in enumerate(row):
            if btn is focused:
                return r, c
    return None

def action_focus_left(self) -> None:
    pos = self._focused_position()
    if pos is None:
        return
    r, c = pos
    if c > 0:
        self._rows[r][c - 1].focus()

def action_focus_right(self) -> None:
    pos = self._focused_position()
    if pos is None:
        return
    r, c = pos
    if c < len(self._rows[r]) - 1:
        self._rows[r][c + 1].focus()
```

- [ ] **Step 4: Run tests to verify they pass**

```
uv run pytest tests/nova_widgets/test_button_box.py::test_right_moves_focus_within_row tests/nova_widgets/test_button_box.py::test_left_moves_focus_within_row tests/nova_widgets/test_button_box.py::test_right_does_not_wrap_at_end tests/nova_widgets/test_button_box.py::test_left_does_not_wrap_at_start -v
```

Expected: PASS

- [ ] **Step 5: Coding-guideline follow-up checklist**

- [ ] Full type annotations on new methods — confirmed
- [ ] `uv run ruff check src/nova_widgets/button_box.py` — zero issues

---

### Task 3: Up / down navigation

**Files:**
- Modify: `src/nova_widgets/button_box.py`
- Modify: `tests/nova_widgets/test_button_box.py`

- [ ] **Step 1: Write the failing tests**

Append to `tests/nova_widgets/test_button_box.py`:

```python
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
        # Do not focus any button — app.focused may be the app itself or the box
        await pilot.press("right")
        await pilot.press("down")
        await pilot.pause()
        # Neither button should be focused as a result of the key presses
        focused = app.focused
        assert focused is not app.query_one("#a", Button)
        assert focused is not app.query_one("#b", Button)
```

- [ ] **Step 2: Run tests to verify they fail**

```
uv run pytest tests/nova_widgets/test_button_box.py::test_down_moves_to_same_column_in_next_row tests/nova_widgets/test_button_box.py::test_up_moves_to_same_column_in_prev_row tests/nova_widgets/test_button_box.py::test_down_clamps_column_when_target_row_is_shorter tests/nova_widgets/test_button_box.py::test_up_does_not_move_on_first_row tests/nova_widgets/test_button_box.py::test_down_does_not_move_on_last_row -v
```

Expected: FAIL — actions not implemented yet.

- [ ] **Step 3: Implement up/down actions**

Add to `ButtonBox`:

```python
def action_focus_up(self) -> None:
    pos = self._focused_position()
    if pos is None:
        return
    r, c = pos
    if r > 0:
        target_row = self._rows[r - 1]
        target_row[min(c, len(target_row) - 1)].focus()

def action_focus_down(self) -> None:
    pos = self._focused_position()
    if pos is None:
        return
    r, c = pos
    if r < len(self._rows) - 1:
        target_row = self._rows[r + 1]
        target_row[min(c, len(target_row) - 1)].focus()
```

- [ ] **Step 4: Run tests to verify they pass**

```
uv run pytest tests/nova_widgets/test_button_box.py -v
```

Expected: All tests PASS.

- [ ] **Step 5: Coding-guideline follow-up checklist**

- [ ] Full type annotations on new methods — confirmed
- [ ] `uv run ruff check src/nova_widgets/button_box.py` — zero issues

---

### Task 4: Export `ButtonBox` from `nova_widgets`

**Files:**
- Modify: `src/nova_widgets/__init__.py`

- [ ] **Step 1: Add export**

In `src/nova_widgets/__init__.py`, add the import and `__all__` entry:

Current file:
```python
from .animated_icon import AnimatedIcon
from .custom_border import CustomBorderMixin
from .icon import Icon
from .menu import Action, Menu, MenuBar

__all__ = ["Action", "AnimatedIcon", "CustomBorderMixin", "Icon", "Menu", "MenuBar"]
```

Updated file:
```python
from .animated_icon import AnimatedIcon
from .button_box import ButtonBox
from .custom_border import CustomBorderMixin
from .icon import Icon
from .menu import Action, Menu, MenuBar

__all__ = ["Action", "AnimatedIcon", "ButtonBox", "CustomBorderMixin", "Icon", "Menu", "MenuBar"]
```

- [ ] **Step 2: Verify import works**

```
uv run python -c "from nova_widgets import ButtonBox; print(ButtonBox)"
```

Expected: prints the class without errors.

- [ ] **Step 3: Run full test suite to confirm no regressions**

```
uv run pytest tests/nova_widgets/ -v
```

Expected: All tests PASS.

---

### Task 5: Use `ButtonBox` in `DecisionDialog`

**Files:**
- Modify: `src/nova_navigator/dialogs/decision_dialog.py`

- [ ] **Step 1: Write a smoke test for `DecisionDialog` with `ButtonBox`**

Append to `tests/nova_widgets/test_button_box.py`:

```python
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
```

- [ ] **Step 2: Run test to verify it passes (it exercises existing code)**

```
uv run pytest tests/nova_widgets/test_button_box.py::test_button_box_arrow_navigation_in_two_rows -v
```

Expected: PASS (no code changes yet — this is an integration check on the existing `ButtonBox`).

- [ ] **Step 3: Update `DecisionDialog.compose` to use `ButtonBox`**

In `src/nova_navigator/dialogs/decision_dialog.py`:

Replace the import line for containers:
```python
from textual.containers import Horizontal, Vertical
```
with:
```python
from textual.containers import Vertical

from nova_widgets import ButtonBox
```

Replace the `compose` method body (the part that builds `button_boxes`):

Old:
```python
        button_boxes = []
        if buttons:
            button_boxes.append(Horizontal(*buttons, id="button_box"))
        if to_all_buttons:
            button_boxes.append(Horizontal(*to_all_buttons, id="button_box_to_all"))
```

New:
```python
        rows: list[list[widgets.Button]] = []
        if buttons:
            rows.append(buttons)
        if to_all_buttons:
            rows.append(to_all_buttons)
        button_box = ButtonBox(rows, id="button_box")
        button_boxes = [button_box]
```

- [ ] **Step 4: Remove manual navigation code and bindings from `DecisionDialog`**

Remove the following BINDINGS entries from `DecisionDialog.BINDINGS`:
```python
Binding(key="left", action="app.focus_previous", show=False),
Binding(key="right", action="app.focus_next", show=False),
Binding(key="up", action="focus_up", show=False),
Binding(key="down", action="focus_down", show=False),
```

Remove these methods entirely:
```python
def action_focus_up(self) -> None:
    self._move_focus_vertical(-1)

def action_focus_down(self) -> None:
    self._move_focus_vertical(1)

def _move_focus_vertical(self, direction: int) -> None:
    ...
```

- [ ] **Step 5: Update CSS in `DecisionDialog.DEFAULT_CSS`**

Remove the two stale CSS rules:
```css
        #button_box {
            height: auto;
            align-horizontal: center;
        }
        #button_box_to_all {
            height: auto;
            align-horizontal: center;
        }
```

Replace with a single rule that styles the `ButtonBox`:
```css
        #button_box {
            height: auto;
        }
```

- [ ] **Step 6: Run full QA**

```
uv run qa
```

Expected: zero failures (lint, type check, tests).

---

### Task 6: Final verification

- [ ] **Step 1: Run entire test suite**

```
uv run pytest -v
```

Expected: all tests PASS.

- [ ] **Step 2: Run QA**

```
uv run qa
```

Expected: zero failures.

- [ ] **Step 3: Confirm `ButtonBox` compose return type is annotated**

Check that `compose` in `button_box.py` has `-> ComposeResult` and `from textual.app import ComposeResult` is imported.
