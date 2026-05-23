from __future__ import annotations

import asyncio
import threading
import time
from collections.abc import Callable
from dataclasses import dataclass
from datetime import datetime
from enum import Enum
from typing import Any, ClassVar, Self

from rich.color import Color
from rich.segment import Segment
from rich.style import Style
from textual import events, on, work
from textual.app import ComposeResult
from textual.binding import Binding, BindingType
from textual.containers import Horizontal
from textual.geometry import Region, Size, Spacing, clamp
from textual.message import Message
from textual.reactive import Reactive, var
from textual.scroll_view import ScrollView
from textual.strip import Strip
from textual.widgets import Button, Input, Static
from textual.widgets.data_table import ColumnKey

from nova_widgets import unicode
from nova_widgets.custom_border import CustomBorderMixin

from ..config import conf_
from ..format_utils import format_size
from ..icons import ico_
from ..vfs import VPath
from ..vfs.types import Stat
from .popup_widget import PopupWidget


class UpPath(VPath):
    def __init__(self) -> None:
        pass

    @property
    def compact_path_str(self) -> str:
        return ".."

    @property
    def name(self) -> str:
        return ".."

    @property
    def parent(self) -> Self:
        return self

    @property
    def stat(self) -> Stat:
        return Stat(is_directory=True)

    def __str__(self) -> str:
        return ".."

    def __eq__(self, other: object) -> bool:
        return isinstance(other, UpPath)

    def __hash__(self) -> int:
        return hash("UpPath")


UP_PATH = UpPath()

MOUSE_BUTTON_LEFT = 1
MOUSE_BUTTON_RIGHT = 3
MOUSE_DOUBLE_CLICK = 2


def _ljust(s: str, width: int) -> str:
    if unicode.wcswidth(s) > width:
        return s[: width - 1] + "…"
    return unicode.ljust(s, width)


def _rjust(s: str, width: int) -> str:
    if unicode.wcswidth(s) > width:
        return "…" + s[1 - width :]
    return unicode.rjust(s, width)


def column_formatter_icon(path: VPath) -> str:
    """Convert path to display icon."""
    stat = path.stat

    if stat.is_directory:
        icon = ico_("folder")
    else:
        icon = conf_.filetypes.get_icon_for_filename(path.name, default=ico_("file"))

    if not stat.is_directory and not path.guess_mimetype() and stat.is_executable:
        icon = ico_("executable")

    if stat.is_symlink:
        icon_str = icon.glyph + "~"
    else:
        icon_str = icon.glyph + " "

    if stat.is_symlink and stat.is_broken_symlink:
        icon_str = ico_("broken link").glyph + "!"
    return icon_str


def column_formatter_name(path: VPath) -> str:
    """Convert path to display name."""
    return f"{path.name}"


def column_formatter_size(path: VPath) -> str:
    """Convert size in bytes to human-readable format."""
    stat = path.stat
    if stat.size < 0:
        return ""

    if stat.is_directory:
        return "-"

    return format_size(stat.size)


def column_formatter_modified(path: VPath) -> str:
    """Convert timestamp to human-readable date."""
    stat = path.stat
    if stat.modified < 0:
        return ""

    dt = datetime.fromtimestamp(stat.modified)

    # format as follows:
    # - if date is today, show "today HH:MM"
    # - if date is this year, show "mon day HH:MM"
    # - otherwise, show full date "mon day year"
    now = datetime.now()
    if dt.date() == now.date():
        return dt.strftime("Today  %H:%M")

    if dt.year == now.year:
        return dt.strftime("%b %d %H:%M")

    return dt.strftime("%b %d  %Y")


def column_sorter_name(path: VPath) -> tuple[int, str]:
    if isinstance(path, UpPath):
        return 0, path.name

    stat = path.stat
    if stat.is_directory and stat.is_hidden:
        return 1, path.name

    if stat.is_directory and not stat.is_hidden:
        return 2, path.name

    if stat.is_hidden:
        return 3, path.name

    return 4, path.name


def column_sorter_size(path: VPath) -> tuple[int, int]:
    if isinstance(path, UpPath):
        return 0, 0

    stat = path.stat
    if stat.is_directory:
        return 1, stat.size
    return 2, stat.size


def column_sorter_modified(path: VPath) -> tuple[int, float]:
    if isinstance(path, UpPath):
        return 0, 0.0
    stat = path.stat
    return 1, stat.modified


@dataclass
class Column:
    title: str
    width: int
    formatter: Callable[[VPath], str]
    sorter: Callable[[VPath], Any]


class FilterWidget(PopupWidget, can_focus=True):
    """Widget for finding files in the directory browser."""

    BINDINGS: ClassVar[list[BindingType]] = [
        Binding("escape", "dismiss", "Dismiss", show=False),
    ]
    CLOSE_ACTION = PopupWidget.CloseAction.KEEP
    CLOSE_ON_BLUR = False

    DEFAULT_CSS = """
    FilterWidget {
        width: 40;
        height: 1;
        border: none;

        Static {
            width: 10;
            padding-right: 1;
            padding-left: 1;
        }

        Input {
            width: 27;
            padding-left: 1;
        }

        Button {
            max-width: 3;
            text-align: left;
        }
    }
    """

    input: Input

    def __init__(self, title: str, position: tuple[int, int], browser: DirectoryBrowser) -> None:
        super().__init__(title, position)
        self.browser = browser
        self.input = Input(placeholder="Type to filter ...", compact=True)
        self.display = False

    def compose(self) -> ComposeResult:
        yield Horizontal(
            Static("Filter:"),
            self.input,
            Button(ico_("xmark").glyph, id="close-button", compact=True),
        )

    def on_focus(self, event: events.Focus) -> None:
        self.show()
        self.input.focus()

    def on_button_pressed(self, event: Button.Pressed) -> None:
        if event.button.id == "close-button":
            self.action_dismiss()

    def on_input_submitted(self, event: Input.Submitted) -> None:
        self.action_accept()

    def action_accept(self) -> None:
        if not self.value:
            self.action_dismiss()
            return
        self.browser.focus()

    def action_dismiss(self) -> None:
        self.input.value = ""
        self.hide()
        self.browser.focus()

    @property
    def value(self) -> str:
        return self.input.value if self.input is not None else ""

    @on(Input.Changed)
    def _on_input_changed(self, event: Input.Changed) -> None:
        self.browser.on_filter_widget_input_changed(event)


class GoToPathWidget(PopupWidget, can_focus=True):
    """Widget for navigating to an arbitrary path or URI."""

    BINDINGS: ClassVar[list[BindingType]] = [
        Binding("escape", "dismiss", "Dismiss", show=False),
    ]
    CLOSE_ACTION = PopupWidget.CloseAction.KEEP
    CLOSE_ON_BLUR = False

    DEFAULT_CSS = """
    GoToPathWidget {
        width: 60;
        height: 1;
        border: none;

        Static {
            width: 10;
            padding-right: 1;
            padding-left: 1;
        }

        Input {
            width: 48;
            padding-left: 1;
        }
    }
    """

    class Submitted(Message):
        """Posted when the user submits a path or URI to navigate to."""

        def __init__(self, path: str, browser: DirectoryBrowser) -> None:
            super().__init__()
            self.path = path
            self.browser = browser

    input: Input

    def __init__(self, position: tuple[int, int], browser: DirectoryBrowser) -> None:
        super().__init__("Go to Path", position)
        self.browser = browser
        self.input = Input(placeholder="Path or URI (e.g. ssh://user@host/path)…", compact=True)
        self.display = False

    def compose(self) -> ComposeResult:
        yield Horizontal(
            self.input,
        )

    def on_focus(self, event: events.Focus) -> None:
        self.show()
        self.input.focus()

    def on_input_submitted(self, event: Input.Submitted) -> None:
        self.action_accept()

    def action_accept(self) -> None:
        value = self.input.value.strip()
        if not value:
            self.action_dismiss()
            return
        self.post_message(GoToPathWidget.Submitted(value, self.browser))
        self.input.value = ""
        self.hide()
        self.browser.focus()

    def action_dismiss(self) -> None:
        self.input.value = ""
        self.hide()
        self.browser.focus()


class DirectoryBrowser(CustomBorderMixin, ScrollView):
    DEFAULT_CSS = """
    DirectoryBrowser {
        border: round grey;
    }
    """

    BINDINGS: ClassVar[list[BindingType]] = [
        Binding("enter", "select_cursor", "Select", show=False),
        Binding("up", "cursor_up", "Cursor up", show=False),
        Binding("down", "cursor_down", "Cursor down", show=False),
        Binding("right", "cursor_right", "Cursor right", show=False),
        Binding("left", "cursor_left", "Cursor left", show=False),
        Binding("pageup", "page_up", "Page up", show=False),
        Binding("pagedown", "page_down", "Page down", show=False),
        Binding("home", "scroll_top", "Top", show=False),
        Binding("end", "scroll_bottom", "Bottom", show=False),
        Binding("insert", "insert_select", "Select", show=False),
        Binding("ctrl+a", "select_all", "Select All", show=False),
        Binding("ctrl+f", "filter", "Filter", show=True),
    ]

    COMPONENT_CLASSES: ClassVar[set[str]] = {
        "cursor",
        "highlight-directory",
        "highlight-hidden",
        "highlight-executable",
        "highlight-symlink",
        "highlight-broken-symlink",
        "highlight-up",
        "highlight-selected",
    }

    # messages
    class PathSelected(Message):
        """Posted when an item is selected in the directory browser."""

        def __init__(self, browser: DirectoryBrowser, path: VPath) -> None:
            self.browser = browser
            """The directory browser."""
            self.path = path
            super().__init__()

    class ItemChanged(Message):
        def __init__(self, browser: DirectoryBrowser, path: VPath | None) -> None:
            self.browser = browser
            """The directory browser."""
            self.path = path
            super().__init__()

    class ContextMenu(Message):
        """Posted when the context menu is requested in the directory browser."""

        def __init__(self, browser: DirectoryBrowser, path: VPath | None) -> None:
            self.browser = browser
            """The directory browser."""
            self.path = path
            super().__init__()

    class Focus(Message):
        """Posted when the directory browser receives focus."""

        def __init__(self, browser: DirectoryBrowser) -> None:
            self.browser = browser
            super().__init__()

    class PathChanged(Message):
        """Posted when the current directory changes."""

        def __init__(self, browser: DirectoryBrowser, path: VPath) -> None:
            self.browser = browser
            self.path = path
            super().__init__()

    class LoadFailed(Message):
        """Posted when a directory listing fails to start."""

        def __init__(self, browser: DirectoryBrowser, path: VPath, error: OSError) -> None:
            self.browser = browser
            self.path = path
            self.error = error
            super().__init__()

    class LoadComplete(Message):
        """Posted when a directory listing finishes loading successfully."""

        def __init__(self, browser: DirectoryBrowser, path: VPath) -> None:
            self.browser = browser
            self.path = path
            super().__init__()

    # private classes

    @dataclass(frozen=True)
    class _PendingNavigation:
        path: VPath
        record_history: bool

    @dataclass
    class _CursorHint:
        """Saved cursor position used to restore the cursor after a directory reload.

        name is the filename to search for.
        row is the row index to fall back to if the named item is no longer present
        (e.g. it was deleted), or None when no positional fallback is needed.
        """

        name: str
        row: int | None = None

    # members

    HEADER_HEIGHT: ClassVar[int] = 1

    _path: VPath
    _history: list[VPath]
    _history_index: int

    _all_items: list[VPath]
    _shown_items: list[VPath]
    _selected_items: set[VPath]

    _load_cancel: threading.Event | None
    _loading: bool
    _pending: _PendingNavigation | None
    _item_colors: dict[str, str]

    _empty_strip: Strip = Strip([])
    _columns: list[Column]

    @property
    def items(self) -> list[VPath]:
        """The full list of items in the current directory (excludes the '..' entry)."""
        return list(self._all_items)

    _max_line_width: int
    _filter_widget: FilterWidget
    _goto_widget: GoToPathWidget

    show_hidden_files: Reactive[bool] = Reactive(default=False, repaint=False, always_update=False)
    cursor_row: Reactive[int] = Reactive(default=0, repaint=False, always_update=True)
    sort_column = var(default=0)
    sort_ascending = var(default=True)

    def __init__(
        self,
        path: VPath,
        name: str | None = None,
        id: str | None = None,
    ) -> None:
        super().__init__(
            name=name,
            id=id,
        )

        self._columns = [
            Column(
                title="",  # Icon
                width=3,
                formatter=column_formatter_icon,
                sorter=column_sorter_name,
            ),
            Column(
                title="Name",
                width=0,
                formatter=column_formatter_name,
                sorter=column_sorter_name,
            ),
            Column(
                title="Size",
                width=10,
                formatter=column_formatter_size,
                sorter=column_sorter_size,
            ),
            Column(
                title="Modified",
                width=12,
                formatter=column_formatter_modified,
                sorter=column_sorter_modified,
            ),
        ]
        self._path = UpPath()
        self._history = []
        self._history_index = -1
        self._load_cancel = None
        self._loading = False
        self._pending = None
        self._item_colors = {}
        # Saved cursor position before a reload; None when no hint is active.
        self._cursor_hint: DirectoryBrowser._CursorHint | None = None
        self._all_items = []
        self._shown_items = []
        self._selected_items = set()
        self._max_line_width = 0
        self._filter_widget = FilterWidget(
            title="Filter",
            position=(10, 0),
            browser=self,
        )
        self._goto_widget = GoToPathWidget(
            position=(0, 0),
            browser=self,
        )
        self.set_path(path)

    def on_mount(self) -> None:
        super().on_mount()
        self.screen.mount(self._filter_widget)
        self.screen.mount(self._goto_widget)
        self._load_directory()

    def _on_focus(self, event: events.Focus) -> None:
        self.post_message(DirectoryBrowser.Focus(self))

    @property
    def path(self) -> VPath:
        return self._path

    @property
    def path_item_under_cursor(self) -> VPath:
        return self._shown_items[self.cursor_row]

    @property
    def selected_path_items(self) -> list[VPath]:
        if not self._selected_items:
            if isinstance(self.path_item_under_cursor, UpPath):
                return []
            return [self.path_item_under_cursor]
        return list(self._selected_items)

    @property
    def can_go_back(self) -> bool:
        return self._history_index > 0

    @property
    def can_go_forward(self) -> bool:
        return self._history_index < len(self._history) - 1

    def go_back(self) -> None:
        if self.can_go_back:
            self._history_index -= 1
            self.set_path(self._history[self._history_index], record_history=False)

    def go_forward(self) -> None:
        if self.can_go_forward:
            self._history_index += 1
            self.set_path(self._history[self._history_index], record_history=False)

    def set_path(self, path: VPath, *, record_history: bool = True) -> None:
        if path == self._path and self._pending is None:
            return

        if path == self._path.parent:
            # navigating up: place cursor on the directory we came from
            self._cursor_hint = self._CursorHint(name=self._path.name)
        else:
            self._cursor_hint = None

        self._pending = self._PendingNavigation(path, record_history)

        path.filesystem.refresh(path)

        if self.is_attached:
            self._load_directory()

    def _commit_pending(self, target: VPath) -> None:
        """Commit a pending navigation once the directory listing has started successfully."""
        if self._pending is None or self._pending.path != target:
            return  # Superseded by a newer navigation

        record_history = self._pending.record_history
        self._pending = None

        if record_history:
            self._history = self._history[: self._history_index + 1]
            self._history.append(target)
            self._history_index = len(self._history) - 1

        self._path = target
        self.border_title = target.uri
        self.post_message(DirectoryBrowser.PathChanged(self, target))

        if self.is_attached:
            self._start_watch()

    # Data management

    def reload(self) -> None:
        """Reload the current directory from the filesystem.

        Saves the name and row index of the item currently under the cursor so
        that the cursor can be restored after the reload. If the item has been
        deleted, the row hint positions the cursor on the next item instead.
        """
        if not self._loading and self.cursor_row < len(self._shown_items):
            self._cursor_hint = self._CursorHint(
                name=self._shown_items[self.cursor_row].name,
                row=self.cursor_row,
            )
        self._pending = None
        self.border_title = self._path.uri
        self._path.filesystem.refresh()
        self.update(self.WhatChanged.ALL)

    def set_item_colors(self, item_colors: dict[str, str] | None) -> None:
        """Set per-filename foreground color overrides.

        Args:
            item_colors: Maps filename to a Rich color string, or None to clear.
        """
        self._item_colors = item_colors or {}
        self.refresh()

    class WhatChanged(int, Enum):
        ALL = 0
        SORTING = 1
        FILTERING = 2

    def _apply_sort_and_filter(
        self,
        *,
        cursor_hint: _CursorHint | None = None,
    ) -> None:
        """Re-sort, re-filter, and restore cursor position from _all_items.

        When cursor_hint is given the cursor is placed on the first item whose
        name matches hint.name. If no such item is found (e.g. it was deleted),
        hint.row is used as a positional fallback and is clamped to the new list
        bounds. When cursor_hint is None the cursor is restored to the same item
        object it was on before the operation.
        """
        if cursor_hint is not None:
            old_item_under_cursor = None
        else:
            old_item_under_cursor = (
                self._shown_items[self.cursor_row] if self.cursor_row < len(self._shown_items) else None
            )
        old_selected_items = self._selected_items

        column_sorter = self._columns[self.sort_column].sorter

        def sorter(item: VPath) -> Any:
            order, value = column_sorter(item)
            return (order if self.sort_ascending else -order), value

        self._all_items.sort(key=sorter, reverse=not self.sort_ascending)

        self._shown_items = [UP_PATH] if self._path.parent != self._path else []
        if self.show_hidden_files and len(self._filter_widget.value) == 0:
            self._shown_items += self._all_items
        else:

            def filter_func(item: VPath) -> bool:
                if not self.show_hidden_files and item.stat.is_hidden:
                    return False
                if len(self._filter_widget.value) == 0:
                    return True
                return self._filter_widget.value.lower() in item.name.lower()

            self._shown_items += [item for item in self._all_items if filter_func(item)]

        # restore cursor position
        self.cursor_row = 0
        if old_item_under_cursor is not None:
            for index, item in enumerate(self._shown_items):
                if item == old_item_under_cursor:
                    self.cursor_row = index
                    break
        elif cursor_hint is not None:
            found = False
            for index, item in enumerate(self._shown_items):
                if item.name == cursor_hint.name:
                    self.cursor_row = index
                    found = True
                    break
            if not found and cursor_hint.row is not None:
                self.cursor_row = cursor_hint.row
        self.cursor_row = self.validate_cursor_row(self.cursor_row)

        # restore selected items
        self._selected_items = set()
        for item in old_selected_items:
            if item in self._shown_items:
                self._selected_items.add(item)

        self._max_line_width = 0
        self._update_virtual_size()

    def _append_items(self, new_items: list[VPath]) -> None:
        """Append new items to _shown_items with filtering only (no sort)."""
        filter_text = self._filter_widget.value.lower()
        for item in new_items:
            if not self.show_hidden_files and item.stat.is_hidden:
                continue
            if filter_text and filter_text not in item.name.lower():
                continue
            self._shown_items.append(item)
        self._update_virtual_size()

    def update(self, what_changed: WhatChanged) -> None:
        if what_changed <= self.WhatChanged.ALL:
            if self.is_attached:
                self._load_directory()
            return
        self._apply_sort_and_filter()
        self.refresh()

    _BATCH_SIZE: ClassVar[int] = 200
    _INCREMENTAL_UPDATE_INTERVAL: ClassVar[float] = 0.3

    def _handle_load_failure(
        self,
        target: VPath,
        exc: OSError,
        saved_all_items: list[VPath],
        saved_shown_items: list[VPath],
    ) -> None:
        """Restore state and post LoadFailed after a navigation error before any items arrived."""
        self._pending = None
        self._all_items = saved_all_items
        self._shown_items = saved_shown_items
        self._cursor_hint = None
        self._loading = False
        self.refresh()
        self.refresh_border()
        self.post_message(DirectoryBrowser.LoadFailed(self, target, exc))

    @work(exclusive=True, group="load")
    async def _load_directory(self) -> None:
        """Load directory contents incrementally into _all_items."""
        pending = self._pending
        target = pending.path if pending is not None else self._path

        last_update = time.monotonic()
        cancel = threading.Event()
        self._load_cancel = cancel

        saved_all_items = self._all_items
        saved_shown_items = self._shown_items

        self._all_items = []
        self._shown_items = []
        self._loading = True
        self.refresh()
        self.refresh_border()

        committed = False
        try:
            batch: list[VPath] = []
            async for vpath in target.filesystem.iterdir(target, cancel=cancel):
                if pending is not None and not committed:
                    self._commit_pending(target)
                    committed = True
                batch.append(vpath)
                if len(batch) >= self._BATCH_SIZE:
                    self._all_items.extend(batch)
                    now = time.monotonic()
                    if now - last_update >= self._INCREMENTAL_UPDATE_INTERVAL:
                        self._append_items(batch)
                        self.refresh()
                        last_update = now
                    batch.clear()
                    await asyncio.sleep(0)

            if pending is not None and not committed:
                # Empty directory — still a successful navigation.
                self._commit_pending(target)
                committed = True

            self._all_items.extend(batch)
        except OSError as exc:
            if pending is not None and not committed:
                # Navigation failed before any item arrived — restore previous state.
                self._handle_load_failure(target, exc, saved_all_items, saved_shown_items)
                return
            else:
                self._all_items = []
        finally:
            cancel.set()

        self._loading = False
        self.refresh_border()
        self._apply_sort_and_filter(cursor_hint=self._cursor_hint)
        self._cursor_hint = None
        self.refresh()
        self.post_message(DirectoryBrowser.LoadComplete(self, self._path))

    @work(exclusive=True, group="watch")
    async def _start_watch(self) -> None:
        try:
            async with self._path.filesystem.watch(self._path, self._on_directory_changed):
                await asyncio.Event().wait()  # run until cancelled by Textual
        except OSError:
            self.log.warning("Watch ended unexpectedly for %s", self._path)
            # Directory still works; user can press F5 to refresh manually.

    async def _on_directory_changed(self, path: VPath) -> None:
        """Called by the filesystem watcher when the directory contents change."""
        if not self._loading and self.cursor_row < len(self._shown_items):
            self._cursor_hint = self._CursorHint(
                name=self._shown_items[self.cursor_row].name,
                row=self.cursor_row,
            )
        self.update(self.WhatChanged.ALL)

    # Rendering

    def _highlight_style(self, path: VPath) -> list[Style]:
        styles = []

        color, background_color = conf_.filetypes.get_colors_for_filename(path.name)
        if color:
            styles.append(Style(color=color))

        if background_color:
            styles.append(Style(bgcolor=background_color))

        stat = path.stat
        hightlight_type_map: dict[str, bool] = {
            "highlight-directory": stat.is_directory,
            "highlight-hidden": stat.is_hidden,
            "highlight-executable": stat.is_executable and not stat.is_directory,
            "highlight-symlink": stat.is_symlink,
            "highlight-broken-symlink": stat.is_symlink and stat.is_broken_symlink,
            "highlight-selected": path in self._selected_items,
        }

        for style, value in hightlight_type_map.items():
            if value:
                styles.append(self.get_component_rich_style(style, partial=True))

        item_color = self._item_colors.get(path.name)
        if item_color:  # item color overrides all other colors, including highlights
            styles.append(Style(color=item_color))

        return styles

    def render_border_bottom_right(self) -> Strip:
        n = len(self._selected_items)
        if n == 0:
            return Strip.blank(0)
        total = sum(p.stat.size for p in self._selected_items if not p.stat.is_directory)
        text = f" {n} file{'s' if n != 1 else ''}, {format_size(total)} "
        return Strip([Segment(text, self._border_rich_style())])

    def render_border_bottom_left(self) -> Strip:
        if self._loading:
            return Strip([Segment(" Loading... ", self._border_rich_style())])
        if not self._shown_items:
            return Strip.blank(0)
        item = self._shown_items[self.cursor_row]
        if isinstance(item, UpPath) or not item.stat.is_symlink:
            return Strip.blank(0)
        try:
            target = item.filesystem.readlink(item)
        except OSError:
            target = "?"
        text = f" → {target} "
        return Strip([Segment(text, self._border_rich_style())])

    def render_line(self, y: int) -> Strip:
        scroll_x, scroll_y = self.scroll_offset

        row = y + scroll_y - self.HEADER_HEIGHT  # -1 for header
        if row >= len(self._shown_items):
            return self._empty_strip

        other_width = (
            self._columns[0].width
            + self._columns[2].width
            + 2  # padding between size and modified
            + self._columns[3].width
            + 1  # padding between modified and scrollbar
            + 1  # scrollbar
            + 1  # ?
        )

        remaining = self.size.width - other_width
        remaining = max(remaining, 1)

        style = self.rich_style
        force_bgcolor: Color | None = None
        if row == self.cursor_row:
            style += self.get_component_rich_style("cursor", partial=True)
            force_bgcolor = style.bgcolor

        if y == 0:
            style += Style.from_meta({"row": -1})
            row_texts = (
                self._columns[0].title,
                self._columns[1].title,
                self._columns[2].title,
                self._columns[3].title,
            )
        else:
            style += Style.from_meta({"row": row})
            item = self._shown_items[row]
            row_texts = (
                self._columns[0].formatter(item),
                self._columns[1].formatter(item)[scroll_x:],
                self._columns[2].formatter(item),
                self._columns[3].formatter(item),
            )

            styles = self._highlight_style(item)
            if styles:
                style += Style.combine(styles)

        if force_bgcolor:
            style += Style(bgcolor=force_bgcolor)

        total_width = len(row_texts[0]) + other_width
        if total_width > self._max_line_width:
            self._max_line_width = total_width
            self._update_virtual_size()

        first_column_width = remaining

        icon_segment = Segment(
            unicode.ljust(row_texts[0], self._columns[0].width), style=style + Style.from_meta({"column": 1})
        )
        name_segment = Segment(
            _ljust(row_texts[1], first_column_width) + " ", style=style + Style.from_meta({"column": 1})
        )
        size_segment = Segment(
            _rjust(row_texts[2], self._columns[2].width) + "  ", style=style + Style.from_meta({"column": 2})
        )
        modified_segment = Segment(
            _rjust(row_texts[3], self._columns[3].width) + " ", style=style + Style.from_meta({"column": 3})
        )

        return Strip([icon_segment, name_segment, size_segment, modified_segment])

    def _refresh_region(self, region: Region) -> Self:
        """Refresh a region of the DataTable, if it's visible within the window.

        This method will translate the region to account for scrolling.

        Returns:
            The `DataTable` instance.
        """
        if not self.window_region.overlaps(region):
            return self
        region = region.translate(-self.scroll_offset)
        self.refresh(region)
        return self

    def is_valid_row_index(self, row_index: int) -> bool:
        """Return a boolean indicating whether the row_index is within table bounds.

        Args:
            row_index: The row index to check.

        Returns:
            True if the row index is within the bounds of the table.
        """
        return 0 <= row_index < len(self._shown_items)

    def _get_row_region(self, row_index: int) -> Region:
        """Get the region of the row at the given index."""
        if not self.is_valid_row_index(row_index):
            return Region(0, 0, 0, 0)

        return Region(0, row_index + self.HEADER_HEIGHT, self.size.width, 1)

    def refresh_row(self, row_index: int) -> Self:
        """Refresh the row at the given index.

        Args:
            row_index: The index of the row to refresh.

        Returns:
            The `DataTable` instance.
        """
        if not self.is_valid_row_index(row_index):
            return self

        region = self._get_row_region(row_index)
        self._refresh_region(region)
        return self

    def _is_symlink_at_row(self, row: int) -> bool:
        if not self._shown_items or not (0 <= row < len(self._shown_items)):
            return False
        item = self._shown_items[row]
        return not isinstance(item, UpPath) and item.stat.is_symlink

    def _scroll_cursor_into_view(self, animate: bool = False) -> None:
        """When the cursor is at a boundary, this method handles scrolling to ensure it remains visible."""
        fixed_offset = Spacing(self.HEADER_HEIGHT, 0, 0, 0)

        _, y, width, height = self._get_row_region(self.cursor_row)
        region = Region(int(self.scroll_x), y, width, height)

        self.scroll_to_region(region, animate=animate, spacing=fixed_offset, force=True)

    def _update_virtual_size(self) -> None:
        """Update the virtual size of the DataTable based on the number of rows and columns."""
        self.virtual_size = Size(self._max_line_width, len(self._shown_items) + self.HEADER_HEIGHT)

    # Events

    def validate_cursor_row(self, row: int) -> int:
        return clamp(row, 0, len(self._shown_items) - 1)

    def watch_cursor_row(self, old_row: int, new_row: int) -> None:
        if old_row != new_row:
            self.refresh_row(old_row)
            self.refresh_row(new_row)
            self.refresh_border()
            self._scroll_cursor_into_view()
            item = self._shown_items[new_row] if 0 <= new_row < len(self._shown_items) else None
            self.post_message(DirectoryBrowser.ItemChanged(self, item))

    def watch_sort_column(self, _old: ColumnKey, _new: ColumnKey) -> None:
        self.update(self.WhatChanged.SORTING)

    def watch_sort_ascending(self, _old: bool, _new: bool) -> None:
        self.update(self.WhatChanged.SORTING)

    def action_cursor_up(self) -> None:
        self.cursor_row = self.cursor_row - 1

    def action_cursor_down(self) -> None:
        self.cursor_row = self.cursor_row + 1

    def action_page_up(self) -> None:
        height = self.scrollable_content_region.height - self.HEADER_HEIGHT
        self.scroll_relative(y=-height, animate=False, force=True)
        self.cursor_row = self.cursor_row - height

    def action_page_down(self) -> None:
        height = self.scrollable_content_region.height - self.HEADER_HEIGHT
        self.scroll_relative(y=height, animate=False, force=True)
        self.cursor_row = self.cursor_row + height

    def action_scroll_top(self) -> None:
        self.cursor_row = 0

    def action_scroll_bottom(self) -> None:
        self.cursor_row = len(self._shown_items) - 1

    # user has selected an item with the insert key
    def action_insert_select(self) -> None:
        if isinstance(self.path_item_under_cursor, UpPath):
            # can not select up path
            self.action_cursor_down()
            return

        self.action_toggle_selection_under_cursor()
        self.action_cursor_down()

    def action_toggle_selection_under_cursor(self) -> None:
        cursor_item = self.path_item_under_cursor
        if cursor_item in self._selected_items:
            self._selected_items.remove(cursor_item)
        else:
            self._selected_items.add(cursor_item)

        self.refresh_row(self.cursor_row)
        self.refresh_border()

    def action_select_all(self) -> None:
        self._selected_items = {item for item in self._shown_items if not isinstance(item, UpPath)}
        self.refresh()

    def action_select_none(self) -> None:
        self._selected_items = set()
        self.refresh()

    def action_invert_selection(self) -> None:
        new_selection = set()
        for item in self._shown_items:
            if isinstance(item, UpPath):
                continue
            if item not in self._selected_items:
                new_selection.add(item)
        self._selected_items = new_selection
        self.refresh()

    def watch_show_hidden_files(self, _old: bool, _new: bool) -> None:
        self.update(self.WhatChanged.FILTERING)

    def _row_at_position(self, y: int) -> int | None:
        _scroll_x, scroll_y = self.scroll_offset
        row = y + scroll_y - self.HEADER_HEIGHT - 1  # -1 for border
        if not self.is_valid_row_index(row):
            return None
        return row

    async def _on_mouse_move(self, event: events.MouseMove) -> None:
        if event.button != 1:
            return
        if not event.ctrl:
            return

        row = self._row_at_position(event.y)
        if row:
            item = self._shown_items[row]
            self._selected_items.add(item)
            self.refresh_row(row)

    async def _on_mouse_down(self, event: events.MouseDown) -> None:
        meta = event.style.meta

        if "row" not in meta or "column" not in meta:
            if event.button == MOUSE_BUTTON_RIGHT:  # TODO: enable context menu on empty area
                self.post_message(DirectoryBrowser.ContextMenu(self, None))
            return
        row_index = meta["row"]
        column_index = meta["column"]

        if row_index < 0:
            # header clicked
            if self.sort_column == column_index:
                self.sort_ascending = not self.sort_ascending
            else:
                self.sort_ascending = True
                self.sort_column = column_index
            return

        self.cursor_row = row_index
        self._scroll_cursor_into_view(animate=True)

        if event.button == MOUSE_BUTTON_RIGHT:
            self.post_message(DirectoryBrowser.ContextMenu(self, self._shown_items[row_index]))

        if event.ctrl:
            self.action_toggle_selection_under_cursor()

        event.stop()

    async def _on_click(self, event: events.Click) -> None:
        meta = event.style.meta
        if "row" not in meta:
            return

        if event.button != MOUSE_BUTTON_LEFT or event.chain != MOUSE_DOUBLE_CLICK:
            return

        event.stop()

        row_index = meta["row"]

        if row_index < 0:
            return  # Header clicked

        item = self._shown_items[row_index]

        if isinstance(item, UpPath):
            path = self._path.parent
        else:
            path = item

        self.post_message(DirectoryBrowser.PathSelected(self, path))

    def _on_mouse_scroll_down(self, event: events.MouseScrollDown) -> None:
        self.action_page_down()
        event.stop()
        event.prevent_default()

    def _on_mouse_scroll_up(self, event: events.MouseScrollUp) -> None:
        self.action_page_up()
        event.stop()
        event.prevent_default()

    # user has selected the item under the cursor (e.g., pressed Enter)
    def _action_select_cursor(self) -> None:
        if not self.is_valid_row_index(self.cursor_row):
            return
        item = self._shown_items[self.cursor_row]

        if isinstance(item, UpPath):
            path = self._path.parent
        else:
            path = item
        self.post_message(DirectoryBrowser.PathSelected(self, path))

    def action_follow_symlink(self) -> None:
        """Navigate to the target of the symlink under the cursor."""
        if not self.is_valid_row_index(self.cursor_row):
            return
        item = self._shown_items[self.cursor_row]
        if isinstance(item, UpPath) or not item.stat.is_symlink:
            return
        try:
            target_vpath = item.filesystem.resolve_link(item)
        except OSError:
            return
        self.post_message(DirectoryBrowser.PathSelected(self, target_vpath))

    def _on_directory_browser_path_selected(self, event: PathSelected) -> None:
        if event.path.stat.is_directory:
            self.set_path(event.path)

    async def action_filter(self) -> None:
        # Position the filter at the top of this browser on screen.
        self._filter_widget.offset = (self.region.x + 2, self.region.y)

        self._filter_widget.focus()

    async def action_go_to_path(self) -> None:
        # Position the go-to widget at the top of this browser on screen.
        self._goto_widget.offset = (self.region.x + 2, self.region.y)

        self._goto_widget.input.value = self._path.uri
        self._goto_widget.input.action_end()
        self._goto_widget.focus()

    def on_filter_widget_input_changed(self, event: Input.Changed) -> None:
        self.update(self.WhatChanged.FILTERING)
