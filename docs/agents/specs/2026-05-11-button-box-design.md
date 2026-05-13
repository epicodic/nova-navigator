# ButtonBox Widget Design

**Date:** 2026-05-11
**Status:** Approved

## Overview

`ButtonBox` is a reusable Textual widget in `nova_widgets` that arranges `Button` widgets in a grid layout and provides arrow-key navigation between them.
It replaces the manual two-`Horizontal`-container pattern currently used in `DecisionDialog`.

## Location

- New file: `src/nova_widgets/button_box.py`
- Exported from `src/nova_widgets/__init__.py`

## API

```python
ButtonBox(rows: list[list[Button]] | list[Button], **kwargs)
```

- A flat `list[Button]` is treated as a single row and normalised to `[[btn1, btn2, ...]]` internally.
- A `list[list[Button]]` defines multiple rows explicitly.
- Rows may have different lengths.
- `**kwargs` are passed through to the `Widget` base class (supports `id`, `classes`, etc.).

## Internal Structure

`_rows: list[list[Button]]` — the normalised grid, stored at `__init__` time and never mutated.

### Compose

Yields a `Vertical` containing one `Horizontal` per row.
Each `Horizontal` has the CSS class `button-box-row`.

## Key Navigation

Bindings are declared on `ButtonBox` with `show=False`.

| Key | Behaviour |
|-----|-----------|
| `left` | Focus previous button in the same row. No wrap. |
| `right` | Focus next button in the same row. No wrap. |
| `up` | Focus the button at the same (or clamped) column index in the row above. |
| `down` | Focus the button at the same (or clamped) column index in the row below. |

If no button inside the box currently has focus, all arrow keys are silently ignored.
If the movement would leave the grid (first row + up, last row + down, etc.), focus stays on the current button.

## Default CSS

```css
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
```

No border or background — callers are responsible for styling.

## Usage in `DecisionDialog`

Replace the two separate `Horizontal` containers and the manual `_move_focus_vertical` method with a single `ButtonBox`:

```python
# Before
Horizontal(*buttons, id="button_box"),
Horizontal(*to_all_buttons, id="button_box_to_all"),

# After
ButtonBox([buttons, to_all_buttons], id="button_box"),
```

Remove from `DecisionDialog`:
- `BINDINGS` entries for `left`, `right`, `up`, `down`
- `action_focus_up`, `action_focus_down`, `_move_focus_vertical`
- CSS for `#button_box` and `#button_box_to_all`

## Testing

Tests live in `tests/nova_widgets/test_button_box.py`.

Scenarios to cover:
- Single-row construction via flat list and nested list produce identical behaviour.
- `left` / `right` move focus along a row; no movement at edges.
- `up` / `down` move to the same column index in adjacent rows.
- `up` / `down` clamp to the last column when the target row is shorter.
- Arrow keys are ignored when no button in the box has focus.
- `ButtonBox` integrates correctly into `DecisionDialog` (smoke test).
