# Clipboard Design

## Overview

Implement Cut, Copy, and Paste operations in Nova Navigator using an internal path clipboard.
The clipboard is internal to the Nova Navigator process only — no cross-app or Dolphin interoperability.
Paste always shows `CopyMoveFilesDialog` (consistent with F5/F6 copy and move).
Cut and Copy operate on the single item under the cursor (no multi-selection).
Cut items are not visually dimmed.

## Constraints

- Internal clipboard only: no `wl-copy`, `xclip`, `pywayland`, or other external tools.
- Path strings are written to the terminal OSC 52 clipboard via Textual's `App.copy_to_clipboard()`.
- This works transparently over SSH because the local terminal handles OSC 52.

---

## Components

### `ClipboardOperation` enum (`nova_navigator/clipboard.py`)

```python
class ClipboardOperation(Enum):
    COPY = auto()
    CUT = auto()
```

### `PathClipboard` class (`nova_navigator/clipboard.py`)

A stateful, mutable class holding the current clipboard content.
One persistent instance lives on `NovaNavigator`; it is never replaced.

```python
class PathClipboard:
    def __init__(self, app: App[Any]) -> None: ...

    def set(self, paths: tuple[VPath, ...], operation: ClipboardOperation) -> None: ...
    def empty(self) -> bool: ...
    def get(self) -> tuple[tuple[VPath, ...], ClipboardOperation]: ...
    def clear(self) -> None: ...
```

**`set(paths, operation)`**
Stores the given paths and operation.
Writes a newline-separated list of `VPath.uri` strings to the terminal clipboard via `self._app.copy_to_clipboard(...)`.

**`empty() -> bool`**
Returns `True` when no paths are stored.

**`get() -> tuple[tuple[VPath, ...], ClipboardOperation]`**
Returns `(paths, operation)`.
Raises `ValueError` if the clipboard is empty.

**`clear()`**
Resets the clipboard to empty (no paths, no operation).

### Persistent instance on `NovaNavigator`

`NovaNavigator` creates exactly one `PathClipboard` at construction time:

```python
self._path_clipboard = PathClipboard(self)
```

---

## Action Handlers

### `_action_copy()` and `_action_cut()`

Both are synchronous (no `@work`).
They skip the `..` (UpPath) entry.

```python
def _action_copy(self) -> None:
    source = self.active_panel().path_item_under_cursor
    if isinstance(source, UpPath):
        return
    self._path_clipboard.set((source,), ClipboardOperation.COPY)
    self._update_actions(source)

def _action_cut(self) -> None:
    source = self.active_panel().path_item_under_cursor
    if isinstance(source, UpPath):
        return
    self._path_clipboard.set((source,), ClipboardOperation.CUT)
    self._update_actions(source)
```

After calling `set()`, `_update_actions` is called immediately so the Paste menu item becomes enabled without waiting for cursor movement.

### `_action_paste()`

Uses `@work`.
Retrieves clipboard content and delegates to `copy_or_move_files_job` (same dialog as F5/F6).
Clears the clipboard only after a successful cut-paste (i.e. the user did not cancel the dialog).
A copy-paste never clears the clipboard; the user may paste the same files multiple times.

```python
@work
async def _action_paste(self) -> None:
    if self._path_clipboard.empty():
        return
    paths, operation = self._path_clipboard.get()
    dst = self.active_panel().path
    job = await copy_or_move_files_job(
        src_paths=list(paths),
        dst_path=dst,
        move=operation == ClipboardOperation.CUT,
    )
    if job is not None:
        self.job_registry.add_job(job)
        await job.start(self.request_callback)
        if operation == ClipboardOperation.CUT:
            self._path_clipboard.clear()
```

---

## `_update_actions` fix

The existing `_update_actions(path)` method builds an `AKey` to match against action enable/disable rules.
It currently always passes `is_path_in_clipboard=None` (no clipboard state).
Fix: pass `not self._path_clipboard.empty()`:

```python
AKey(
    is_empty=path is None,
    is_directory=...,
    is_file=...,
    is_executable=...,
    is_path_in_clipboard=not self._path_clipboard.empty(),
    is_symlink=...,
)
```

This ensures that whenever the cursor moves, the Paste action is correctly enabled or disabled based on actual clipboard state.

---

## Menu wiring

The existing menu items in `nova_navigator.py` already have `name=` set but no `action=`:

```python
mc.action("Copy", shortcut="Ctrl+C", name="copy"),
mc.action("Cut",  shortcut="Ctrl+X", name="cut"),
mc.action("Paste",                   name="paste"),
```

Add `action="copy"`, `action="cut"`, `action="paste"` respectively.

---

## Testing

Tests live under `tests/integration/test_clipboard.py`.
Follow the existing pattern from `tests/integration/test_rename.py`.

Key test cases:
1. **Copy sets clipboard** — after `_action_copy`, `_path_clipboard.empty()` is False and `_path_clipboard.get()` returns the correct path and `ClipboardOperation.COPY`.
2. **Cut sets clipboard** — same as above with `ClipboardOperation.CUT`.
3. **Paste with copy** — calls `copy_or_move_files_job` with `move=False`; clipboard remains non-empty afterwards.
4. **Paste with cut** — calls `copy_or_move_files_job` with `move=True`; clipboard is cleared afterwards.
5. **Cancel paste** — if `copy_or_move_files_job` returns `None` (dialog cancelled), clipboard is not cleared.
6. **UpPath is ignored** — `_action_copy` and `_action_cut` are no-ops when the cursor is on `..`.
7. **Paste disabled when empty** — Paste action is disabled while clipboard is empty; enabled after copy or cut.

Unit tests for `PathClipboard` alone live under `tests/test_clipboard.py`.
Pass a mock app to the constructor to verify `copy_to_clipboard` is called with the correct URI list.
