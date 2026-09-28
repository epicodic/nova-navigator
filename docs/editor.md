# Text Editor Package (`nova_editor`)

This document describes the architecture and design of the `nova_editor` package, a standalone Textual-based text editor with vendored TextArea widget implementation.

---

## Overview

**Purpose:** Provide a modern, Textual-based text editor widget and standalone application designed to handle large files and very long lines with minimal overhead. The package is separate from `nova_navigator` and can be used independently.

**Status:** Scaffold phase. The widget currently uses a vendored copy of Textual 8.2.8's `TextArea` (renamed to `NovaTextArea`) with no modifications to core behavior. Future versions will add:
- Lazy document loading for very large files (>1GB)
- Optimizations for lines longer than typical terminal widths
- Custom buffer management and indexing
- Integration with `nova_navigator`'s VFS layer

---

## Layering

The package is organized in three layers:

### 1. **Core Layer** (`nova_editor/core/`)

Textual-free non-UI editing logic. Currently a stub; will eventually contain:
- Buffer management abstractions
- Efficient line/column indexing
- Lazy document strategies
- Large-file handling utilities

**Key rule:** No imports from `textual` are permitted in this module.

### 2. **Widget Layer** (`nova_editor/widget/`)

Textual widget implementation containing:
- **`NovaTextArea`** — the main editor widget (vendored from Textual 8.2.8, renamed from `TextArea`)
- **`_text_area_theme.py`** — theme support for syntax highlighting
- **`document/`** — vendored document package (implementation details)

The widget currently behaves exactly like Textual's stock `TextArea`. All public APIs are preserved.

### 3. **Application Layer** (`nova_editor/app.py`)

Standalone Textual app (`NovaEditApp`) providing:
- File loading/saving via `Ctrl+S` and `Ctrl+Q`
- Footer showing file path and keyboard shortcuts
- Entry point `main()` for the `nova_edit` CLI command

---

## Vendoring Policy

Rather than subclassing Textual's `TextArea`, we vendor (copy) the full implementation and its dependencies. This is intentional:

| Reason | Benefit |
|--------|---------|
| **Anticipated heavy modifications** | Future work on large-file support will be deep and pervasive; vendoring avoids subclass fragility. |
| **Minimize Textual coupling** | Textual's internals may change; vendoring gives us control over API stability. |
| **Performance optimization** | We can optimize for Nova Navigator's specific use cases. |
| **Version independence** | We pin the vendored code, not Textual. |

### Vendored Files

| File | Upstream | Notes |
|------|----------|-------|
| `widget/_text_area.py` | Textual `widgets/_text_area.py` | Main widget; class renamed `TextArea` → `NovaTextArea` |
| `widget/_text_area_theme.py` | Textual `_text_area_theme.py` | Theme system; imports updated |
| `document/_*.py` | Textual `document/` package | Document model (6 files); internal imports updated |

See `src/nova_editor/UPSTREAM.md` for detailed file inventory and upgrade instructions.

---

## Public API

**Main exports** (via `nova_editor.__init__.py`):
- `NovaTextArea` — the editor widget

**Widget subpackage exports** (via `nova_editor.widget.__init__.py`):
- `NovaTextArea`
- `TextAreaLanguage`
- `ThemeDoesNotExist`
- `LanguageDoesNotExist`

**Usage example:**
```python
from nova_editor import NovaTextArea
from textual.app import App, ComposeResult

class MyEditorApp(App):
    def compose(self) -> ComposeResult:
        yield NovaTextArea(text="Hello, world!")
```

---

## Running the Editor

### Standalone application

```sh
uv run nova_edit                    # Open editor with no file
uv run nova_edit /path/to/file.txt  # Open a specific file
```

### Programmatic use

```python
from nova_editor import NovaTextArea

widget = NovaTextArea(text="initial content")
# Mount in a Textual app as normal
```

---

## Testing

Tests are located under `tests/nova_editor/`:

- **`test_widget.py`** — Widget mounting, typing, undo/redo, newline handling
- **`test_app.py`** — File loading/saving, app initialization, error handling
- **`test_independence.py`** — Verify no dependency on `nova_navigator`

Run all tests:
```sh
uv run pytest tests/nova_editor/
```

---

## Updating from Upstream

When Textual releases a new version and we want to update our vendored code:

1. Identify the target Textual version
2. Extract the updated files from the Textual repository
3. Carefully merge changes while preserving:
   - `NovaTextArea` class name (instead of `TextArea`)
   - Import paths pointing to `nova_editor` packages
4. Test thoroughly with `uv run qa`
5. Update the version reference in `UPSTREAM.md`

See `UPSTREAM.md` for detailed upgrade instructions.

---

## Future Work

- **Lazy loading:** Stream file content into buffers as needed
- **Long lines:** Handle lines longer than the viewport
- **Integration:** Embed in `nova_navigator` as an editor dialog
- **Performance:** Profile and optimize for very large files (>1GB)
- **Syntax highlighting:** Full tree-sitter support
