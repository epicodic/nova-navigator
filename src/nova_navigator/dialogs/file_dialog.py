"""File/directory picker dialog for Nova Navigator."""

from __future__ import annotations

import contextlib
import fnmatch
import pathlib
from dataclasses import dataclass, field
from enum import Enum
from typing import ClassVar

from textual import events
from textual.app import ComposeResult
from textual.binding import Binding, BindingType
from textual.containers import Horizontal
from textual.css.query import NoMatches
from textual.message import Message
from textual.widgets import Input, Label, ListItem, ListView, Static

from nova_widgets import Button, Checkbox, Select

from ..icons import ico_
from .dialog import DefaultButton, Dialog


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


class _FileListItem(ListItem):
    """A list item representing a filesystem entry."""

    def __init__(self, path: pathlib.Path | None) -> None:
        is_dir = path is None or path.is_dir()
        icon = ico_("folder").glyph if is_dir else ico_("file").glyph
        name = ".." if path is None else path.name
        super().__init__(Label(f"{icon} {name}"))
        self.path = path


class _FileListing(ListView):
    """Private file listing widget for FileDialog."""

    BINDINGS: ClassVar[list[BindingType]] = [
        *ListView.BINDINGS,
        Binding("pageup", "page_up", "Page Up", show=False),
        Binding("pagedown", "page_down", "Page Down", show=False),
    ]

    _current_path: pathlib.Path
    _active_filter: FileTypeFilter | None
    _DOUBLE_CLICK: ClassVar[int] = 2

    def __init__(
        self,
        current_path: pathlib.Path,
        active_filter: FileTypeFilter | None = None,
        id: str | None = None,
    ) -> None:
        super().__init__(id=id)
        self._current_path = current_path
        self._active_filter = active_filter
        self._click_is_double: bool = False
        self._last_event_was_click: bool = False
        self._show_hidden: bool = False

    # ── Messages ──────────────────────────────────────────────────────────

    class CursorMoved(Message):
        """Emitted when the cursor moves to a new entry."""

        def __init__(self, path: pathlib.Path | None) -> None:
            super().__init__()
            self.path = path

    class PathNavigated(Message):
        """Emitted when the user navigates into a directory."""

        def __init__(self, path: pathlib.Path) -> None:
            super().__init__()
            self.path = path

    class FileConfirmed(Message):
        """Emitted when the user confirms a file selection."""

        def __init__(self, path: pathlib.Path) -> None:
            super().__init__()
            self.path = path

    # ── Lifecycle ─────────────────────────────────────────────────────────

    def on_mount(self) -> None:
        with contextlib.suppress(PermissionError, OSError):
            self._load_directory()

    # ── Directory loading ─────────────────────────────────────────────────

    def _load_directory(self) -> None:
        """Reload the listing from _current_path.

        Raises:
            PermissionError: if the directory cannot be listed.
            OSError: if the directory cannot be listed.
        """
        dirs: list[pathlib.Path] = []
        files: list[pathlib.Path] = []
        try:
            for entry in sorted(self._current_path.iterdir(), key=lambda p: p.name.lower()):
                if entry.name.startswith(".") and not self._show_hidden:
                    continue
                if entry.is_dir():
                    dirs.append(entry)
                else:
                    if self._active_filter is None or self._active_filter.matches(entry.name):
                        files.append(entry)
        except (PermissionError, OSError) as exc:
            self.notify(f"Cannot open directory: {exc}", severity="error")
            raise
        self.clear()
        self.append(_FileListItem(None))  # ".." entry
        for d in dirs:
            self.append(_FileListItem(d))
        for f in files:
            self.append(_FileListItem(f))
        self.index = 0

    def navigate_to(self, path: pathlib.Path) -> None:
        """Navigate to a new directory path, staying put on error."""
        old_path = self._current_path
        self._current_path = path
        try:
            self._load_directory()
        except (PermissionError, OSError):
            self._current_path = old_path
            raise

    def set_filter(self, active_filter: FileTypeFilter | None) -> None:
        """Change the active file-type filter and reload."""
        self._active_filter = active_filter
        with contextlib.suppress(PermissionError, OSError):
            self._load_directory()

    def set_show_hidden(self, show: bool) -> None:
        """Toggle visibility of hidden (dot) files and reload."""
        self._show_hidden = show
        with contextlib.suppress(PermissionError, OSError):
            self._load_directory()

    # ── ListView event → domain message translation ───────────────────────

    def on_list_view_highlighted(self, event: ListView.Highlighted) -> None:
        event.stop()
        item = event.item
        path = item.path if isinstance(item, _FileListItem) else None
        self.post_message(self.CursorMoved(path))

    def on_list_view_selected(self, event: ListView.Selected) -> None:
        event.stop()
        # Single mouse click → only highlight, no action.  Keyboard Enter and
        # double-click both perform the action.
        if self._last_event_was_click:
            self._last_event_was_click = False
            if not self._click_is_double:
                return
        item = event.item
        if not isinstance(item, _FileListItem):
            return
        path = item.path
        if path is None:
            self.post_message(self.PathNavigated(self._current_path.parent))
        elif path.is_dir():
            self.post_message(self.PathNavigated(path))
        else:
            self.post_message(self.FileConfirmed(path))

    def on_click(self, event: events.Click) -> None:
        """Record click type so on_list_view_selected can distinguish mouse from keyboard."""
        self._click_is_double = event.chain == self._DOUBLE_CLICK
        self._last_event_was_click = True

    # ── Compatibility helpers ──────────────────────────────────────────────

    def action_confirm(self) -> None:
        """Confirm the highlighted item (delegates to ListView's select action)."""
        self.action_select_cursor()

    def action_page_up(self) -> None:
        """Move highlight up by one page."""
        if self.index is None:
            return
        page = max(1, self.scrollable_content_region.height)
        self.index = max(0, self.index - page)

    def action_page_down(self) -> None:
        """Move highlight down by one page."""
        if self.index is None:
            return
        count = len(self._items)
        page = max(1, self.scrollable_content_region.height)
        self.index = min(count - 1, self.index + page)

    @property
    def _cursor(self) -> int:
        """Index of the highlighted item."""
        return self.index if self.index is not None else 0

    @property
    def _items(self) -> list[pathlib.Path | None]:
        """Ordered list of paths in the current listing."""
        return [item.path for item in self.query(_FileListItem)]


class FileDialog(Dialog):
    """Modal file/directory picker dialog.

    Returns the ID of the pressed button (``Decision.OK.name`` or
    ``Decision.CANCEL.name``) via ``run()``.
    After an OK dismissal, ``selected_path`` holds the resolved
    ``pathlib.Path``.
    """

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
            yield Label("Directory:" if self._mode == FileDialogMode.DIR else "File name:")
            yield Input(
                id="filename_input",
                disabled=(self._mode != FileDialogMode.SAVE),
            )
        if self._filters:
            options: list[tuple[str, int]] = [(f.label, i) for i, f in enumerate(self._filters)]
            yield Select(options, id="filter_select", value=0)
        yield Checkbox("Show hidden files", id="show_hidden_cb")

    def on_mount(self) -> None:
        self.query_one("#listing").focus()

    # ── Event handlers ────────────────────────────────────────────────────

    def on__file_listing_cursor_moved(self, event: _FileListing.CursorMoved) -> None:
        inp = self.query_one("#filename_input", Input)
        if self._mode == FileDialogMode.DIR:
            if event.path is not None and event.path.is_dir():
                inp.value = event.path.name
            else:
                inp.value = ""  # ".." or file highlighted in dir mode
        else:
            if event.path is not None and event.path.is_file():
                inp.value = event.path.name
            else:
                inp.value = ""
        self.selected_path = None

    def on__file_listing_path_navigated(self, event: _FileListing.PathNavigated) -> None:
        listing = self.query_one("#listing", _FileListing)
        try:
            listing.navigate_to(event.path)
            self._current_path = event.path
            self.query_one("#path_bar", Static).update(str(self._current_path))
        except (PermissionError, OSError):
            pass  # notify already shown; path bar stays unchanged

    def on__file_listing_file_confirmed(self, event: _FileListing.FileConfirmed) -> None:
        if self._mode == FileDialogMode.DIR:
            return
        self.selected_path = event.path
        super().action_accept_dialog()

    def on_select_changed(self, event: Select.Changed) -> None:
        if event.value is Select.BLANK:
            return
        try:
            listing = self.query_one("#listing", _FileListing)
        except NoMatches:
            return
        idx = event.value
        if not isinstance(idx, int):
            return
        listing.set_filter(self._filters[idx])

    def on_checkbox_changed(self, event: Checkbox.Changed) -> None:
        if event.checkbox.id == "show_hidden_cb":
            self.query_one("#listing", _FileListing).set_show_hidden(event.value)

    # ── Accept / dismiss overrides ────────────────────────────────────────

    def action_accept_dialog(self) -> None:
        """Delegate to listing when it has focus; otherwise validate and dismiss."""
        try:
            listing = self.query_one("#listing", _FileListing)
        except NoMatches:
            return
        if listing.has_focus:
            listing.action_confirm()
            return
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
            hc = listing.highlighted_child
            if isinstance(hc, _FileListItem) and hc.path is not None and hc.path.is_dir():
                candidate = hc.path
            else:
                self.notify("Select a directory", severity="error")
                return False
        else:
            filename = self.query_one("#filename_input", Input).value.strip()
            if not filename:
                self.notify("Enter a filename", severity="error")
                return False
            candidate = self._current_path / filename
            if self._mode == FileDialogMode.OPEN and (not candidate.exists() or not candidate.is_file()):
                self.notify("File not found", severity="error")
                return False
        self.selected_path = candidate
        return True
