# File Dialog Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use skills:subagent-driven-development (recommended) or skills:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Implement a modal file/directory picker dialog (`FileDialog`) with OPEN, SAVE, and DIR modes, backed by a private `_FileListing` scroll widget, with file-type filters and a filename input field.

**Architecture:** `FileDialog(Dialog)` defined in `src/nova_navigator/dialogs/file_dialog.py`. `Dialog` is `ModalScreen[str]` and returns the pressed button ID (`Decision.OK.name` / `Decision.CANCEL.name`). After OK, `dialog.selected_path` holds the resolved `pathlib.Path`. A private `_FileListing(ScrollView)` widget lives in the same file and handles directory rendering, keyboard navigation, and mouse interaction. All local filesystem I/O uses `pathlib.Path` — no VFS/VPath involvement.

**Tech Stack:** Python 3.12, Textual, pytest, pytest-asyncio

**Coding Conventions:** `docs/coding_conventions.md` — read before implementing

**Design doc:** `docs/agents/specs/2026-05-12-file-dialog-design.md` — read before implementing

---

## File Map

| Action | Path | Responsibility |
|--------|------|----------------|
| Create | `src/nova_navigator/dialogs/file_dialog.py` | `FileDialogMode`, `FileTypeFilter`, `_FileListing`, `FileDialog` |
| Modify | `src/nova_navigator/dialogs/__init__.py` | Export `FileDialog`, `FileDialogMode`, `FileTypeFilter` |
| Create | `tests/dialogs/test_file_dialog.py` | All tests for the dialog |

---

## Task 1: `FileDialogMode` enum and `FileTypeFilter` dataclass

**Files:**
- Create: `src/nova_navigator/dialogs/file_dialog.py`
- Create: `tests/dialogs/test_file_dialog.py`

- [ ] **Step 1: Write the failing test**

```python
# tests/dialogs/test_file_dialog.py
import fnmatch
import pathlib
import pytest

from nova_navigator.dialogs.file_dialog import FileDialogMode, FileTypeFilter


def test_file_dialog_mode_values() -> None:
    assert FileDialogMode.OPEN.value == "open"
    assert FileDialogMode.SAVE.value == "save"
    assert FileDialogMode.DIR.value == "dir"


def test_file_type_filter_pattern_matching() -> None:
    f = FileTypeFilter(label="Python files", patterns=["*.py", "*.pyi"])
    assert fnmatch.fnmatch("foo.py", f.patterns[0])
    assert not fnmatch.fnmatch("foo.txt", f.patterns[0])
```

- [ ] **Step 2: Run test to verify it fails**

```
uv run pytest tests/dialogs/test_file_dialog.py::test_file_dialog_mode_values tests/dialogs/test_file_dialog.py::test_file_type_filter_pattern_matching -v
```

Expected: FAIL — module does not exist yet

- [ ] **Step 3: Implement `FileDialogMode` and `FileTypeFilter`**

Create `src/nova_navigator/dialogs/file_dialog.py` with:

```python
from __future__ import annotations

import fnmatch
import pathlib
from dataclasses import dataclass, field
from enum import Enum
from typing import ClassVar

from textual.app import ComposeResult
from textual.binding import Binding, BindingType
from textual.message import Message
from textual.reactive import var
from textual.screen import ModalScreen
from textual.scroll_view import ScrollView
from textual.strip import Strip
from textual.widgets import Input, Label, Select, Static
from textual.containers import Horizontal, Vertical

from nova_widgets import Button, ButtonBox

from ..decision import Decision


class FileDialogMode(Enum):
    """Mode for FileDialog."""

    OPEN = "open"
    SAVE = "save"
    DIR = "dir"


@dataclass
class FileTypeFilter:
    """A file-type filter for FileDialog."""

    label: str
    patterns: list[str] = field(default_factory=list)

    def matches(self, name: str) -> bool:
        """Return True if name matches any pattern."""
        return any(fnmatch.fnmatch(name, p) for p in self.patterns)
```

- [ ] **Step 4: Run test to verify it passes**

```
uv run pytest tests/dialogs/test_file_dialog.py::test_file_dialog_mode_values tests/dialogs/test_file_dialog.py::test_file_type_filter_pattern_matching -v
```

Expected: PASS

- [ ] **Step 5: Coding-guideline follow-up checklist**

- [ ] Conventions file read: `docs/coding_conventions.md`
- [ ] Naming conventions match project rules for all new/edited symbols
- [ ] Full type annotations on all functions/methods
- [ ] Run `uv run ruff check src/nova_navigator/dialogs/file_dialog.py` — zero errors
- [ ] Run `uv run ty check src/nova_navigator/dialogs/file_dialog.py` — zero errors

---

## Task 2: `_FileListing` widget — rendering

**Files:**
- Modify: `src/nova_navigator/dialogs/file_dialog.py`
- Modify: `tests/dialogs/test_file_dialog.py`

The widget renders a flat list of entries: `..` first, then directories (A–Z), then files (A–Z, filtered).
Each row: two-character icon prefix (NerdFont glyph + space) then the entry name.
Icons: use `ico_("folder")` for directories and `ico_("file")` for files (same as `directory_browser.py`).
Dotfiles are excluded.

- [ ] **Step 1: Write failing test for listing entries**

```python
# tests/dialogs/test_file_dialog.py — add after existing tests
from textual.app import App, ComposeResult as CR
from nova_navigator.dialogs.file_dialog import _FileListing, FileTypeFilter


class _ListingApp(App[None]):
    def __init__(self, listing: _FileListing) -> None:
        super().__init__()
        self._listing = listing

    def compose(self) -> CR:
        yield self._listing


@pytest.mark.asyncio
async def test_listing_shows_parent_entry(tmp_path: pathlib.Path) -> None:
    listing = _FileListing(current_path=tmp_path)
    app = _ListingApp(listing)
    async with app.run_test() as pilot:
        await pilot.pause()
        # ".." must always be item 0
        assert listing._items[0] is None  # None sentinel represents ".."


@pytest.mark.asyncio
async def test_listing_dirs_before_files(tmp_path: pathlib.Path) -> None:
    (tmp_path / "aaa").mkdir()
    (tmp_path / "bbb.txt").write_text("x")
    listing = _FileListing(current_path=tmp_path)
    app = _ListingApp(listing)
    async with app.run_test() as pilot:
        await pilot.pause()
        # items: [None, dir_path, file_path]
        assert listing._items[1].is_dir()
        assert listing._items[2].is_file()


@pytest.mark.asyncio
async def test_listing_excludes_dotfiles(tmp_path: pathlib.Path) -> None:
    (tmp_path / ".hidden").write_text("x")
    (tmp_path / "visible.txt").write_text("y")
    listing = _FileListing(current_path=tmp_path)
    app = _ListingApp(listing)
    async with app.run_test() as pilot:
        await pilot.pause()
        names = [p.name for p in listing._items if p is not None]
        assert ".hidden" not in names
        assert "visible.txt" in names
```

- [ ] **Step 2: Run to verify it fails**

```
uv run pytest tests/dialogs/test_file_dialog.py::test_listing_shows_parent_entry tests/dialogs/test_file_dialog.py::test_listing_dirs_before_files tests/dialogs/test_file_dialog.py::test_listing_excludes_dotfiles -v
```

Expected: FAIL — `_FileListing` not yet implemented

- [ ] **Step 3: Implement `_FileListing` with rendering**

Add this class to `src/nova_navigator/dialogs/file_dialog.py` (after the `FileTypeFilter` class):

```python
from rich.segment import Segment
from rich.style import Style
from textual.geometry import Size

from ..icons import ico_


class _FileListing(ScrollView):
    """Private scrollable file listing widget for FileDialog."""

    COMPONENT_CLASSES: ClassVar[set[str]] = {"cursor", "item"}

    # None sentinel = ".." (parent entry)
    _items: list[pathlib.Path | None]
    _cursor: int
    _current_path: pathlib.Path
    _active_filter: FileTypeFilter | None

    def __init__(
        self,
        current_path: pathlib.Path,
        active_filter: FileTypeFilter | None = None,
        id: str | None = None,
    ) -> None:
        super().__init__(id=id)
        self._current_path = current_path
        self._active_filter = active_filter
        self._items = []
        self._cursor = 0

    # ── Messages ──────────────────────────────────────────────────────────

    class CursorMoved(Message):
        """Emitted when cursor moves to a new entry."""

        def __init__(self, path: pathlib.Path | None) -> None:
            super().__init__()
            self.path = path  # None means ".."

    class PathNavigated(Message):
        """Emitted when user navigates into a directory."""

        def __init__(self, path: pathlib.Path) -> None:
            super().__init__()
            self.path = path

    class FileConfirmed(Message):
        """Emitted when user confirms a file (Enter / double-click on file)."""

        def __init__(self, path: pathlib.Path) -> None:
            super().__init__()
            self.path = path

    # ── Lifecycle ─────────────────────────────────────────────────────────

    def on_mount(self) -> None:
        self._load_directory()

    # ── Directory loading ─────────────────────────────────────────────────

    def _load_directory(self) -> None:
        """Load entries from _current_path into _items and refresh."""
        dirs: list[pathlib.Path] = []
        files: list[pathlib.Path] = []
        try:
            for entry in sorted(self._current_path.iterdir(), key=lambda p: p.name.lower()):
                if entry.name.startswith("."):
                    continue
                if entry.is_dir():
                    dirs.append(entry)
                else:
                    if self._active_filter is None or self._active_filter.matches(entry.name):
                        files.append(entry)
        except (PermissionError, OSError) as exc:
            self.notify(f"Cannot open directory: {exc}", severity="error")
            return
        self._items = [None, *dirs, *files]  # None = ".."
        self._cursor = 0
        self.virtual_size = Size(self.size.width, len(self._items))
        self.refresh()

    def navigate_to(self, path: pathlib.Path) -> None:
        """Navigate listing to a new directory path."""
        self._current_path = path
        self._load_directory()

    def set_filter(self, active_filter: FileTypeFilter | None) -> None:
        """Change the active file-type filter and reload."""
        self._active_filter = active_filter
        self._load_directory()

    # ── Rendering ─────────────────────────────────────────────────────────

    def render_line(self, y: int) -> Strip:
        scroll_y = self.scroll_offset.y
        row = scroll_y + y
        if row >= len(self._items):
            return Strip.blank(self.size.width)

        item = self._items[row]
        is_cursor = row == self._cursor

        if item is None:
            icon_str = ico_("folder").glyph + " "
            name = ".."
        elif item.is_dir():
            icon_str = ico_("folder").glyph + " "
            name = item.name
        else:
            icon_str = ico_("file").glyph + " "
            name = item.name

        text = icon_str + name
        if len(text) < self.size.width:
            text = text + " " * (self.size.width - len(text))
        else:
            text = text[: self.size.width]

        base_style = self.get_component_rich_style("cursor" if is_cursor else "item")
        segments = [Segment(text, base_style)]
        return Strip(segments)

    # ── Scroll size ───────────────────────────────────────────────────────

    def get_content_height(self, container: Size, viewport: Size, width: int) -> int:
        return len(self._items)

    def get_content_width(self, container: Size, viewport: Size) -> int:
        return container.width
```

- [ ] **Step 4: Run tests to verify they pass**

```
uv run pytest tests/dialogs/test_file_dialog.py::test_listing_shows_parent_entry tests/dialogs/test_file_dialog.py::test_listing_dirs_before_files tests/dialogs/test_file_dialog.py::test_listing_excludes_dotfiles -v
```

Expected: PASS

- [ ] **Step 5: Coding-guideline follow-up checklist**

- [ ] Conventions file read: `docs/coding_conventions.md`
- [ ] All new methods/classes fully type-annotated
- [ ] Run `uv run ruff check src/nova_navigator/dialogs/file_dialog.py` — zero errors
- [ ] Run `uv run ty check src/nova_navigator/dialogs/file_dialog.py` — zero errors

---

## Task 3: `_FileListing` — keyboard navigation and messages

**Files:**
- Modify: `src/nova_navigator/dialogs/file_dialog.py`
- Modify: `tests/dialogs/test_file_dialog.py`

- [ ] **Step 1: Write failing tests**

```python
# tests/dialogs/test_file_dialog.py — add after existing tests

@pytest.mark.asyncio
async def test_cursor_down_moves_cursor(tmp_path: pathlib.Path) -> None:
    (tmp_path / "alpha").mkdir()
    listing = _FileListing(current_path=tmp_path)
    app = _ListingApp(listing)
    async with app.run_test() as pilot:
        await pilot.pause()
        assert listing._cursor == 0
        await pilot.press("down")
        await pilot.pause()
        assert listing._cursor == 1


@pytest.mark.asyncio
async def test_enter_on_dotdot_navigates_to_parent(tmp_path: pathlib.Path) -> None:
    subdir = tmp_path / "sub"
    subdir.mkdir()
    listing = _FileListing(current_path=subdir)
    app = _ListingApp(listing)
    navigated: list[pathlib.Path] = []

    def on_navigated(event: _FileListing.PathNavigated) -> None:
        navigated.append(event.path)

    app.on(_FileListing.PathNavigated, on_navigated)

    async with app.run_test() as pilot:
        await pilot.pause()
        # cursor is at 0 ("..")
        await pilot.press("enter")
        await pilot.pause()
        assert navigated and navigated[0] == tmp_path


@pytest.mark.asyncio
async def test_enter_on_file_emits_file_confirmed(tmp_path: pathlib.Path) -> None:
    f = tmp_path / "report.txt"
    f.write_text("hello")
    listing = _FileListing(current_path=tmp_path)
    app = _ListingApp(listing)
    confirmed: list[pathlib.Path] = []

    def on_confirmed(event: _FileListing.FileConfirmed) -> None:
        confirmed.append(event.path)

    app.on(_FileListing.FileConfirmed, on_confirmed)

    async with app.run_test() as pilot:
        await pilot.pause()
        await pilot.press("down")   # move to the file (index 1; index 0 is "..")
        await pilot.pause()
        await pilot.press("enter")
        await pilot.pause()
        assert confirmed and confirmed[0] == f
```

- [ ] **Step 2: Run to verify they fail**

```
uv run pytest tests/dialogs/test_file_dialog.py::test_cursor_down_moves_cursor tests/dialogs/test_file_dialog.py::test_enter_on_dotdot_navigates_to_parent tests/dialogs/test_file_dialog.py::test_enter_on_file_emits_file_confirmed -v
```

Expected: FAIL

- [ ] **Step 3: Add keyboard handling to `_FileListing`**

Add these methods inside the `_FileListing` class:

```python
    DEFAULT_CSS = """
    _FileListing {
        .cursor {
            background: $accent;
            color: $text;
        }
        .item {
            background: $surface;
            color: $text;
        }
    }
    """

    BINDINGS: ClassVar[list[BindingType]] = [
        Binding("up", "cursor_up", "Up", show=False),
        Binding("down", "cursor_down", "Down", show=False),
        Binding("enter", "confirm", "Select", show=False),
    ]

    def action_cursor_up(self) -> None:
        if self._cursor > 0:
            self._cursor -= 1
            self._scroll_to_cursor()
            self.refresh()
            self.post_message(self.CursorMoved(self._items[self._cursor]))

    def action_cursor_down(self) -> None:
        if self._cursor < len(self._items) - 1:
            self._cursor += 1
            self._scroll_to_cursor()
            self.refresh()
            self.post_message(self.CursorMoved(self._items[self._cursor]))

    def action_confirm(self) -> None:
        if not self._items:
            return
        item = self._items[self._cursor]
        if item is None:
            # ".." — navigate to parent
            parent = self._current_path.parent
            self.post_message(self.PathNavigated(parent))
        elif item.is_dir():
            self.post_message(self.PathNavigated(item))
        else:
            self.post_message(self.FileConfirmed(item))

    def _scroll_to_cursor(self) -> None:
        """Ensure the cursor row is visible."""
        self.scroll_to(y=self._cursor, animate=False)
```

- [ ] **Step 4: Run to verify they pass**

```
uv run pytest tests/dialogs/test_file_dialog.py::test_cursor_down_moves_cursor tests/dialogs/test_file_dialog.py::test_enter_on_dotdot_navigates_to_parent tests/dialogs/test_file_dialog.py::test_enter_on_file_emits_file_confirmed -v
```

Expected: PASS

- [ ] **Step 5: Coding-guideline follow-up checklist**

- [ ] All new methods fully type-annotated
- [ ] Run `uv run ruff check src/nova_navigator/dialogs/file_dialog.py` — zero errors
- [ ] Run `uv run ty check src/nova_navigator/dialogs/file_dialog.py` — zero errors

---

## Task 4: `FileDialog` — layout and composition

**Files:**
- Modify: `src/nova_navigator/dialogs/file_dialog.py`
- Modify: `tests/dialogs/test_file_dialog.py`

The `FileDialog` composes the path bar, `_FileListing`, filename input, optional filter `Select`, and OK/Cancel buttons.
It extends `ModalScreen[pathlib.Path | None]` directly (not `Dialog`, since `Dialog` is typed `ModalScreen[str]`).
Buttons are created manually using `Button` from `nova_widgets`.

- [ ] **Step 1: Write failing test for dialog composition**

`FileDialog` is a `ModalScreen` (pushed via `push_screen`/`push_screen_wait`), not a plain widget.
Use `app.push_screen` to mount it in the test app:

```python
# tests/dialogs/test_file_dialog.py — add after existing tests
from textual.widgets import Input, Select, Static

from nova_navigator.dialogs.file_dialog import FileDialog, FileDialogMode, FileTypeFilter
from nova_navigator.decision import Decision


class _DialogApp(App[str]):
    """Minimal app that immediately pushes a FileDialog."""

    def __init__(self, dialog: FileDialog) -> None:
        super().__init__()
        self._dialog = dialog

    def compose(self) -> CR:
        return iter([])

    async def on_mount(self) -> None:
        await self.push_screen(self._dialog)


@pytest.mark.asyncio
async def test_dialog_path_bar_shows_start_path(tmp_path: pathlib.Path) -> None:
    dialog = FileDialog(mode=FileDialogMode.OPEN, start_path=tmp_path, title="Test")
    app = _DialogApp(dialog)
    async with app.run_test() as pilot:
        await pilot.pause()
        path_bar = app.screen.query_one("#path_bar", Static)
        assert str(tmp_path) in str(path_bar.renderable)


@pytest.mark.asyncio
async def test_dialog_open_mode_input_is_readonly(tmp_path: pathlib.Path) -> None:
    dialog = FileDialog(mode=FileDialogMode.OPEN, start_path=tmp_path, title="Test")
    app = _DialogApp(dialog)
    async with app.run_test() as pilot:
        await pilot.pause()
        inp = app.screen.query_one("#filename_input", Input)
        assert inp.disabled


@pytest.mark.asyncio
async def test_dialog_dir_mode_hides_input(tmp_path: pathlib.Path) -> None:
    dialog = FileDialog(mode=FileDialogMode.DIR, start_path=tmp_path, title="Test")
    app = _DialogApp(dialog)
    async with app.run_test() as pilot:
        await pilot.pause()
        row = app.screen.query_one("#filename_row")
        assert not row.display


@pytest.mark.asyncio
async def test_dialog_filter_select_shown_when_filters_given(tmp_path: pathlib.Path) -> None:
    filters = [FileTypeFilter("Python", ["*.py"]), FileTypeFilter("All", ["*"])]
    dialog = FileDialog(mode=FileDialogMode.OPEN, start_path=tmp_path, title="Test", filters=filters)
    app = _DialogApp(dialog)
    async with app.run_test() as pilot:
        await pilot.pause()
        sel = app.screen.query_one("#filter_select", Select)
        assert sel is not None


@pytest.mark.asyncio
async def test_dialog_no_filter_select_without_filters(tmp_path: pathlib.Path) -> None:
    from textual.css.query import NoMatches
    dialog = FileDialog(mode=FileDialogMode.OPEN, start_path=tmp_path, title="Test")
    app = _DialogApp(dialog)
    async with app.run_test() as pilot:
        await pilot.pause()
        with pytest.raises(NoMatches):
            app.screen.query_one("#filter_select", Select)
```

- [ ] **Step 2: Run to verify they fail**

```
uv run pytest tests/dialogs/test_file_dialog.py::test_dialog_path_bar_shows_start_path tests/dialogs/test_file_dialog.py::test_dialog_open_mode_input_is_readonly tests/dialogs/test_file_dialog.py::test_dialog_dir_mode_hides_input tests/dialogs/test_file_dialog.py::test_dialog_filter_select_shown_when_filters_given tests/dialogs/test_file_dialog.py::test_dialog_no_filter_select_without_filters -v
```

Expected: FAIL

- [ ] **Step 3: Implement `FileDialog` class**

Append the `FileDialog` class to `src/nova_navigator/dialogs/file_dialog.py`.

Note: `Dialog` is `ModalScreen[str]`; it dismisses with the pressed button's string ID.
`FileDialog` stores the resolved path in `self.selected_path` before calling the base dismiss.
Imports needed at the top of the file (add alongside existing imports):
```python
from .dialog import ComposeResult, DefaultButton, Dialog
```

```python
class FileDialog(Dialog):
    """Modal file/directory picker dialog."""

    DEFAULT_CSS = """
    FileDialog {
        _FileListing {
            height: 15;
            border: inner $surface;
        }

        #filename_row {
            height: auto;
            margin-top: 1;
        }

        #filename_row Label {
            width: 12;
            padding-top: 1;
        }

        #filter_select {
            margin-top: 1;
        }
    }
    """

    selected_path: pathlib.Path | None

    def __init__(
        self,
        mode: FileDialogMode,
        start_path: pathlib.Path,
        title: str,
        filters: list[FileTypeFilter] | None = None,
        id: str | None = None,
    ) -> None:
        super().__init__(title=title, id=id, buttons=[DefaultButton.OK, DefaultButton.CANCEL])
        self._mode = mode
        self._filters = filters or []
        self.selected_path = None
        # Validate / fall back start_path
        if not start_path.exists() or not start_path.is_dir():
            start_path = pathlib.Path.home()
        self._current_path = start_path

    # ── Composition ───────────────────────────────────────────────────────

    def compose_content(self) -> ComposeResult:
        yield Static(str(self._current_path), id="path_bar")
        yield _FileListing(
            current_path=self._current_path,
            active_filter=self._filters[0] if self._filters else None,
            id="listing",
        )
        with Horizontal(id="filename_row"):
            yield Label("File name:")
            yield Input(
                id="filename_input",
                disabled=(self._mode != FileDialogMode.SAVE),
            )
        if self._filters:
            options = [(f.label, i) for i, f in enumerate(self._filters)]
            yield Select(options, id="filter_select", value=0)

    def on_mount(self) -> None:
        if self._mode == FileDialogMode.DIR:
            self.query_one("#filename_row").display = False
        self.query_one("#listing").focus()

    # ── Event handlers ────────────────────────────────────────────────────

    def on__file_listing_cursor_moved(self, event: _FileListing.CursorMoved) -> None:
        if event.path is not None and not event.path.is_dir():
            inp = self.query_one("#filename_input", Input)
            inp.value = event.path.name

    def on__file_listing_path_navigated(self, event: _FileListing.PathNavigated) -> None:
        listing = self.query_one("#listing", _FileListing)
        try:
            listing.navigate_to(event.path)
            self._current_path = event.path
            self.query_one("#path_bar", Static).update(str(self._current_path))
        except (PermissionError, OSError):
            pass  # notify already shown by _FileListing; path bar unchanged

    def on__file_listing_file_confirmed(self, event: _FileListing.FileConfirmed) -> None:
        if self._mode != FileDialogMode.DIR:
            self.selected_path = event.path
            super().action_accept_dialog()

    def on_select_changed(self, event: Select.Changed) -> None:
        idx = int(event.value)
        self.query_one("#listing", _FileListing).set_filter(self._filters[idx])

    # ── Accept / dismiss overrides ────────────────────────────────────────

    def action_accept_dialog(self) -> None:
        """Validate selection then delegate dismiss to Dialog base."""
        if self._validate_and_store():
            super().action_accept_dialog()

    def on_button_pressed(self, event: Button.Pressed) -> None:
        """Intercept OK to validate; let Cancel fall through to Dialog base."""
        if self._button_accept and event.button.id == self._button_accept.id:
            event.stop()
            if self._validate_and_store():
                self.dismiss(self._button_accept.id)
        else:
            super().on_button_pressed(event)

    # ── Validation ────────────────────────────────────────────────────────

    def _validate_and_store(self) -> bool:
        """Resolve and validate the selected path. Stores result in selected_path.

        Returns True if valid and selected_path was set.
        """
        listing = self.query_one("#listing", _FileListing)
        if self._mode == FileDialogMode.DIR:
            item = listing._items[listing._cursor] if listing._items else None
            candidate = self._current_path.parent if item is None else (
                item if item.is_dir() else self._current_path
            )
            if not candidate.is_dir():
                self.notify("Not a directory", severity="error")
                return False
        else:
            filename = self.query_one("#filename_input", Input).value.strip()
            if not filename:
                self.notify("Enter a filename", severity="error")
                return False
            candidate = self._current_path / filename
            if self._mode == FileDialogMode.OPEN:
                if not candidate.exists() or not candidate.is_file():
                    self.notify("File not found", severity="error")
                    return False
        self.selected_path = candidate
        return True
```

- [ ] **Step 4: Run tests to verify they pass**

```
uv run pytest tests/dialogs/test_file_dialog.py::test_dialog_path_bar_shows_start_path tests/dialogs/test_file_dialog.py::test_dialog_open_mode_input_is_readonly tests/dialogs/test_file_dialog.py::test_dialog_dir_mode_hides_input tests/dialogs/test_file_dialog.py::test_dialog_filter_select_shown_when_filters_given tests/dialogs/test_file_dialog.py::test_dialog_no_filter_select_without_filters -v
```

Expected: PASS

- [ ] **Step 5: Coding-guideline follow-up checklist**

- [ ] All methods fully type-annotated
- [ ] Run `uv run ruff check src/nova_navigator/dialogs/file_dialog.py` — zero errors
- [ ] Run `uv run ty check src/nova_navigator/dialogs/file_dialog.py` — zero errors

---

## Task 5: `FileDialog` — OPEN/SAVE/DIR acceptance behavior

**Files:**
- Modify: `tests/dialogs/test_file_dialog.py`

These tests exercise the full round-trip: navigate, select, OK, get result.

- [ ] **Step 1: Write the tests**

```python
# tests/dialogs/test_file_dialog.py — add after existing tests

from nova_navigator.decision import Decision


class _CapturingApp(App[str]):
    """App that pushes a FileDialog and captures the dismissed button ID."""

    def __init__(self, dialog: FileDialog) -> None:
        super().__init__()
        self._dialog = dialog
        self.result: str | None = None

    def compose(self) -> CR:
        return iter([])

    async def on_mount(self) -> None:
        self.result = await self.push_screen_wait(self._dialog)
        self.exit()


@pytest.mark.asyncio
async def test_open_mode_ok_returns_file_path(tmp_path: pathlib.Path) -> None:
    target = tmp_path / "report.txt"
    target.write_text("data")
    dialog = FileDialog(mode=FileDialogMode.OPEN, start_path=tmp_path, title="Open")
    app = _CapturingApp(dialog)
    async with app.run_test() as pilot:
        await pilot.pause()
        # index 0 = "..", index 1 = the file
        await pilot.press("down")
        await pilot.pause()
        await pilot.press("enter")  # triggers action_accept_dialog
        await pilot.pause(delay=0.2)
    assert app.result == Decision.OK.name
    assert dialog.selected_path == target


@pytest.mark.asyncio
async def test_open_mode_ok_on_nonexistent_file_keeps_dialog_open(tmp_path: pathlib.Path) -> None:
    dialog = FileDialog(mode=FileDialogMode.OPEN, start_path=tmp_path, title="Open")
    app = _DialogApp(dialog)
    async with app.run_test() as pilot:
        await pilot.pause()
        # Directly set the input value to a nonexistent filename to trigger validation
        inp = app.screen.query_one("#filename_input", Input)
        inp.disabled = False
        inp.value = "does_not_exist.txt"
        await pilot.pause()
        await pilot.press("enter")
        await pilot.pause()
        # Dialog must still be open
        assert app.screen.query_one("#filename_input") is not None


@pytest.mark.asyncio
async def test_save_mode_ok_returns_composed_path(tmp_path: pathlib.Path) -> None:
    dialog = FileDialog(mode=FileDialogMode.SAVE, start_path=tmp_path, title="Save")
    app = _CapturingApp(dialog)
    async with app.run_test() as pilot:
        await pilot.pause()
        inp = app.query("#filename_input").first(Input)
        await pilot.click(inp)
        await pilot.press(*list("newfile.txt"))
        await pilot.pause()
        await pilot.press("enter")
        await pilot.pause(delay=0.2)
    assert app.result == Decision.OK.name
    assert dialog.selected_path == tmp_path / "newfile.txt"


@pytest.mark.asyncio
async def test_dir_mode_ok_returns_directory(tmp_path: pathlib.Path) -> None:
    subdir = tmp_path / "mydir"
    subdir.mkdir()
    dialog = FileDialog(mode=FileDialogMode.DIR, start_path=tmp_path, title="Dir")
    app = _CapturingApp(dialog)
    async with app.run_test() as pilot:
        await pilot.pause()
        # index 0 = "..", index 1 = subdir
        await pilot.press("down")
        await pilot.pause()
        await pilot.press("enter")
        await pilot.pause(delay=0.2)
    assert app.result == Decision.OK.name
    assert dialog.selected_path == subdir


@pytest.mark.asyncio
async def test_cancel_returns_cancel_button_id(tmp_path: pathlib.Path) -> None:
    dialog = FileDialog(mode=FileDialogMode.OPEN, start_path=tmp_path, title="Open")
    app = _CapturingApp(dialog)
    async with app.run_test() as pilot:
        await pilot.pause()
        await pilot.press("escape")
        await pilot.pause(delay=0.2)
    assert app.result == Decision.CANCEL.name
    assert dialog.selected_path is None


def test_start_path_fallback_to_home_when_invalid() -> None:
    bad_path = pathlib.Path("/nonexistent/path/xyz123")
    dialog = FileDialog(mode=FileDialogMode.OPEN, start_path=bad_path, title="Test")
    assert dialog._current_path == pathlib.Path.home()


@pytest.mark.asyncio
async def test_permission_denied_directory_stays_put(tmp_path: pathlib.Path) -> None:
    restricted = tmp_path / "restricted"
    restricted.mkdir(mode=0o000)
    try:
        listing = _FileListing(current_path=tmp_path)
        app = _ListingApp(listing)
        async with app.run_test() as pilot:
            await pilot.pause()
            listing.navigate_to(restricted)
            await pilot.pause()
            # Must remain at the original directory, not the restricted one
            assert listing._current_path == tmp_path
    finally:
        restricted.chmod(0o755)


@pytest.mark.asyncio
async def test_filter_hides_non_matching_files(tmp_path: pathlib.Path) -> None:
    (tmp_path / "script.py").write_text("")
    (tmp_path / "readme.md").write_text("")
    py_filter = FileTypeFilter("Python", ["*.py"])
    listing = _FileListing(current_path=tmp_path, active_filter=py_filter)
    app = _ListingApp(listing)
    async with app.run_test() as pilot:
        await pilot.pause()
        names = [p.name for p in listing._items if p is not None]
        assert "script.py" in names
        assert "readme.md" not in names
```

- [ ] **Step 2: Run tests**

```
uv run pytest tests/dialogs/test_file_dialog.py -v
```

Fix any failures — adjust implementation details as needed (e.g. event handler naming for Textual's message routing, dismiss mechanics).

- [ ] **Step 3: Coding-guideline follow-up checklist**

- [ ] All tests use `@pytest.mark.asyncio`
- [ ] No tests use `time.sleep` or blocking calls
- [ ] Run `uv run ruff check tests/dialogs/test_file_dialog.py` — zero errors

---

## Task 6: Export from `dialogs/__init__.py`

**Files:**
- Modify: `src/nova_navigator/dialogs/__init__.py`

- [ ] **Step 1: Add exports**

Open `src/nova_navigator/dialogs/__init__.py` and add:

```python
from .file_dialog import FileDialog, FileDialogMode, FileTypeFilter
```

And add to `__all__`:

```python
"FileDialog",
"FileDialogMode",
"FileTypeFilter",
```

- [ ] **Step 2: Verify import works**

```
uv run python -c "from nova_navigator.dialogs import FileDialog, FileDialogMode, FileTypeFilter; print('OK')"
```

Expected: `OK`

- [ ] **Step 3: Run full test suite**

```
uv run qa
```

Expected: zero failures, zero lint errors, zero type errors.

---

## Task 7: Permission-denied fix — `navigate_to` stays on error

**Files:**
- Modify: `src/nova_navigator/dialogs/file_dialog.py`

The `navigate_to` method must restore `_current_path` to the old value on error.
The `FileDialog` handler already wraps `navigate_to` in a try/except (included in Task 4 Step 3).
This task ensures `_load_directory` re-raises so `navigate_to` can restore state.

- [ ] **Step 1: Update `_load_directory` to re-raise on error**

Replace the `except` block in `_load_directory`:

```python
        except (PermissionError, OSError) as exc:
            self.notify(f"Cannot open directory: {exc}", severity="error")
            raise  # let navigate_to restore _current_path
```

- [ ] **Step 2: Update `navigate_to` to restore path on error**

Replace the existing `navigate_to` method:

```python
    def navigate_to(self, path: pathlib.Path) -> None:
        """Navigate listing to a new directory path, staying put on error."""
        old_path = self._current_path
        self._current_path = path
        try:
            self._load_directory()
        except (PermissionError, OSError):
            self._current_path = old_path
            raise
```

- [ ] **Step 3: Run the permission-denied test to verify**

```
uv run pytest tests/dialogs/test_file_dialog.py::test_permission_denied_directory_stays_put -v
```

Expected: PASS

- [ ] **Step 4: Run full test suite**

```
uv run qa
```

Expected: zero failures.

---

## Final Verification

- [ ] Run `uv run qa` — zero lint, type, and test failures
- [ ] Confirm `FileDialog`, `FileDialogMode`, `FileTypeFilter` are importable from `nova_navigator.dialogs`
- [ ] All 12 test scenarios from the design doc are covered
