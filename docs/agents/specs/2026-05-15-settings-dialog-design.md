# Settings Dialog Design

Date: 2026-05-15

## Overview

Implement a modal dialog that lets the user view and edit all application settings stored in `Settings`.
Settings are persisted to TOML via the existing `ModelConfig` machinery.
The dialog consists of two new components: a generic `ModelEditor` widget and a `SettingsDialog` that drives it.

## Goals

- Display every editable field in a `BaseModel` instance as a labelled row with an auto-selected control.
- Group top-level `BaseModel` sub-fields of `Settings` into tabs.
- On OK, write changes back to the model and persist to disk.
- On Cancel, discard all changes.

## Non-Goals

- Editing list-of-model fields (e.g. `list[RemoteConnection]`).
- Inline validation beyond what Input already provides.
- Live-apply (changes only take effect on OK).

## Components

### `ModelEditor`

File: `src/nova_navigator/widgets/model_editor.py`

A `Widget` that takes a single flat `BaseModel` instance and renders its fields as rows.

#### Construction

```python
ModelEditor(model: BaseModel)
```

Stores the model instance for later use in `apply()`.

#### Field discovery

Iterate `dataclasses.fields(model)` paired with `get_type_hints(type(model))`.
Skip a field if any of the following metadata keys are present: `self_factory`, `toml_key`.
Skip fields whose resolved type is a `BaseModel` subclass (nested sections — handled by `SettingsDialog`).
Skip fields whose type is not one of the four supported primitives (`bool`, `str`, `int`, `float`).

#### Row layout

All rows must form a visual table: the name column, control column, and comment column are left-aligned across every row.
This is achieved by giving each column class a fixed or fractional width in CSS — the same width applies to every row, so all controls line up vertically and all comment texts start at the same horizontal position.

Each visible field renders as a single `Horizontal` row (class `me-row`) containing three children:

1. `Label` (class `me-name`) — title-cased field name (e.g. `show_hidden_files` → `Show Hidden Files`).
   Fixed width, right-padded so the next column always starts at the same position.
2. Control (class `me-control`) — selected by type:
   - `bool` → `Checkbox`, initialised from the current field value.
   - `str` → `Input`, initialised from the current field value, no restriction.
   - `int` → `Input`, `type="integer"`, initialised from `str(value)`.
   - `float` → `Input`, `type="number"`, initialised from `str(value)`.
   Fixed width so comment text always starts at the same column.
3. `Label` (class `me-comment`) — comment text from `field_comment` metadata (`f.metadata.get("toml_comment", "")`).
   `1fr` width, dimmed styling. Starts at the same horizontal position for every row.

The three column widths are the only layout properties that matter for alignment; all three must be defined on the class selectors (not inline), so every row inherits the same widths.

During `compose()` the widget populates an instance dict `_field_controls: dict[str, Widget]` mapping field name → the rendered control (Checkbox or Input), and `_field_types: dict[str, type]` mapping field name → the Python primitive type. These are used by `apply()`.

#### `apply()` method

```python
def apply(self, target: BaseModel) -> None:
```

Iterates `_field_controls`. Uses `_field_types` to coerce the control's value to the correct Python type before writing it to `target` with `object.__setattr__`.
Coercion:
- `Checkbox` → `bool(checkbox.value)`
- `Input` for `int` → `int(input.value)` (fallback to existing value on `ValueError`)
- `Input` for `float` → `float(input.value)` (fallback on `ValueError`)
- `Input` for `str` → `input.value`

#### CSS

```css
ModelEditor {
    height: auto;

    .model-editor-row {
        height: 1;
        margin-bottom: 1;
    }

    /* Fixed widths ensure all three columns are left-aligned across every row */
    .me-name {
        width: 24;
    }

    .me-control {
        width: 20;
    }

    .me-comment {
        width: 1fr;
        color: $text-muted;
    }
}
```

---

### `SettingsDialog`

File: `src/nova_navigator/dialogs/settings_dialog.py`

A `Dialog` subclass that composes one `ModelEditor` per top-level sub-section of `Settings`.

#### Construction

```python
SettingsDialog(settings: Settings)
```

Stores a reference to the live `Settings` instance.

#### Layout

`compose_content()` builds a `TabbedContent`.
For each field of `Settings` whose resolved type is a `BaseModel` subclass:
- Create a `TabPane` whose title is the title-cased field name.
- Inside it, place a `ModelEditor` for `getattr(settings, field_name)`, with id `editor_<field_name>`.

Buttons: OK and Cancel (using existing `DefaultButton.OK` / `DefaultButton.CANCEL`).

#### Save behaviour

Override `action_accept_dialog()`:
1. For each section field, query `editor_<field_name>`, call `editor.apply(getattr(self._settings, field_name))`.
2. Call `self._settings.save()`.
3. Call `super().action_accept_dialog()` to dismiss.

#### CSS

```css
SettingsDialog {
    #dialog_box {
        width: 70%;
        height: auto;
    }

    TabbedContent {
        height: auto;
    }
}
```

---

## Integration

### `dialog_tester.py`

Register `SettingsDialog` in `src/tools/dialog_tester.py`:

```python
DialogEntry(
    name="SettingsDialog",
    factory=lambda: SettingsDialog(Settings.load()),
)
```

### `main.py` keybinding

Add a binding in `MainScreen` to open the settings dialog, e.g. `ctrl+comma` → `action_settings`.

```python
async def action_settings(self) -> None:
    from nova_navigator.dialogs.settings_dialog import SettingsDialog
    await SettingsDialog(conf_.settings).run()
```

---

## Testing

File: `tests/nova_widgets/test_model_editor.py`

Use `App.run_test()` with a minimal wrapper app.
Test cases:

1. **Correct widget types** — given a model with one `bool`, one `str`, one `int` field, assert that the composed DOM contains one `Checkbox` and two `Input` widgets.
2. **Initial values** — assert that `Checkbox.value` and `Input.value` match the model's field values.
3. **`apply()` writes back** — set new values on the controls, call `apply(target)`, assert `target` fields updated.
4. **Skips BaseModel sub-fields** — a model with a nested `BaseModel` field renders no row for it.
5. **Skips computed/key fields** — fields with `self_factory` or `toml_key` metadata are not rendered.

---

## File Summary

| Path | Action |
|------|--------|
| `src/nova_navigator/widgets/model_editor.py` | New |
| `src/nova_navigator/dialogs/settings_dialog.py` | New |
| `src/tools/dialog_tester.py` | Register `SettingsDialog` |
| `src/nova_navigator/main.py` | Add keybinding + action |
| `tests/nova_widgets/test_model_editor.py` | New tests |
