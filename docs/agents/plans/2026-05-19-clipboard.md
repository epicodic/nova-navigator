# Clipboard (Cut / Copy / Paste) Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use skills:subagent-driven-development (recommended) or skills:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Implement Cut, Copy, and Paste file operations using an internal `PathClipboard` class that also writes URIs to the terminal's OSC 52 clipboard.

**Architecture:** A stateful `PathClipboard` class (in its own module) holds the current clipboard content and owns the OSC 52 write.
One persistent instance lives on `NovaNavigator` (the App).
Action handlers on `MainScreen` call into it; `_update_actions` consults it to enable/disable Paste.

**Tech Stack:** Python 3.12, pytest

**Coding Conventions:** `docs/coding_conventions.md` — read before implementing

**Design spec:** `docs/agents/specs/2026-05-19-clipboard-design.md` — read before implementing

---

## File Map

| Action | File |
|--------|------|
| Create | `src/nova_navigator/clipboard.py` |
| Create | `tests/test_clipboard.py` |
| Modify | `src/nova_navigator/nova_navigator.py` |
| Create | `tests/integration/test_clipboard.py` |

---

## Task 1: `PathClipboard` class

**Files:**
- Create: `src/nova_navigator/clipboard.py`
- Test: `tests/test_clipboard.py`

- [ ] **Step 1: Write the failing unit tests**

```python
# tests/test_clipboard.py
from __future__ import annotations

from unittest.mock import MagicMock

import pytest

from nova_navigator.clipboard import ClipboardOperation, PathClipboard
from nova_navigator.vfs import VPath
from nova_navigator.vfs.filesystems import LocalFilesystem


def _mock_app() -> MagicMock:
    app = MagicMock()
    app.copy_to_clipboard = MagicMock()
    return app


def _vpath(name: str) -> VPath:
    import pathlib
    from nova_navigator.vfs.filesystems.local import LocalFilesystem
    fs = LocalFilesystem.singleton()
    return VPath(pathlib.PurePosixPath(f"/tmp/{name}"), fs)


def test_clipboard_starts_empty() -> None:
    cb = PathClipboard(_mock_app())
    assert cb.empty() is True


def test_get_on_empty_raises() -> None:
    cb = PathClipboard(_mock_app())
    with pytest.raises(ValueError):
        cb.get()


def test_set_stores_paths_and_operation() -> None:
    cb = PathClipboard(_mock_app())
    p = _vpath("file.txt")
    cb.set((p,), ClipboardOperation.COPY)

    assert cb.empty() is False
    paths, op = cb.get()
    assert paths == (p,)
    assert op == ClipboardOperation.COPY


def test_set_writes_uri_to_osc52() -> None:
    app = _mock_app()
    cb = PathClipboard(app)
    p = _vpath("file.txt")
    cb.set((p,), ClipboardOperation.COPY)

    app.copy_to_clipboard.assert_called_once_with(p.uri)


def test_set_multiple_paths_writes_newline_separated_uris() -> None:
    app = _mock_app()
    cb = PathClipboard(app)
    p1 = _vpath("a.txt")
    p2 = _vpath("b.txt")
    cb.set((p1, p2), ClipboardOperation.CUT)

    app.copy_to_clipboard.assert_called_once_with(f"{p1.uri}\n{p2.uri}")


def test_clear_makes_empty() -> None:
    cb = PathClipboard(_mock_app())
    p = _vpath("file.txt")
    cb.set((p,), ClipboardOperation.COPY)
    cb.clear()

    assert cb.empty() is True


def test_clear_then_get_raises() -> None:
    cb = PathClipboard(_mock_app())
    p = _vpath("file.txt")
    cb.set((p,), ClipboardOperation.CUT)
    cb.clear()

    with pytest.raises(ValueError):
        cb.get()
```

- [ ] **Step 2: Run tests to verify they fail**

```
uv run pytest tests/test_clipboard.py -v
```

Expected: `ModuleNotFoundError` or `ImportError` — `clipboard.py` does not exist yet.

- [ ] **Step 3: Implement `clipboard.py`**

```python
# src/nova_navigator/clipboard.py
from __future__ import annotations

from enum import Enum, auto
from typing import Any

from textual.app import App

from nova_navigator.vfs import VPath


class ClipboardOperation(Enum):
    """Operation associated with a path clipboard entry."""

    COPY = auto()
    CUT = auto()


class PathClipboard:
    """Internal path clipboard for Cut / Copy / Paste operations.

    One persistent instance lives on ``NovaNavigator``.
    Calling ``set`` also writes the path URIs to the terminal OSC 52 clipboard
    via Textual's ``App.copy_to_clipboard``.
    """

    def __init__(self, app: App[Any]) -> None:
        self._app = app
        self._paths: tuple[VPath, ...] = ()
        self._operation: ClipboardOperation | None = None

    def set(self, paths: tuple[VPath, ...], operation: ClipboardOperation) -> None:
        """Store *paths* with *operation* and write URIs to the OSC 52 clipboard."""
        self._paths = paths
        self._operation = operation
        self._app.copy_to_clipboard("\n".join(p.uri for p in paths))

    def empty(self) -> bool:
        """Return ``True`` when no paths are stored."""
        return not self._paths

    def get(self) -> tuple[tuple[VPath, ...], ClipboardOperation]:
        """Return ``(paths, operation)``.

        Raises:
            ValueError: If the clipboard is empty.
        """
        if self.empty() or self._operation is None:
            raise ValueError("PathClipboard is empty")
        return self._paths, self._operation

    def clear(self) -> None:
        """Reset the clipboard to the empty state."""
        self._paths = ()
        self._operation = None
```

- [ ] **Step 4: Run tests to verify they pass**

```
uv run pytest tests/test_clipboard.py -v
```

Expected: all 7 tests PASS.

- [ ] **Step 5: Coding-guideline follow-up checklist**

- [ ] `docs/coding_conventions.md` read
- [ ] All symbols use `snake_case` / `UpperCamelCase` correctly
- [ ] Full type annotations on every method
- [ ] No `# noqa` or `# type: ignore`
- [ ] `uv run qa` passes

---

## Task 2: Wire `PathClipboard` into `NovaNavigator` and fix `_update_actions`

**Files:**
- Modify: `src/nova_navigator/nova_navigator.py`

No new tests needed for this task — the existing integration test suite validates that `_update_actions` does not crash, and the clipboard integration tests in Task 4 will verify the Paste enable/disable behaviour.

- [ ] **Step 1: Add import and `_path_clipboard` instance**

In `nova_navigator.py`, add the import near the other `nova_navigator.*` imports (around line 37):

```python
from nova_navigator.clipboard import ClipboardOperation, PathClipboard
```

In `NovaNavigator.__init__` (around line 777), add the instance after `super().__init__()`:

```python
def __init__(self) -> None:
    apply_runtime_patches()
    super().__init__()
    self._path_clipboard = PathClipboard(self)
    self._showing_exception_dialog = False
    register_azure_scheme()
    register_ssh_scheme()
    register_remote_scheme(conf_.remotes)
```

- [ ] **Step 2: Fix `_update_actions` to pass real clipboard state**

`_update_actions` is a method on `MainScreen` (around line 462).
It currently constructs the `other` `AKey` without `is_path_in_clipboard`.
Replace the `AKey(...)` call in the `for` loop with one that includes it:

```python
        for key, action_name in actions:
            a = self._act(action_name)
            a.set_enabled(
                key.matches(
                    AKey(
                        is_empty=path is None,
                        is_directory=path is not None and path.stat.is_directory,
                        is_file=path is not None and not path.stat.is_directory,
                        is_executable=path is not None and path.stat.is_executable and not path.stat.is_directory,
                        is_path_in_clipboard=not self.app._path_clipboard.empty(),
                        is_symlink=path is not None and path.stat.is_symlink,
                    )
                )
            )
```

- [ ] **Step 3: Run QA**

```
uv run qa
```

Expected: zero failures (same baseline as before).

- [ ] **Step 4: Coding-guideline follow-up checklist**

- [ ] Full type annotations on any changed signatures
- [ ] No `# noqa` or `# type: ignore`
- [ ] `uv run qa` passes

---

## Task 3: `_action_copy` and `_action_cut` handlers

**Files:**
- Modify: `src/nova_navigator/nova_navigator.py`
- Create: `tests/integration/test_clipboard.py`

- [ ] **Step 1: Write failing integration tests for Copy and Cut**

```python
# tests/integration/test_clipboard.py
"""Integration tests for Cut / Copy / Paste actions."""

from __future__ import annotations

from unittest.mock import patch

import pytest

from nova_navigator.clipboard import ClipboardOperation
from tests.integration.conftest import (
    AppCtx,
    auto_cancel_dialog,
    auto_confirm_copy_dialog,
    poll_until,
    set_panels,
)

_COPY_DIALOG_PATH = "nova_navigator.filemanager.jobs.CopyMoveFilesDialog"


# ---------------------------------------------------------------------------
# Copy
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
@pytest.mark.integration
async def test_copy_sets_clipboard_with_copy_operation(app_ctx: AppCtx) -> None:
    """_action_copy stores the cursor item in the clipboard as COPY."""
    (app_ctx.src_dir / "file.txt").write_text("hello")
    await set_panels(app_ctx)

    await app_ctx.pilot.app.run_action("copy", app_ctx.screen)
    await app_ctx.pilot.pause()

    cb = app_ctx.pilot.app._path_clipboard
    assert not cb.empty()
    paths, op = cb.get()
    assert op == ClipboardOperation.COPY
    assert paths[0].name == "file.txt"


@pytest.mark.asyncio
@pytest.mark.integration
async def test_copy_on_parent_entry_does_nothing(app_ctx: AppCtx) -> None:
    """_action_copy is a no-op when the cursor is on the '..' entry."""
    from nova_navigator.vfs import VPath

    (app_ctx.src_dir / "file.txt").write_text("")
    app_ctx.screen._left_panel.set_path(VPath(app_ctx.src_dir, app_ctx.fs))
    app_ctx.screen._right_panel.set_path(VPath(app_ctx.dst_dir, app_ctx.fs))
    app_ctx.screen._left_panel.focus()
    await app_ctx.pilot.pause()
    # cursor is on '..' — do NOT press down

    await app_ctx.pilot.app.run_action("copy", app_ctx.screen)
    await app_ctx.pilot.pause()

    assert app_ctx.pilot.app._path_clipboard.empty()


# ---------------------------------------------------------------------------
# Cut
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
@pytest.mark.integration
async def test_cut_sets_clipboard_with_cut_operation(app_ctx: AppCtx) -> None:
    """_action_cut stores the cursor item in the clipboard as CUT."""
    (app_ctx.src_dir / "file.txt").write_text("hello")
    await set_panels(app_ctx)

    await app_ctx.pilot.app.run_action("cut", app_ctx.screen)
    await app_ctx.pilot.pause()

    cb = app_ctx.pilot.app._path_clipboard
    assert not cb.empty()
    paths, op = cb.get()
    assert op == ClipboardOperation.CUT
    assert paths[0].name == "file.txt"


@pytest.mark.asyncio
@pytest.mark.integration
async def test_cut_on_parent_entry_does_nothing(app_ctx: AppCtx) -> None:
    """_action_cut is a no-op when the cursor is on the '..' entry."""
    from nova_navigator.vfs import VPath

    (app_ctx.src_dir / "file.txt").write_text("")
    app_ctx.screen._left_panel.set_path(VPath(app_ctx.src_dir, app_ctx.fs))
    app_ctx.screen._right_panel.set_path(VPath(app_ctx.dst_dir, app_ctx.fs))
    app_ctx.screen._left_panel.focus()
    await app_ctx.pilot.pause()

    await app_ctx.pilot.app.run_action("cut", app_ctx.screen)
    await app_ctx.pilot.pause()

    assert app_ctx.pilot.app._path_clipboard.empty()
```

- [ ] **Step 2: Run tests to verify they fail**

```
uv run pytest tests/integration/test_clipboard.py -v
```

Expected: FAIL — actions not yet registered.

- [ ] **Step 3: Add `_action_copy` and `_action_cut` to `MainScreen`**

Add these two methods to `MainScreen` alongside the existing `_action_rename` and `_action_copy_names` methods (around line 608):

```python
    def _action_copy(self) -> None:
        source = self.active_panel().path_item_under_cursor
        if isinstance(source, UpPath):
            return
        self.app._path_clipboard.set((source,), ClipboardOperation.COPY)
        self._update_actions(source)

    def _action_cut(self) -> None:
        source = self.active_panel().path_item_under_cursor
        if isinstance(source, UpPath):
            return
        self.app._path_clipboard.set((source,), ClipboardOperation.CUT)
        self._update_actions(source)
```

- [ ] **Step 4: Wire `action=` into the menu items**

Locate these two lines in `compose` (around line 143–144):

```python
            mc.action("Copy", shortcut="Ctrl+C", name="copy"),
            mc.action("Cut", shortcut="Ctrl+X", name="cut"),
```

Change them to:

```python
            mc.action("Copy", shortcut="Ctrl+C", action="copy", name="copy"),
            mc.action("Cut", shortcut="Ctrl+X", action="cut", name="cut"),
```

- [ ] **Step 5: Run tests to verify they pass**

```
uv run pytest tests/integration/test_clipboard.py::test_copy_sets_clipboard_with_copy_operation tests/integration/test_clipboard.py::test_copy_on_parent_entry_does_nothing tests/integration/test_clipboard.py::test_cut_sets_clipboard_with_cut_operation tests/integration/test_clipboard.py::test_cut_on_parent_entry_does_nothing -v
```

Expected: all 4 PASS.

- [ ] **Step 6: Coding-guideline follow-up checklist**

- [ ] Full type annotations
- [ ] No `# noqa` or `# type: ignore`
- [ ] `uv run qa` passes

---

## Task 4: `_action_paste` handler + integration tests

**Files:**
- Modify: `src/nova_navigator/nova_navigator.py`
- Modify: `tests/integration/test_clipboard.py`

- [ ] **Step 1: Write failing integration tests for Paste**

Append these tests to `tests/integration/test_clipboard.py`:

```python
# ---------------------------------------------------------------------------
# Paste
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
@pytest.mark.integration
async def test_paste_copy_copies_file_to_active_panel(app_ctx: AppCtx) -> None:
    """Paste after Copy calls copy_or_move_files_job with move=False."""
    (app_ctx.src_dir / "file.txt").write_text("data")
    await set_panels(app_ctx)

    # Copy the file into clipboard
    await app_ctx.pilot.app.run_action("copy", app_ctx.screen)
    await app_ctx.pilot.pause()

    # Switch focus to the right panel (destination) and paste
    app_ctx.screen._right_panel.focus()
    await app_ctx.pilot.pause()

    with patch(_COPY_DIALOG_PATH, return_value=auto_confirm_copy_dialog()):
        await app_ctx.pilot.app.run_action("paste", app_ctx.screen)
        await poll_until(app_ctx.pilot, lambda: (app_ctx.dst_dir / "file.txt").exists())

    assert (app_ctx.dst_dir / "file.txt").exists()
    # Clipboard preserved after copy-paste
    assert not app_ctx.pilot.app._path_clipboard.empty()


@pytest.mark.asyncio
@pytest.mark.integration
async def test_paste_cut_moves_file_and_clears_clipboard(app_ctx: AppCtx) -> None:
    """Paste after Cut calls copy_or_move_files_job with move=True and clears clipboard."""
    (app_ctx.src_dir / "file.txt").write_text("data")
    await set_panels(app_ctx)

    # Cut the file into clipboard
    await app_ctx.pilot.app.run_action("cut", app_ctx.screen)
    await app_ctx.pilot.pause()

    # Switch focus to the right panel (destination) and paste
    app_ctx.screen._right_panel.focus()
    await app_ctx.pilot.pause()

    with patch(_COPY_DIALOG_PATH, return_value=auto_confirm_copy_dialog()):
        await app_ctx.pilot.app.run_action("paste", app_ctx.screen)
        await poll_until(app_ctx.pilot, lambda: (app_ctx.dst_dir / "file.txt").exists())

    assert (app_ctx.dst_dir / "file.txt").exists()
    assert not (app_ctx.src_dir / "file.txt").exists()
    # Clipboard cleared after cut-paste
    assert app_ctx.pilot.app._path_clipboard.empty()


@pytest.mark.asyncio
@pytest.mark.integration
async def test_paste_cancel_preserves_clipboard(app_ctx: AppCtx) -> None:
    """Cancelling the Paste dialog leaves the clipboard unchanged."""
    (app_ctx.src_dir / "file.txt").write_text("data")
    await set_panels(app_ctx)

    await app_ctx.pilot.app.run_action("cut", app_ctx.screen)
    await app_ctx.pilot.pause()

    with patch(_COPY_DIALOG_PATH, return_value=auto_cancel_dialog()):
        await app_ctx.pilot.app.run_action("paste", app_ctx.screen)
        await app_ctx.pilot.pause()

    # Clipboard NOT cleared on cancel
    assert not app_ctx.pilot.app._path_clipboard.empty()
    # File not moved
    assert (app_ctx.src_dir / "file.txt").exists()


@pytest.mark.asyncio
@pytest.mark.integration
async def test_paste_does_nothing_when_clipboard_empty(app_ctx: AppCtx) -> None:
    """_action_paste is a no-op when the clipboard is empty."""
    (app_ctx.src_dir / "file.txt").write_text("data")
    await set_panels(app_ctx)

    dialog_created = False

    def _track(_: object, **__: object) -> object:
        nonlocal dialog_created
        dialog_created = True
        return auto_cancel_dialog()

    with patch(_COPY_DIALOG_PATH, side_effect=_track):
        await app_ctx.pilot.app.run_action("paste", app_ctx.screen)
        await app_ctx.pilot.pause()

    assert not dialog_created
```

- [ ] **Step 2: Run tests to verify they fail**

```
uv run pytest tests/integration/test_clipboard.py::test_paste_copy_copies_file_to_active_panel tests/integration/test_clipboard.py::test_paste_cut_moves_file_and_clears_clipboard tests/integration/test_clipboard.py::test_paste_cancel_preserves_clipboard tests/integration/test_clipboard.py::test_paste_does_nothing_when_clipboard_empty -v
```

Expected: FAIL — `_action_paste` not yet implemented.

- [ ] **Step 3: Implement `_action_paste` on `MainScreen`**

Add this method alongside the other clipboard action handlers:

```python
    @work
    async def _action_paste(self) -> None:
        if self.app._path_clipboard.empty():
            return
        paths, operation = self.app._path_clipboard.get()
        dst = self.active_panel().path
        job = await copy_or_move_files_job(
            src_paths=list(paths),
            dst_path=dst,
            move=operation == ClipboardOperation.CUT,
        )
        if job is not None:
            self.app.job_registry.add_job(job)
            await job.start(self.app.request_callback)
            if operation == ClipboardOperation.CUT:
                self.app._path_clipboard.clear()
                self._update_actions(self.active_panel().path_item_under_cursor)
```

- [ ] **Step 4: Wire `action="paste"` into the menu item**

Locate this line in `compose` (around line 146):

```python
            mc.action("Paste", name="paste"),
```

Change it to:

```python
            mc.action("Paste", action="paste", name="paste"),
```

- [ ] **Step 5: Run the full clipboard test suite**

```
uv run pytest tests/test_clipboard.py tests/integration/test_clipboard.py -v
```

Expected: all 11 tests PASS.

- [ ] **Step 6: Run full QA**

```
uv run qa
```

Expected: zero failures (6 pre-existing Azure errors are acceptable — they require the `azurite` emulator).

- [ ] **Step 7: Coding-guideline follow-up checklist**

- [ ] Full type annotations
- [ ] No `# noqa` or `# type: ignore`
- [ ] `uv run qa` passes with same baseline
