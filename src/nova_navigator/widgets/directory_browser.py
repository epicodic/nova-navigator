from __future__ import annotations

import asyncio
import threading
from collections.abc import Callable
from dataclasses import dataclass
from datetime import datetime
from enum import Enum
from typing import Any, ClassVar, Self

import watchdog
import watchdog.events
import watchdog.observers
import watchdog.observers.api
from rich.color import Color
from rich.segment import Segment
from rich.style import Style
from textual import events, on
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

from .. import unicode
from ..config import GLOBAL_CONFIG
from ..icon_set import ICONS
from ..path_stats import PathStats
from ..vfs import VFSPath
from ..vfs.local import LocalFilesystem
from .overlay_widget import OverlayWidget


class UpPath(VFSPath):
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
    def stats(self) -> PathStats:
        return PathStats(is_directory=True)

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

DECIMAL_MAGNITUDE: int = 1000


def _ljust(s: str, width: int) -> str:
    if unicode.wcswidth(s) > width:
        return s[: width - 1] + "…"
    return unicode.ljust(s, width)


def _rjust(s: str, width: int) -> str:
    if unicode.wcswidth(s) > width:
        return "…" + s[1 - width :]
    return unicode.rjust(s, width)


def column_formatter_icon(path: VFSPath) -> str:
    """Convert path to display icon."""
    stats = path.stats

    if stats.is_directory:
        icon = ICONS.get_icon("folder")
    else:
        icon = GLOBAL_CONFIG.filetypes.get_icon_for_filename(path.name, default=ICONS.get_icon("file"))

    if not stats.is_directory and not path.guess_mimetype() and stats.is_executable:
        icon = ICONS.get_icon("executable")

    if stats.is_symlink:
        icon += "~"
    else:
        icon += " "

    if stats.is_symlink and stats.is_broken_symlink:
        icon = ICONS.get_icon("broken link") + "!"

    return icon


def column_formatter_name(path: VFSPath) -> str:
    """Convert path to display name."""
    return f"{path.name}"


def column_formatter_size(path: VFSPath) -> str:
    """Convert size in bytes to human-readable format."""
    stats = path.stats
    if stats.size < 0:
        return ""

    if stats.is_directory:
        return "-"

    size = stats.size
    for unit in ["", "K", "M", "G", "T"]:
        if size < DECIMAL_MAGNITUDE:
            return f"{size}{unit}"
        size //= DECIMAL_MAGNITUDE
    return f"{size}P"


def column_formatter_modified(path: VFSPath) -> str:
    """Convert timestamp to human-readable date."""
    stats = path.stats
    if stats.modified < 0:
        return ""

    dt = datetime.fromtimestamp(stats.modified)

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


def column_sorter_name(path: VFSPath) -> tuple[int, str]:
    if isinstance(path, UpPath):
        return 0, path.name

    stats = path.stats
    if stats.is_directory and stats.is_hidden:
        return 1, path.name

    if stats.is_directory and not stats.is_hidden:
        return 2, path.name

    if stats.is_hidden:
        return 3, path.name

    return 4, path.name


def column_sorter_size(path: VFSPath) -> tuple[int, int]:
    if isinstance(path, UpPath):
        return 0, 0

    stats = path.stats
    if stats.is_directory:
        return 1, stats.size
    return 2, stats.size


def column_sorter_modified(path: VFSPath) -> tuple[int, float]:
    if isinstance(path, UpPath):
        return 0, 0.0
    stats = path.stats
    return 1, stats.modified


@dataclass
class Column:
    title: str
    width: int
    formatter: Callable[[VFSPath], str]
    sorter: Callable[[VFSPath], Any]


class FilterWidget(OverlayWidget, can_focus=True):
    """Widget for finding files in the directory browser."""

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

    BINDINGS: ClassVar[list[BindingType]] = [
        Binding("escape", "dismiss", "Dismiss", show=False),
    ]

    input: Input

    def __init__(self, title: str, position: tuple[int, int], browser: DirectoryBrowser) -> None:
        super().__init__(
            title, position, close_on_escape=False, close_on_blur=False, close_action=self.CloseAction.NONE
        )
        self.browser = browser
        self.input = Input(placeholder="Type to filter ...", compact=True)
        self.display = False

    def compose(self) -> ComposeResult:
        yield Horizontal(
            Static("Filter:"),
            self.input,
            Button(ICONS.get_icon("xmark"), id="close-button", compact=True),
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
        self.parent_widget.focus()

    def action_dismiss(self) -> None:
        self.input.value = ""
        self.hide()
        self.parent_widget.focus()

    @property
    def value(self) -> str:
        return self.input.value if self.input is not None else ""

    @on(Input.Changed)
    def _on_input_changed(self, event: Input.Changed) -> None:
        self.browser.on_filter_widget_input_changed(event)


class DirectoryBrowser(ScrollView):
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
        Binding("ctrl+h", "toggle_hidden", "Show/Hide Hidden Files", show=False),
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

        def __init__(self, browser: DirectoryBrowser, path: VFSPath) -> None:
            self.browser = browser
            """The directory browser."""
            self.path = path
            super().__init__()

    class ContextMenu(Message):
        """Posted when the context menu is requested in the directory browser."""

        def __init__(self, browser: DirectoryBrowser, path: VFSPath) -> None:
            self.browser = browser
            """The directory browser."""
            self.path = path
            super().__init__()

    # private classes

    class _FileSystemEventHandler(watchdog.events.FileSystemEventHandler):
        DEBOUNCE_INTERVAL_SECONDS = 0.2

        _timer: threading.Timer | None

        def __init__(self, browser: DirectoryBrowser) -> None:
            self.browser = browser
            self._loop = asyncio.get_event_loop()
            self._timer = None
            self._lock = threading.Lock()

        async def handle(self) -> None:
            self.browser.update(self.browser.WhatChanged.ALL)

        def on_timer(self) -> None:
            self._loop.call_soon_threadsafe(asyncio.create_task, self.handle())

        def on_any_event(self, event: watchdog.events.FileSystemEvent) -> None:
            # logging.warning(f"Filesystem event: {event}")
            if not isinstance(event, watchdog.events.DirModifiedEvent):
                return  # ignore other events

            with self._lock:
                if self._timer is not None:
                    self._timer.cancel()

                self._timer = threading.Timer(
                    self.DEBOUNCE_INTERVAL_SECONDS,
                    self.on_timer,
                )
                self._timer.start()

    # members

    HEADER_HEIGHT: ClassVar[int] = 1

    _path: VFSPath

    _all_items: list[VFSPath]
    _shown_items: list[VFSPath]
    _selected_items: set[VFSPath]

    _empty_strip: Strip = Strip([])
    _columns: list[Column]
    _max_line_width: int
    # _observer: watchdog.observers.ObserverType
    _watch: watchdog.observers.api.ObservedWatch | None
    _filter_widget: FilterWidget

    show_hidden_files: Reactive[bool] = Reactive(default=False, repaint=False, always_update=False)
    cursor_row: Reactive[int] = Reactive(default=0, repaint=False, always_update=True)
    sort_column = var(default=0)
    sort_ascending = var(default=True)

    def __init__(
        self,
        path: VFSPath,
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
        self._observer = watchdog.observers.Observer()
        self._observer.start()
        self._watch = None
        self._all_items = []
        self._shown_items = []
        self._selected_items = set()
        self._max_line_width = 0
        self._filter_widget = FilterWidget(
            title="Filter",
            position=(10, 0),
            browser=self,
        )
        self.set_path(path)

    def on_mount(self) -> None:
        super().on_mount()
        self.mount(self._filter_widget)

    async def _on_key(self, event: events.Key) -> None:
        self.log("KEY EVENT!!!", event)
        # event.stop()

    @property
    def path(self) -> VFSPath:
        return self._path

    @property
    def path_item_under_cursor(self) -> VFSPath:
        return self._shown_items[self.cursor_row]

    @property
    def selected_path_items(self) -> list[VFSPath]:
        if not self._selected_items:
            if isinstance(self.path_item_under_cursor, UpPath):
                return []
            return [self.path_item_under_cursor]
        return list(self._selected_items)

    def set_path(self, path: VFSPath) -> None:
        if path == self._path:
            return

        old_name = None
        if path == self._path.parent:
            # navigating up, keep cursor on same item if possible
            old_name = self._path.name

        self.border_title = path.compact_path_str
        self._path = path
        self.update(self.WhatChanged.ALL)

        if old_name is not None:
            for index, item in enumerate(self._shown_items):
                if item.name == old_name:
                    self.cursor_row = index
                    break
        else:
            self.cursor_row = 0

        if self._watch is not None:
            self._observer.unschedule(self._watch)
            self._watch = None

        if isinstance(self._path.filesystem, LocalFilesystem):
            self.log(f"Watching path: {self._path.path}")
            self._watch = self._observer.schedule(
                self._FileSystemEventHandler(self),
                self._path.path.as_posix(),
                recursive=False,
                # event_filter=[watchdog.events.DirModifiedEvent],
            )

    # Data management

    class WhatChanged(int, Enum):
        ALL = 0
        SORTING = 1
        FILTERING = 2

    def update(self, what_changed: WhatChanged) -> None:
        # store old selection and cursor state
        old_item_under_cursor = self._shown_items[self.cursor_row] if self._shown_items else None
        old_selected_items = self._selected_items

        if what_changed <= self.WhatChanged.ALL:
            self._all_items = self._path.iterdir()

        if what_changed <= self.WhatChanged.SORTING:
            column_sorter = self._columns[self.sort_column].sorter

            def sorter(item: VFSPath) -> Any:
                order, value = column_sorter(item)
                return (order if self.sort_ascending else -order), value

            self._all_items.sort(key=sorter, reverse=not self.sort_ascending)

        self._shown_items = [UP_PATH] if self._path.parent != self._path else []
        if what_changed <= self.WhatChanged.FILTERING:
            if self.show_hidden_files and len(self._filter_widget.value) == 0:
                self._shown_items += self._all_items
            else:

                def filter_func(item: VFSPath) -> bool:
                    if not self.show_hidden_files and item.stats.is_hidden:
                        return False
                    if len(self._filter_widget.value) == 0:
                        return True
                    return self._filter_widget.value.lower() in item.name.lower()

                self._shown_items += [item for item in self._all_items if filter_func(item)]

        # restore cursor position
        self.cursor_row = 0
        for index, item in enumerate(self._shown_items):
            if item == old_item_under_cursor:
                self.cursor_row = index
                break
        self.cursor_row = self.validate_cursor_row(self.cursor_row)

        # restore selected items
        self._selected_items = set()
        for item in old_selected_items:
            if item in self._shown_items:
                self._selected_items.add(item)

        self._max_line_width = 0
        self._update_virtual_size()

        self.refresh()

    # Rendering

    def _highlight_style(self, path: VFSPath) -> list[Style]:
        styles = []

        color, background_color = GLOBAL_CONFIG.filetypes.get_colors_for_filename(path.name)
        if color:
            styles.append(
                Style(
                    color=color,
                )
            )

        if background_color:
            styles.append(
                Style(
                    bgcolor=background_color,
                )
            )

        stats = path.stats
        hightlight_type_map: dict[str, bool] = {
            "highlight-directory": stats.is_directory,
            "highlight-hidden": stats.is_hidden,
            "highlight-executable": stats.is_executable and not stats.is_directory,
            "highlight-symlink": stats.is_symlink,
            "highlight-broken-symlink": stats.is_symlink and stats.is_broken_symlink,
            "highlight-selected": path in self._selected_items,
        }

        for style, value in hightlight_type_map.items():
            if value:
                styles.append(self.get_component_rich_style(style, partial=True))

        return styles

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
            self._scroll_cursor_into_view()

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

        cursor_item = self.path_item_under_cursor
        if cursor_item in self._selected_items:
            self._selected_items.remove(cursor_item)
        else:
            self._selected_items.add(cursor_item)

        self.refresh_row(self.cursor_row)
        self.action_cursor_down()

    def action_select_all(self) -> None:
        self._selected_items = {item for item in self._shown_items if not isinstance(item, UpPath)}
        self.refresh()

    def action_toggle_hidden(self) -> None:
        self.show_hidden_files = not self.show_hidden_files
        # self.update(self.WhatChanged.FILTERING)

    def watch_show_hidden_files(self, _old: bool, _new: bool) -> None:
        self.update(self.WhatChanged.FILTERING)

    async def _on_mouse_down(self, event: events.MouseDown) -> None:
        meta = event.style.meta

        if "row" not in meta or "column" not in meta:
            # if event.button == MOUSE_BUTTON_RIGHT: # TODO: enable context menu on empty area
            #    self.post_message(DirectoryBrowser.ContextMenu(self, None)) #
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

        event.stop()

    async def _on_click(self, event: events.Click) -> None:
        meta = event.style.meta
        if "row" not in meta:
            return

        if event.button != MOUSE_BUTTON_LEFT or event.chain != MOUSE_DOUBLE_CLICK:
            return

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

    def _on_directory_browser_path_selected(self, event: PathSelected) -> None:
        if event.path.stats.is_directory:
            self.set_path(event.path)

    async def _action_filter(self) -> None:
        self._filter_widget.focus()

    def on_filter_widget_input_changed(self, event: Input.Changed) -> None:
        self.update(self.WhatChanged.FILTERING)
