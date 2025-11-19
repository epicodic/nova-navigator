from __future__ import annotations

import os
from abc import abstractmethod
from collections.abc import Callable
from dataclasses import asdict, dataclass
from datetime import datetime
from functools import partial
from pathlib import Path
from typing import Any, ClassVar

from rich.style import Style
from rich.text import Text
from textual import events
from textual.binding import Binding, BindingType
from textual.coordinate import Coordinate
from textual.message import Message
from textual.reactive import var
from textual.widgets import DataTable
from textual.widgets.data_table import ColumnKey

UP_PATH = Path("..")

MOUSE_BUTTON_LEFT = 1
MOUSE_DOUBLE_CLICK = 2

DECIMAL_MAGNITUDE: int = 1000


@dataclass
class PathClassification:
    is_up: bool = False
    is_directory: bool = False
    is_hidden: bool = False
    is_executable: bool = False
    is_symlink: bool = False


class PathInfo(PathClassification):
    path: Path
    size: int
    modified: float

    def __init__(self, path: Path, size: int = -1, modified: float = -1.0, **kwargs: Any) -> None:
        super().__init__(**kwargs)
        self.path = path
        self.size = size
        self.modified = modified


def _query_path_no_except[T](path: Path, f: Callable[[Path], T], default: T) -> T:
    try:
        return f(path)
    except:  # noqa: E722
        return default


def _get_path_info(path: Path) -> PathInfo:
    if path.name == "..":
        return PathInfo(
            path=UP_PATH,
            is_up=True,
            is_directory=True,
        )

    is_directory = _query_path_no_except(path, Path.is_dir, default=False)
    is_hidden = path.name.startswith(".")
    is_symlink = _query_path_no_except(path, Path.is_symlink, default=False)
    is_executable = False
    size = -1
    modified = -1.0

    try:
        stat = path.stat()
        is_executable = not is_directory and stat.st_mode & 0o111 != 0
        size = stat.st_size if path.is_file() else -1
        modified = stat.st_mtime
    except:  # noqa: E722, S110
        pass

    if is_directory:
        # count sub-directories and files
        try:
            size = len(list(os.scandir(path)))
        except:  # noqa: E722
            size = -1

    return PathInfo(
        path=path,
        is_directory=is_directory,
        is_hidden=is_hidden,
        is_executable=is_executable,
        is_symlink=is_symlink,
        size=size,
        modified=modified,
    )


class ColumnItem(Text):
    @abstractmethod
    def _sorter(self) -> tuple[int, Any]:
        raise NotImplementedError

    def sorter(self, reverse: bool) -> Any:
        order, value = self._sorter()
        return (order if not reverse else -order), value

    @staticmethod
    def sorter_fn(reverse: bool, item: ColumnItem) -> Any:
        return item.sorter(reverse)


class PathText(ColumnItem):
    _info: PathInfo

    def __init__(self, info: PathInfo, **kwargs: Any) -> None:
        super().__init__(PathText._path_to_display(info), **kwargs)
        self._info = info

    def __str__(self) -> str:
        return self._info.path.name

    @staticmethod
    def _path_to_display(info: PathInfo) -> str:
        """Convert path to display name."""
        prefix = PathText._name_prefix(info)
        return f"{prefix}{info.path.name}"

    @staticmethod
    def _name_prefix(info: PathInfo) -> str:
        if info.is_directory:
            if info.is_symlink:
                return "~"
            return "/"

        if info.is_symlink:
            return "@"

        return " "

    def _sorter(self) -> tuple[int, str]:
        s = str(self)

        if self._info.is_up:
            return 0, s

        if self._info.is_directory and self._info.is_hidden:
            return 1, s

        if self._info.is_directory and not self._info.is_hidden:
            return 2, s

        if self._info.is_hidden:
            return 3, s

        return 4, s


class SizeText(ColumnItem):
    _info: PathInfo

    def __init__(self, info: PathInfo, **kwargs: Any) -> None:
        super().__init__(SizeText._size_to_display(info), **kwargs)
        self._info = info

    @staticmethod
    def _size_to_display(info: PathInfo) -> str:
        """Convert size in bytes to human-readable format."""
        if info.size < 0:
            return ""

        if info.is_directory:
            return f"{info.size} items"

        for unit in ["", "K", "M", "G", "T"]:
            if info.size < DECIMAL_MAGNITUDE:
                return f"{info.size}{unit}"
            info.size //= DECIMAL_MAGNITUDE
        return f"{info.size}P"

    def _sorter(self) -> tuple[int, int]:
        if self._info.is_up:
            return 0, 0

        if self._info.is_directory:
            return 1, self._info.size

        return 2, self._info.size


class ModifiedText(ColumnItem):
    _info: PathInfo

    def __init__(self, info: PathInfo, **kwargs: Any) -> None:
        super().__init__(ModifiedText._date_to_display(info), **kwargs)
        self._info = info

    @staticmethod
    def _date_to_display(info: PathInfo) -> str:
        """Convert timestamp to human-readable date."""
        dt = datetime.fromtimestamp(info.modified)

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

    def _sorter(self) -> tuple[int, float]:
        if self._info.is_up:
            return 0, 0.0
        return 1, self._info.modified


def list_dir(directory: Path) -> list[Path]:
    try:
        return list(directory.iterdir())
    except OSError:
        return []


class DirectoryBrowser(DataTable):
    BINDINGS: ClassVar[list[BindingType]] = [
        Binding("home", "scroll_top", "Top", show=False),
        Binding("end", "scroll_bottom", "Bottom", show=False),
    ]

    COMPONENT_CLASSES: ClassVar[set[str]] = {
        "highlight-directory",
        "highlight-file",
        "highlight-hidden",
        "highlight-executable",
        "highlight-symlink",
        "highlight-up",
    }

    _path: Path
    _items: list[PathInfo]
    _sort_column: int

    sort_column = var(default=None)

    sort_ascending = var(default=True)

    def __init__(
        self,
        name: str | None = None,
        id: str | None = None,
        path: Path | None = None,
    ) -> None:
        super().__init__(
            name=name,
            id=id,
            cursor_type="row",
        )

        key = self.add_column(Text("Name ⬆", justify="center"), key="name")
        self.sort_column = key
        self.add_column(Text("Size", justify="center"), width=10, key="size")
        self.add_column(Text("Modified", justify="center"), width=12, key="modified")
        self._path = path or Path.cwd()
        self._items = []

    def on_mount(self) -> None:
        self.update()
        super().on_mount()

    def _highlight_style(self, c: PathClassification) -> list[Style]:
        d = asdict(c)
        styles = []

        for key, value in d.items():
            if not value:
                continue

            key_no_prefix = key.removeprefix("is_")

            styles.append(self.get_component_rich_style(f"highlight-{key_no_prefix}", partial=True))

        return styles

    def update(self) -> None:
        self.clear()

        paths = list_dir(self._path)
        if self._path.parent != self._path:
            paths = [UP_PATH, *paths]

        self._items = [_get_path_info(path) for path in paths]
        for index, info in enumerate(self._items):
            styles = self._highlight_style(info)

            if styles:
                style = Style.combine(styles)
            else:
                style = Style()

            self.add_row(
                PathText(info, style=style),
                SizeText(info, style=style, justify="right"),
                ModifiedText(info, style=style),
                key=str(index),
            )

        self._sort()

    class ItemSelected(Message):
        """Posted when an item is selected in the directory browser."""

        def __init__(self, browser: DirectoryBrowser, item: PathInfo) -> None:
            self.browser = browser
            """The directory browser."""
            self.item = item
            super().__init__()

    async def _on_click(self, event: events.Click) -> None:
        if event.button != MOUSE_BUTTON_LEFT or event.chain != MOUSE_DOUBLE_CLICK:
            return

        meta = event.style.meta
        if "row" not in meta or "column" not in meta:
            return
        row_index = meta["row"]
        column_index = meta["column"]

        if row_index < 0 or column_index < 0:
            return  # Header clicked

        row_key, _ = self.coordinate_to_cell_key(Coordinate(row_index, column_index))
        assert row_key.value is not None
        index = int(row_key.value)
        info = self._items[index]

        self.post_message(DirectoryBrowser.ItemSelected(self, info))

    def action_select_cursor(self) -> None:
        super().action_select_cursor()
        row_key, _ = self.coordinate_to_cell_key(self.cursor_coordinate)
        assert row_key.value is not None
        index = int(row_key.value)
        info = self._items[index]
        self.post_message(DirectoryBrowser.ItemSelected(self, info))

    def on_directory_browser_item_selected(self, event: ItemSelected) -> None:
        self.log(f"item chosen: {event.item}")

        if event.item.is_up:
            new_path = self._path.parent
        else:
            new_path = event.item.path

        if event.item.is_directory:
            self._path = new_path
            self.update()

    def on_data_table_header_selected(self, event: DataTable.HeaderSelected) -> None:
        if self.sort_column == event.column_key:
            self.sort_ascending = not self.sort_ascending
        else:
            self.sort_ascending = True
            self.sort_column = event.column_key

    def watch_sort_column(self, _old: ColumnKey, _new: ColumnKey) -> None:
        self._sort()

    def watch_sort_ascending(self, _old: bool, _new: bool) -> None:
        self._sort()

    def _sort(self) -> None:
        if self.sort_column is None:
            return

        self.sort(
            self.sort_column,
            key=partial(ColumnItem.sorter_fn, not self.sort_ascending),
            reverse=not self.sort_ascending,
        )
