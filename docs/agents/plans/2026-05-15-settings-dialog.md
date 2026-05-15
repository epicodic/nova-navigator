# Settings Dialog Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use skills:subagent-driven-development (recommended) or skills:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Implement a `ModelEditor` widget and `SettingsDialog` that let the user view and edit all application settings from a modal dialog.

**Architecture:** `ModelEditor` (in `nova_navigator/widgets/`) takes any flat `BaseModel` instance and auto-generates labelled rows with type-appropriate controls. `SettingsDialog` (in `nova_navigator/dialogs/`) iterates the top-level sub-sections of `Settings`, creates one tab per section with a `ModelEditor`, and saves on OK.

**Tech Stack:** Python 3.12, Textual, pytest, pytest-asyncio

**Coding Conventions:** `docs/coding_conventions.md` — read before implementing

**Spec:** `docs/agents/specs/2026-05-15-settings-dialog-design.md`

---

## File Map

| Path | Action |
|------|--------|
| `src/nova_navigator/widgets/model_editor.py` | **Create** — `ModelEditor` widget |
| `src/nova_navigator/dialogs/settings_dialog.py` | **Create** — `SettingsDialog` |
| `src/tools/dialog_tester.py` | **Modify** — register `SettingsDialog` |
| `src/nova_navigator/nova_navigator.py` | **Modify** — add `Binding` + `_action_settings` |
| `tests/nova_widgets/test_model_editor.py` | **Create** — unit tests for `ModelEditor` |

---

## Task 1: `ModelEditor` widget

**Files:**
- Create: `src/nova_navigator/widgets/model_editor.py`

### Background

`ModelEditor` receives a single `BaseModel` dataclass instance.
It uses `dataclasses.fields()` + `get_type_hints()` to discover editable fields and renders each as a three-column row:

```
| Name (title-cased) | Control (Checkbox / Input) | Comment (dimmed) |
```

The three columns use fixed CSS class widths so all rows align.
A `_field_controls: dict[str, Checkbox | Input]` and `_field_types: dict[str, type]` are built during `compose()` and consumed by `apply()`.

- [ ] **Step 1: Create the file with the complete `ModelEditor` implementation**

```python
"""ModelEditor — generic widget that renders a flat BaseModel as an editable form."""

from __future__ import annotations

import dataclasses
from typing import Any, get_type_hints

from textual.app import ComposeResult
from textual.containers import Horizontal, Vertical
from textual.widget import Widget
from textual.widgets import Label

from nova_navigator.config.model import BaseModel
from nova_widgets import Checkbox, Input

_PRIMITIVE_TYPES: frozenset[type] = frozenset({bool, str, int, float})


def _title_case(name: str) -> str:
    """Convert a snake_case name to Title Case words."""
    return " ".join(word.capitalize() for word in name.split("_"))


def _is_base_model_type(t: Any) -> bool:
    return isinstance(t, type) and issubclass(t, BaseModel)


class ModelEditor(Widget):
    """Widget that auto-renders all primitive fields of a BaseModel as labelled rows.

    Each row contains: a title-cased field name label, a type-appropriate
    control (Checkbox for bool, Input for str/int/float), and a dimmed comment
    label taken from field_comment metadata.

    All rows share CSS class selectors so columns are left-aligned across rows.

    After the user edits values, call :meth:`apply` to write them back to a
    target model instance.
    """

    DEFAULT_CSS = """
    ModelEditor {
        height: auto;

        .me-row {
            height: 1;
            margin-bottom: 1;
        }

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
    """

    _field_controls: dict[str, Checkbox | Input]
    _field_types: dict[str, type]

    def __init__(self, model: BaseModel, **kwargs: Any) -> None:
        super().__init__(**kwargs)
        self._model = model
        self._field_controls = {}
        self._field_types = {}

    def compose(self) -> ComposeResult:
        rows: list[Widget] = []
        hints = get_type_hints(type(self._model))

        for f in dataclasses.fields(self._model):  # type: ignore[arg-type]
            # Skip special metadata fields
            if "self_factory" in f.metadata or "toml_key" in f.metadata:
                continue

            field_type = hints.get(f.name)
            if field_type is None:
                continue

            # Skip nested BaseModel sub-sections
            if _is_base_model_type(field_type):
                continue

            # Only render the four supported primitives
            if field_type not in _PRIMITIVE_TYPES:
                continue

            current_value = getattr(self._model, f.name)
            comment = str(f.metadata.get("toml_comment", ""))

            if field_type is bool:
                control: Checkbox | Input = Checkbox(value=bool(current_value))
            elif field_type is int:
                control = Input(value=str(current_value), type="integer")
            elif field_type is float:
                control = Input(value=str(current_value), type="number")
            else:  # str
                control = Input(value=str(current_value))

            self._field_controls[f.name] = control
            self._field_types[f.name] = field_type

            rows.append(
                Horizontal(
                    Label(_title_case(f.name), classes="me-name"),
                    control.add_class("me-control"),
                    Label(comment, classes="me-comment"),
                    classes="me-row",
                )
            )

        yield Vertical(*rows)

    def apply(self, target: BaseModel) -> None:
        """Write current control values back into *target*.

        Coerces each value to the field's declared Python primitive type.
        For numeric Input fields, falls back to the existing target value on
        ValueError (e.g. if the user left the field empty).
        """
        for field_name, control in self._field_controls.items():
            field_type = self._field_types[field_name]
            if isinstance(control, Checkbox):
                value: Any = bool(control.value)
            elif field_type is int:
                try:
                    value = int(control.value)
                except ValueError:
                    value = getattr(target, field_name)
            elif field_type is float:
                try:
                    value = float(control.value)
                except ValueError:
                    value = getattr(target, field_name)
            else:
                value = control.value
            object.__setattr__(target, field_name, value)
```

- [ ] **Step 2: Coding-guideline follow-up checklist**

Run this checklist and record PASS/FAIL with file evidence:
- [ ] `docs/coding_conventions.md` read
- [ ] All functions/methods have full type annotations
- [ ] No `# noqa` or `# type: ignore` suppressions
- [ ] `snake_case` for functions/variables, `UpperCamelCase` for classes
- [ ] Run `uv run ruff check src/nova_navigator/widgets/model_editor.py` — expect zero errors
- [ ] Run `uv run ty check src/nova_navigator/widgets/model_editor.py` — expect zero errors

---

## Task 2: Unit tests for `ModelEditor`

**Files:**
- Create: `tests/nova_widgets/test_model_editor.py`

### Background

Uses the minimal `App.run_test()` pattern from AGENTS.md.
We define small in-test dataclass models so tests have no external config dependency.

- [ ] **Step 1: Write the test file**

```python
"""Unit tests for ModelEditor widget."""

from __future__ import annotations

from dataclasses import dataclass, field

import pytest
from textual.app import App, ComposeResult

from nova_navigator.config.model import BaseModel, field_comment
from nova_navigator.widgets.model_editor import ModelEditor
from nova_widgets import Checkbox, Input


# ── minimal test models ──────────────────────────────────────────────────────


@dataclass
class _FlatModel(BaseModel):
    flag: bool = field_comment(True, "A boolean flag.")
    name: str = field_comment("alice", "A string name.")
    count: int = field_comment(42, "An integer count.")
    ratio: float = field_comment(3.14, "A float ratio.")


@dataclass
class _NestedSection(BaseModel):
    value: int = field_comment(1, "Nested int.")


@dataclass
class _ModelWithNested(BaseModel):
    nested: _NestedSection = field(default_factory=_NestedSection)
    label: str = field_comment("top", "Top-level str.")


# ── test app wrapper ──────────────────────────────────────────────────────────


class _TestApp(App[None]):
    def __init__(self, editor: ModelEditor) -> None:
        super().__init__()
        self._editor = editor

    def compose(self) -> ComposeResult:
        yield self._editor


# ── tests ─────────────────────────────────────────────────────────────────────


@pytest.mark.asyncio
async def test_bool_field_renders_checkbox() -> None:
    model = _FlatModel()
    editor = ModelEditor(model)
    async with _TestApp(editor).run_test() as pilot:
        await pilot.pause()
        assert len(list(editor.query(Checkbox))) == 1


@pytest.mark.asyncio
async def test_str_int_float_fields_render_inputs() -> None:
    model = _FlatModel()
    editor = ModelEditor(model)
    async with _TestApp(editor).run_test() as pilot:
        await pilot.pause()
        # str, int, float → 3 Input widgets
        assert len(list(editor.query(Input))) == 3


@pytest.mark.asyncio
async def test_initial_checkbox_value_matches_model() -> None:
    model = _FlatModel(flag=False)
    editor = ModelEditor(model)
    async with _TestApp(editor).run_test() as pilot:
        await pilot.pause()
        checkbox = editor.query_one(Checkbox)
        assert checkbox.value is False


@pytest.mark.asyncio
async def test_initial_input_values_match_model() -> None:
    model = _FlatModel(name="bob", count=7, ratio=2.5)
    editor = ModelEditor(model)
    async with _TestApp(editor).run_test() as pilot:
        await pilot.pause()
        inputs = list(editor.query(Input))
        values = {inp.value for inp in inputs}
        assert "bob" in values
        assert "7" in values
        assert "2.5" in values


@pytest.mark.asyncio
async def test_apply_writes_back_to_target() -> None:
    model = _FlatModel(flag=True, name="alice", count=1, ratio=1.0)
    editor = ModelEditor(model)
    async with _TestApp(editor).run_test() as pilot:
        await pilot.pause()
        # Directly mutate the underlying control values
        editor._field_controls["flag"].value = False  # type: ignore[union-attr]
        editor._field_controls["name"].value = "new"  # type: ignore[union-attr]
        await pilot.pause()

        target = _FlatModel()
        editor.apply(target)
        assert target.flag is False
        assert target.name == "new"


@pytest.mark.asyncio
async def test_nested_base_model_field_not_rendered() -> None:
    """BaseModel sub-fields must not appear as rows."""
    model = _ModelWithNested()
    editor = ModelEditor(model)
    async with _TestApp(editor).run_test() as pilot:
        await pilot.pause()
        # Only 'label' (str) should be rendered — 'nested' must be skipped
        assert len(list(editor.query(Input))) == 1
        assert len(list(editor.query(Checkbox))) == 0


@pytest.mark.asyncio
async def test_apply_int_fallback_on_empty_input() -> None:
    """If numeric Input is emptied, apply() keeps the existing target value."""
    model = _FlatModel(count=99)
    editor = ModelEditor(model)
    async with _TestApp(editor).run_test() as pilot:
        await pilot.pause()
        for inp in editor.query(Input):
            if inp.value == "99":
                await inp.clear()
                break
        await pilot.pause()

    target = _FlatModel(count=99)
    editor.apply(target)
    assert target.count == 99
```

- [ ] **Step 2: Run the tests to verify they pass**

```
uv run pytest tests/nova_widgets/test_model_editor.py -v
```

Expected: all tests PASS.

- [ ] **Step 3: Coding-guideline follow-up checklist**

- [ ] `docs/coding_conventions.md` read
- [ ] Full type annotations on all functions
- [ ] No `# noqa` or `# type: ignore` suppressions
- [ ] Run `uv run ruff check tests/nova_widgets/test_model_editor.py` — zero errors
- [ ] Run `uv run ty check tests/nova_widgets/test_model_editor.py` — zero errors

---

## Task 3: `SettingsDialog`

**Files:**
- Create: `src/nova_navigator/dialogs/settings_dialog.py`

### Background

`SettingsDialog` extends `Dialog`.
It iterates the top-level fields of `Settings`, finds those whose type is a `BaseModel` subclass, and creates a `TabPane` + `ModelEditor` per section inside a `TabbedContent`.
On OK it calls `apply()` on each editor and then `settings.save()`.

- [ ] **Step 1: Create the file with the complete `SettingsDialog` implementation**

```python
"""SettingsDialog — modal dialog for editing all application settings."""

from __future__ import annotations

import dataclasses
from typing import Any, get_type_hints

from textual.app import ComposeResult
from textual.widgets import TabbedContent, TabPane

from nova_navigator.config.model import BaseModel
from nova_navigator.config.settings import Settings
from nova_navigator.widgets.model_editor import ModelEditor

from .dialog import DefaultButton, Dialog


def _title_case(name: str) -> str:
    """Convert a snake_case name to Title Case words."""
    return " ".join(word.capitalize() for word in name.split("_"))


class SettingsDialog(Dialog):
    """Modal dialog that exposes all Settings sections as tabs of editable rows."""

    DEFAULT_CSS = """
    SettingsDialog {
        #dialog_box {
            width: 70%;
            height: auto;
        }

        TabbedContent {
            height: auto;
        }
    }
    """

    _settings: Settings
    _editors: dict[str, ModelEditor]

    def __init__(self, settings: Settings) -> None:
        super().__init__(title="Settings", buttons=[DefaultButton.OK, DefaultButton.CANCEL])
        self._settings = settings
        self._editors = {}

    def compose_content(self) -> ComposeResult:
        hints = get_type_hints(Settings)
        panes: list[TabPane] = []

        for f in dataclasses.fields(Settings):  # type: ignore[arg-type]
            field_type = hints.get(f.name)
            if field_type is None:
                continue
            if not (isinstance(field_type, type) and issubclass(field_type, BaseModel)):
                continue

            section_value = getattr(self._settings, f.name)
            editor_id = f"editor_{f.name}"
            editor = ModelEditor(section_value, id=editor_id)
            self._editors[f.name] = editor
            panes.append(TabPane(_title_case(f.name), editor))

        yield TabbedContent(*panes)

    def action_accept_dialog(self) -> None:
        for field_name, editor in self._editors.items():
            editor.apply(getattr(self._settings, field_name))
        self._settings.save()
        super().action_accept_dialog()
```

- [ ] **Step 2: Coding-guideline follow-up checklist**

- [ ] `docs/coding_conventions.md` read
- [ ] Full type annotations on all functions
- [ ] No `# noqa` or `# type: ignore` suppressions
- [ ] Run `uv run ruff check src/nova_navigator/dialogs/settings_dialog.py` — zero errors
- [ ] Run `uv run ty check src/nova_navigator/dialogs/settings_dialog.py` — zero errors

---

## Task 4: Register `SettingsDialog` in `dialog_tester.py`

**Files:**
- Modify: `src/tools/dialog_tester.py`

- [ ] **Step 1: Add import and `DialogEntry`**

Add to the import block (near the other dialog imports):

```python
from nova_navigator.dialogs.settings_dialog import SettingsDialog
```

Add to `_ENTRIES` list (e.g. after the `ConnectToDialog` entry):

```python
DialogEntry(
    "SettingsDialog",
    "Edit all application settings (uses real config).",
    lambda: SettingsDialog(conf_.settings),
),
```

- [ ] **Step 2: Verify the entry appears**

```
uv run dialog_tester --list
```

Expected output contains `SettingsDialog`.

- [ ] **Step 3: Coding-guideline follow-up checklist**

- [ ] Run `uv run ruff check src/tools/dialog_tester.py` — zero errors
- [ ] Run `uv run ty check src/tools/dialog_tester.py` — zero errors

---

## Task 5: Wire keybinding in `nova_navigator.py`

**Files:**
- Modify: `src/nova_navigator/nova_navigator.py`

The menu already shows `"Settings"` with shortcut `"Ctrl+F1"` (line 109).
Add the matching `Binding` to `BINDINGS` and implement the action method.

- [ ] **Step 1: Add `Binding` to `MainScreen.BINDINGS`**

In `src/nova_navigator/nova_navigator.py`, in `MainScreen.BINDINGS`, add after the last existing entry:

```python
Binding("ctrl+f1", "settings", "Settings", show=False),
```

Full updated `BINDINGS` block:
```python
BINDINGS: ClassVar = [
    Binding("^q", "quit", "Quit"),
    Binding("ctrl+o", "toggle_maximized_terminal", "Maximize Terminal", priority=True),
    Binding("ctrl+l", "toggle_terminal", "Enlarge Terminal", priority=True),
    Binding("f4", "open_editor", "Edit"),
    Binding("f5", "copy_or_move_files(False)", "Copy"),
    Binding("f6", "copy_or_move_files(True)", "Move"),
    Binding("f8", "delete_files", "Delete"),
    Binding("ctrl+b", "show_bookmarks", "Bookmark"),
    Binding("ctrl+h", "toggle_hidden", description="Show/Hide Hidden Files", show=False),
    Binding("ctrl+d", "start_dummy_operation", "Start Dummy Operation"),
    Binding("ctrl+g", "go_to_path", "Go to Path", show=False),
    Binding("alt+left", "go_back", "Go Back", show=False),
    Binding("alt+right", "go_forward", "Go Forward", show=False),
    Binding("alt+up", "go_up", "Go Up"),
    Binding("ctrl+shift+g", "connect_to", "Connect to Remote", show=False),
    Binding("ctrl+r", "refresh", "Refresh", show=False, priority=True),
    Binding("ctrl+f1", "settings", "Settings", show=False),
]
```

- [ ] **Step 2: Add `_action_settings` method**

Add near the other dialog action methods (e.g. after `_action_manage_remotes`):

```python
@work
async def _action_settings(self) -> None:
    from nova_navigator.dialogs.settings_dialog import SettingsDialog

    dialog = SettingsDialog(conf_.settings)
    await dialog.run()
```

- [ ] **Step 3: Coding-guideline follow-up checklist**

- [ ] Run `uv run ruff check src/nova_navigator/nova_navigator.py` — zero errors
- [ ] Run `uv run ty check src/nova_navigator/nova_navigator.py` — zero errors

---

## Task 6: Full QA pass

- [ ] **Step 1: Run full test suite**

```
uv run pytest
```

Expected: all tests pass, no regressions.

- [ ] **Step 2: Run full QA**

```
uv run qa
```

Expected: zero lint, type, and test failures.
