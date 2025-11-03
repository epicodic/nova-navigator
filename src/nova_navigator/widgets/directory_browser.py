from __future__ import annotations

from collections.abc import Callable
from dataclasses import dataclass
from datetime import datetime
from typing import Any, ClassVar, Self

from rich.segment import Segment
from rich.style import Style
from textual import events
from textual.binding import Binding, BindingType
from textual.geometry import Region, Size, Spacing, clamp
from textual.message import Message
from textual.reactive import Reactive, var
from textual.scroll_view import ScrollView
from textual.strip import Strip
from textual.widgets.data_table import ColumnKey

from ..config import global_config
from ..vfs import LocalPath, UpPath, VFSPath

UP_PATH = UpPath()

MOUSE_BUTTON_LEFT = 1
MOUSE_DOUBLE_CLICK = 2

DECIMAL_MAGNITUDE: int = 1000


def _ljust(s: str, width: int) -> str:
    if len(s) > width:
        return s[: width - 1] + "…"
    return s.ljust(width)


def _rjust(s: str, width: int) -> str:
    if len(s) > width:
        return "…" + s[1 - width :]
    return s.rjust(width)


def column_formatter_name(path: VFSPath) -> str:
    """Convert path to display name."""

    def _name_prefix(path: VFSPath) -> str:
        stats = path.stats
        if stats.is_directory:
            if stats.is_symlink:
                return "~"
            return "/"

        if stats.is_symlink:
            return "@"

        return " "

    prefix = _name_prefix(path)
    return f"{prefix}{path.name}"


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
    ]

    COMPONENT_CLASSES: ClassVar[set[str]] = {
        "cursor",
        "highlight-directory",
        "highlight-hidden",
        "highlight-executable",
        "highlight-symlink",
        "highlight-up",
    }

    # messages
    class PathSelected(Message):
        """Posted when an item is selected in the directory browser."""

        def __init__(self, browser: DirectoryBrowser, path: VFSPath) -> None:
            self.browser = browser
            """The directory browser."""
            self.path = path
            super().__init__()

    # members

    HEADER_HEIGHT: ClassVar[int] = 1

    _path: VFSPath

    _items: list[VFSPath]
    _empty_strip: Strip = Strip([])
    _columns: list[Column]
    _max_line_width: int

    cursor_row: Reactive[int] = Reactive(10, repaint=False, always_update=True)
    sort_column = var(default=0)
    sort_ascending = var(default=True)

    def __init__(
        self,
        name: str | None = None,
        id: str | None = None,
        path: VFSPath | None = None,
    ) -> None:
        super().__init__(
            name=name,
            id=id,
        )
        self._path = path or LocalPath.cwd()

        self._columns = [
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
        self.update()

    def on_mount(self) -> None:
        super().on_mount()

    def set_path(self, path: VFSPath) -> None:
        if path == self._path:
            return

        self._path = path
        self.update()

    # Data management

    def update(self) -> None:
        if self._path.parent != self._path:
            self._items = [UP_PATH]
        else:
            self._items = []

        self._items += self._path.iterdir()

        self.cursor_row = 0
        self._max_line_width = 0
        self._update_virtual_size()
        self._sort()

    def _sort(self) -> None:
        column_sorter = self._columns[self.sort_column].sorter

        def sorter(item: VFSPath) -> Any:
            order, value = column_sorter(item)
            return (order if self.sort_ascending else -order), value

        self._items.sort(key=sorter, reverse=not self.sort_ascending)
        self.refresh()

    # Rendering

    def _highlight_style(self, path: VFSPath) -> list[Style]:
        styles = []

        color, background_color = global_config.extensions.get_colors_for_filename(path.name)
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
        }

        for style, value in hightlight_type_map.items():
            if value:
                styles.append(self.get_component_rich_style(style, partial=True))

        return styles

    def render_line(self, y: int) -> Strip:
        scroll_x, scroll_y = self.scroll_offset

        row = y + scroll_y - self.HEADER_HEIGHT  # -1 for header
        if row >= len(self._items):
            return self._empty_strip

        other_width = (
            self._columns[1].width
            + 2  # padding between size and modified
            + self._columns[2].width
            + 1  # padding between modified and scrollbar
            + 1  # scrollbar
            + 1  # ?
        )

        remaining = self.size.width - other_width
        remaining = max(remaining, 1)

        style = self.rich_style

        if row == self.cursor_row:
            style += self.get_component_rich_style("cursor", partial=True)

        if y == 0:
            style += Style.from_meta({"row": -1})
            row_texts = (
                self._columns[0].title,
                self._columns[1].title,
                self._columns[2].title,
            )
        else:
            style += Style.from_meta({"row": row})
            item = self._items[row]
            row_texts = (
                self._columns[0].formatter(item)[scroll_x:],
                self._columns[1].formatter(item),
                self._columns[2].formatter(item),
            )

            styles = self._highlight_style(item)
            if styles:
                style += Style.combine(styles)

        total_width = len(row_texts[0]) + other_width
        if total_width > self._max_line_width:
            self._max_line_width = total_width
            self._update_virtual_size()

        first_column_width = remaining
        name_segment = Segment(
            _ljust(row_texts[0], first_column_width) + " ", style=style + Style.from_meta({"column": 0})
        )
        size_segment = Segment(
            _rjust(row_texts[1], self._columns[1].width) + "  ", style=style + Style.from_meta({"column": 1})
        )
        modified_segment = Segment(
            _rjust(row_texts[2], self._columns[2].width) + " ", style=style + Style.from_meta({"column": 2})
        )

        return Strip([name_segment, size_segment, modified_segment])

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
        return 0 <= row_index < len(self._items)

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
        self.virtual_size = Size(self._max_line_width, len(self._items) + self.HEADER_HEIGHT)

    # Events

    def validate_cursor_row(self, row: int) -> int:
        return clamp(row, 0, len(self._items) - 1)

    def watch_cursor_row(self, old_row: int, new_row: int) -> None:
        if old_row != new_row:
            self.refresh_row(old_row)
            self.refresh_row(new_row)
            self._scroll_cursor_into_view()

    def watch_sort_column(self, _old: ColumnKey, _new: ColumnKey) -> None:
        self._sort()

    def watch_sort_ascending(self, _old: bool, _new: bool) -> None:
        self._sort()

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
        self.cursor_row = len(self._items) - 1

    async def _on_mouse_down(self, event: events.MouseDown) -> None:
        meta = event.style.meta
        if "row" not in meta or "column" not in meta:
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

        item = self._items[row_index]

        if isinstance(item, UpPath):
            path = self._path.parent
        else:
            path = item

        self.post_message(DirectoryBrowser.PathSelected(self, path))

    def _on_mouse_scroll_down(self, event: events.MouseScrollDown) -> None:
        self.action_cursor_down()
        event.stop()
        event.prevent_default()

    def _on_mouse_scroll_up(self, event: events.MouseScrollUp) -> None:
        self.action_cursor_up()
        event.stop()
        event.prevent_default()

    def _action_select_cursor(self) -> None:
        if not self.is_valid_row_index(self.cursor_row):
            return
        item = self._items[self.cursor_row]

        if isinstance(item, UpPath):
            path = self._path.parent
        else:
            path = item
        self.post_message(DirectoryBrowser.PathSelected(self, path))

    def _on_directory_browser_path_selected(self, event: PathSelected) -> None:
        if event.path.stats.is_directory:
            self.set_path(event.path)
