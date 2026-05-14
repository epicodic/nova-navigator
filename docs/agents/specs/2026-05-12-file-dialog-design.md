# File Dialog Design

**Date:** 2026-05-12
**Status:** Draft

## Overview

A modal file/directory picker dialog for Nova Navigator.
It covers three modes: file open, file save, and directory select.
The dialog presents a flat scrollable listing of the current directory, a path bar, a filename input, an optional file-type filter, and OK/Cancel buttons.

## Scope

- Single selection only (no multi-select).
- Local filesystem only (`pathlib.Path` throughout — no VFS/VPath involvement).
- Hidden files (dotfiles) are excluded; no toggle.
- File-type filter is optional; caller may omit it.

## Public API

### `FileDialogMode`

```python
class FileDialogMode(Enum):
    OPEN = "open"   # pick an existing file
    SAVE = "save"   # choose a path for writing (file need not exist)
    DIR  = "dir"    # pick an existing directory
```

### `FileTypeFilter`

```python
@dataclass
class FileTypeFilter:
    label: str           # e.g. "Python files"
    patterns: list[str]  # e.g. ["*.py", "*.pyi"] — fnmatch patterns
```

### `FileDialog`

```python
class FileDialog(Dialog):
    selected_path: pathlib.Path | None  # populated after OK is confirmed

    def __init__(
        self,
        mode: FileDialogMode,
        start_path: pathlib.Path,
        title: str,
        filters: list[FileTypeFilter] | None = None,
        id: str | None = None,
    ) -> None: ...

    async def run(self) -> str: ...
```

`FileDialog` extends `Dialog` (which is `ModalScreen[str]`).
`run()` returns the ID of the button the user pressed — `Decision.OK.name` (`"OK"`) or `Decision.CANCEL.name` (`"CANCEL"`).
If OK was pressed and validation passed, `selected_path` holds the resolved `pathlib.Path`.

Typical call:

```python
dialog = FileDialog(
    mode=FileDialogMode.OPEN,
    start_path=pathlib.Path.home(),
    title="Open file",
    filters=[FileTypeFilter("Python files", ["*.py"]), FileTypeFilter("All files", ["*"])],
)
button = await dialog.run()
if button == Decision.OK.name:
    path = dialog.selected_path  # pathlib.Path
```

## Internal Widget: `_FileListing`

`_FileListing(ScrollView)` is a private widget defined in `file_dialog.py`.
It renders a flat scrollable list of entries in the current directory.

### Row format

Each row: `[icon] name`

- One character icon: directory icon for directories, file icon for files.
- Icons are sourced from the existing `nova_navigator.icons` module.
- No size or date columns.

### Entry ordering

1. `..` (parent entry) — always first.
2. Directories — sorted A–Z.
3. Files — sorted A–Z, filtered by the active `FileTypeFilter` pattern via `fnmatch`.

Dotfiles are excluded from both directories and files.

### Keyboard

| Key | Action |
|-----|--------|
| Up / Down | Move cursor |
| Enter (on `..` or directory) | Navigate into directory |
| Enter (on file, OPEN/SAVE mode) | Emit `FileConfirmed` |
| Enter (on file, DIR mode) | No-op (files not selectable) |

### Mouse

- Click on a row: move cursor to that row and update the Input field.
- Double-click on a directory: navigate into it.
- Double-click on a file (OPEN/SAVE): emit `FileConfirmed`.

### Messages

```python
class _FileListing(ScrollView):
    class CursorMoved(Message):
        """Cursor moved to a new entry."""
        path: pathlib.Path  # the item now under cursor

    class PathNavigated(Message):
        """User navigated into a directory."""
        path: pathlib.Path  # new current directory

    class FileConfirmed(Message):
        """User confirmed a file selection (Enter or double-click on file)."""
        path: pathlib.Path
```

### Internal state

```python
_current_path: pathlib.Path         # directory being listed
_items: list[pathlib.Path]          # entries after sorting and filtering
_cursor: int                        # index into _items (0 = "..")
_active_filter: FileTypeFilter | None
```

## `FileDialog` Layout

```
FileDialog (Dialog → ModalScreen[str])
├── #dialog_box (Vertical, centered, rounded border)  ← composed by Dialog base
│   ├── [compose_content() output]
│   │   ├── #path_bar (Static)             — current directory path
│   │   ├── #listing (_FileListing)        — scrollable entry list
│   │   ├── Horizontal #filename_row (visible: OPEN, SAVE only)
│   │   │   ├── Label("File name:")
│   │   │   └── #filename_input (Input)
│   │   └── #filter_select (Select)        — only rendered if filters given
│   └── #button_box (ButtonBox)            ← added automatically by Dialog base
```

### CSS

`FileDialog` defines its own `DEFAULT_CSS` following the same style as `Dialog`: centered modal, rounded border, `width: 60%`, `height: auto` up to a max — with `_FileListing` taking a fixed height (e.g. 15 lines).

### Mode-driven behaviour

| | OPEN | SAVE | DIR |
|---|---|---|---|
| Input shown? | Yes | Yes | No |
| Input editable? | No (read-only) | Yes | — |
| Input updated by cursor? | Yes | Yes (but remains editable) | — |
| Files selectable (Enter)? | Yes | Yes | No |
| OK enabled when? | cursor on a file | input non-empty | cursor on a dir or `..` resolved |

In DIR mode the filename input row is hidden entirely.

### Focus flow

Dialog opens with focus on `#listing`.
Tab cycles: `#listing` → `#filename_input` (OPEN/SAVE) → `#filter_select` (if present) → OK → Cancel.

### Key bindings

| Key | Action |
|-----|--------|
| Escape | Cancel (dismiss with `None`) |
| Enter (at dialog level) | Accept (OK action) |
| Tab / Shift+Tab | Cycle focus |

The `#listing` widget consumes Up/Down/Enter before they bubble to the dialog level.

## OK Action Logic

Override both `action_accept_dialog()` (Enter key) and `on_button_pressed()` (mouse click on OK).

1. Resolve result path:
   - OPEN: `_listing._current_path / filename_input.value`
   - SAVE: `_listing._current_path / filename_input.value`
   - DIR: `_listing._items[_listing._cursor]` (the item under cursor)
2. Validate:
   - OPEN: path must exist and be a file — else `notify("File not found")` and stay open.
   - DIR: path must exist and be a directory — else `notify("Not a directory")` and stay open.
   - SAVE: no existence check.
3. On success: set `self.selected_path = result_path`, then call `super().action_accept_dialog()`.

## Cancel Action

Handled by the `Dialog` base class (`action_dismiss_dialog`). No override needed.

## Error Handling

| Situation | Behaviour |
|-----------|-----------|
| `start_path` is not a directory or doesn't exist | Fall back to `pathlib.Path.home()` silently |
| Listing a directory raises `PermissionError` / `OSError` | `notify("Cannot open directory: <error>")`, stay in current dir |
| OK pressed with invalid selection (OPEN/DIR) | `notify("...")`, dialog stays open |

## File Location

`src/nova_navigator/dialogs/file_dialog.py`

All public names are exported from `src/nova_navigator/dialogs/__init__.py`:
`FileDialog`, `FileDialogMode`, `FileTypeFilter`.

## Testing

**File:** `tests/dialogs/test_file_dialog.py`

Test infrastructure: wrap `FileDialog` in a minimal `App` following the pattern in `tests/dialogs/test_manage_remotes_dialog.py`.
Use `tmp_path` (pytest fixture) to create real temp directories and files on disk.

### Test cases

| # | Mode | Scenario | Expected |
|---|------|----------|----------|
| 1 | OPEN | Navigate into a subdirectory | Path bar updates; listing shows subdirectory contents |
| 2 | OPEN | Navigate to `..` | Path bar updates to parent |
| 3 | OPEN | Cursor on file → OK | Returns `current_dir / filename` |
| 4 | OPEN | Cursor on dir → OK | `notify` shown, dialog stays open |
| 5 | SAVE | Type a name → OK | Returns `current_dir / typed_name` |
| 6 | SAVE | Empty input → OK | OK disabled / no dismiss |
| 7 | DIR | Cursor on dir → OK | Returns directory path |
| 8 | DIR | Cursor on file → Enter | No-op (file not selectable) |
| 9 | Any | Press Escape | Returns `None` |
| 10 | Any | File-type filter active | Non-matching files hidden |
| 11 | Any | Navigate into permission-denied dir | `notify` shown, stays in current dir |
| 12 | Any | `start_path` doesn't exist | Falls back to home directory |

All tests use `@pytest.mark.asyncio` and `async with app.run_test() as pilot`.
