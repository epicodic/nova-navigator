from __future__ import annotations

import logging
import re
import subprocess
from enum import Enum
from typing import ClassVar

# import paramiko
from textual import work
from textual.app import App, ComposeResult
from textual.binding import Binding
from textual.containers import Horizontal
from textual.events import Focus, Key, Resize
from textual.logging import TextualHandler
from textual.screen import Screen
from textual.widgets import Footer, Input, Tree

# from nn.widgets.menu import MenuBar, MenuHeader
from nova_navigator import archive
from nova_navigator.config import GLOBAL_CONFIG, get_config_file_path
from nova_navigator.editor import Editor
from nova_navigator.file_operations import copy_or_move_files_operation, delete_files_operation
from nova_navigator.icon_set import ICONS, IconSet
from nova_navigator.operation import Operation

# from nova_navigator.vfs.archive import ArchivePath
# from nova_navigator.vfs import ArchiveFilesystem, LocalFilesystem, SSHFilesystem, VFSPath
from nova_navigator.vfs import ArchiveFilesystem, LocalFilesystem, VFSPath
from nova_navigator.widgets.directory_browser import DirectoryBrowser
from nova_navigator.widgets.popup_widget import PopupWidget
from nova_navigator.widgets.side_bar import SideBar
from nova_navigator.widgets.terminal import Terminal, shell_clear_prompt, shell_cmd_cd, shell_init_code

logging.basicConfig(
    level="INFO",
    handlers=[TextualHandler()],
)


class CommandInput(Input):
    pass


class BookmarksDialog(PopupWidget, can_focus=True):
    """Bookmarks dialog overlay widget."""

    DEFAULT_CSS = """
        BookmarksDialog {
            width: 40;
            height: 20;
        }
    """
    _tree_widget: Tree[VFSPath]

    def __init__(self, position: tuple[int, int]) -> None:
        super().__init__("Bookmarks", position, close_action=PopupWidget.CloseAction.REMOVE)
        self._tree_widget = Tree("Bookmarks")
        self._tree_widget.show_root = False

        for group in GLOBAL_CONFIG.bookmarks.groups:
            group_node = self._tree_widget.root.add(ICONS.get_icon(group.icon) + " " + group.name, expand=True)
            for bookmark in group.bookmarks:
                group_node.add_leaf(ICONS.get_icon(name=bookmark.icon) + " " + bookmark.name)

    def compose(self) -> ComposeResult:
        yield self._tree_widget

    def on_focus(self, event: Focus) -> None:
        self.show()
        self._tree_widget.focus()


class MainScreen(Screen[None]):
    BINDINGS: ClassVar = [
        Binding("^q", "request_quit", "Quit"),
        Binding("ctrl+o", "toggle_maximized_terminal", "Maximize Terminal", priority=True),
        Binding("ctrl+l", "toggle_terminal", "Enlarge Terminal", priority=True),
        Binding("f4", "open_editor", "Edit"),
        Binding("f5", "copy_or_move_files(False)", "Copy"),
        Binding("f6", "copy_or_move_files(True)", "Move"),
        Binding("f8", "delete_files", "Delete"),
        Binding("ctrl+b", "bookmark", "Bookmark"),
    ]

    class _TerminalMode(Enum):
        MINIMIZED = 0
        ENLARGED = 1
        MAXIMIZED = 2

    _left_pane: DirectoryBrowser
    _right_pane: DirectoryBrowser
    _terminal: Terminal
    _terminal_waits_for_enter: bool = False
    _terminal_mode: _TerminalMode
    _last_active_pane: DirectoryBrowser
    _operations: list[Operation]

    _bookmark_dialog: BookmarksDialog

    def compose(self) -> ComposeResult:
        # yield Header()

        # yield MenuBar(
        #     MenuHeader(menu_id="left", name="Left"),
        #     MenuHeader(menu_id="file", name="File"),
        #     MenuHeader(menu_id="edit", name="Edit"),
        #     MenuHeader(menu_id="settings", name="Settings"),
        #     MenuHeader(menu_id="right", name="Right"),
        # )

        self._left_side_bar = SideBar()
        self._left_pane = DirectoryBrowser(id="pane-left", path=LocalFilesystem.singleton().cwd())
        self._last_active_pane = self._left_pane

        # client = paramiko.SSHClient()
        # client.load_system_host_keys()
        # client.connect("127.0.0.1")
        # fs = SSHFilesystem(client)
        # self._right_pane = DirectoryBrowser(id="pane-right", path=fs.cwd())
        self._right_pane = DirectoryBrowser(id="pane-right", path=LocalFilesystem.singleton().cwd())
        yield Horizontal(
            self._left_side_bar,
            self._left_pane,
            self._right_pane,
        )

        self._terminal = Terminal("/usr/bin/zsh", id="terminal")
        self._terminal.styles.height = 1
        self._terminal.start()

        yield self._terminal
        yield Footer()

    def __init__(self) -> None:
        super().__init__()
        self._terminal_mode = self._TerminalMode.MINIMIZED
        self._operations = []

    async def on_mount(self) -> None:
        pre_cmd = shell_init_code(self._terminal.fd_pre_cmd_child)
        await self._terminal.send(pre_cmd)

    def on_resize(self, event: Resize) -> None:
        self._resize_terminal()

    # async def on_event(self, event: events.Event) -> None:
    #     if isinstance(event, Key):
    #         if await self.grab_key_events(event):
    #             return  # event was handled

    #     await super().on_event(event)
    async def _on_key(self, event: Key) -> None:
        self.log(f"MainScreen _on_key: {event.key}")
        if await self._handle_key(event):
            # if the event was handled, stop its propagation
            event.stop()
            return

    async def _handle_key(self, event: Key) -> bool:
        if self._terminal_mode == self._TerminalMode.MAXIMIZED:
            return False

        if not self._left_pane.has_focus and not self._right_pane.has_focus:
            return False  # special handling only when a pane has focus

        match event.key:
            case "tab":
                await self.action_toggle_panels()
                return True

            case "shift+tab":
                await self._terminal.on_key(Key("tab", character="\t"))
                return True

            case "enter":
                if self._terminal_waits_for_enter:
                    self._terminal_waits_for_enter = False
                    await self._terminal.on_key(event)
                    return True

            case "ctrl+down":
                path = self._last_active_pane.path_item_under_cursor
                await self._terminal.send(f"{path.name}")
                return True

            case "ctrl+shift+down":
                path = self._last_active_pane.path_item_under_cursor
                await self._terminal.send(f"{path}")
                return True

        KEYS_TO_MAP_TO_TERMINAL = {
            "backspace": "backspace",
            "delete": "delete",
            "left": "left",
            "right": "right",
            "shift+up": "up",
            "shift+down": "down",
        }

        if event.key in KEYS_TO_MAP_TO_TERMINAL:
            mapped_key = KEYS_TO_MAP_TO_TERMINAL[event.key]
            mapped_key_event = Key(mapped_key, character=event.character)
            await self._terminal.on_key(mapped_key_event)
            return True

        if event.is_printable:
            self._terminal_waits_for_enter = True
            await self._terminal.on_key(event)
            return True

        return False

    async def grab_key_events(self, event: Key) -> bool:
        """Grab key events before they reach other widgets.

        Return True if the event was handled here, False to let it propagate.
        """
        # recover Ctrl+H (the "real" backspace has character \x7f)
        if event.key == "backspace" and event.character == "\x08":
            event.key = "ctrl+h"

        self.log(f"grab_key_events: {event.key}")

        return False

    async def action_toggle_panels(self) -> None:
        self.log("Toggling panels")
        if self._last_active_pane == self._left_pane:
            self._right_pane.focus()
            self._last_active_pane = self._right_pane
        else:
            self._left_pane.focus()
            self._last_active_pane = self._left_pane
        await self._set_terminal_directory(self._last_active_pane.path)

    def action_toggle_maximized_terminal(self) -> None:
        if self._terminal_mode == self._TerminalMode.MAXIMIZED:
            self._terminal_mode = self._TerminalMode.MINIMIZED
            self._last_active_pane.focus()
        else:
            self._terminal_mode = self._TerminalMode.MAXIMIZED
            self._terminal.focus()
        self._resize_terminal()

    def action_toggle_terminal(self) -> None:
        if self._terminal_mode == self._TerminalMode.ENLARGED:
            self._terminal_mode = self._TerminalMode.MINIMIZED
            self._last_active_pane.focus()
        else:
            self._terminal_mode = self._TerminalMode.ENLARGED
            self._last_active_pane.focus()
        self._resize_terminal()

    def _resize_terminal(self) -> None:
        self._terminal.styles.width = int(self.size.width)
        match self._terminal_mode:
            case self._TerminalMode.MINIMIZED:
                self._terminal.styles.height = 1
            case self._TerminalMode.ENLARGED:
                self._terminal.styles.height = self.size.height // 2
            case self._TerminalMode.MAXIMIZED:
                self._terminal.styles.height = self.size.height - 2

    async def _set_terminal_directory(self, path: VFSPath) -> None:
        # assert isinstance(path, LocalPath), "Only local paths are supported at the moment"
        if not isinstance(path.filesystem, LocalFilesystem):
            return
        await self._terminal.send(shell_clear_prompt() + " " + shell_cmd_cd(path.path) + "\n")

    async def _on_directory_browser_path_selected(self, event: DirectoryBrowser.PathSelected) -> None:
        if event.path.stats.is_directory:
            await self._set_terminal_directory(event.path)
            return

        # handle archives
        path = event.path

        if archive.is_supported_archive(path.path):
            archive_path = VFSPath("/", ArchiveFilesystem(archive_parent=path.parent, archive=path))
            event.browser.set_path(archive_path)
            return

        if path.stats.is_executable:
            mimetype = path.guess_mimetype()
            # if mimetype matches ".*/x-.*", run it directly
            if mimetype is None or re.match(r".*/x-.*$", mimetype) is not None:
                subprocess.Popen(args=[path.path], cwd=path.parent.path)
                return

        # open file with xdg-open
        open_cmd = GLOBAL_CONFIG.filetypes.get_open_command_for_file_path(path.path)
        subprocess.Popen(args=open_cmd, cwd=path.parent.path)

    def _on_terminal_pre_cmd(self, event: Terminal.PreCmd) -> None:
        # TODO handle non-local paths
        self._last_active_pane.set_path(VFSPath(event.cwd, LocalFilesystem.singleton()))

    # operations

    @work
    async def action_copy_or_move_files(self, move: bool) -> None:
        source_paths = list(self._last_active_pane.selected_path_items)
        destination_path = self._left_pane.path if self._last_active_pane == self._right_pane else self._right_pane.path

        operation = await copy_or_move_files_operation(
            source_paths=source_paths,
            destination_path=destination_path,
            move=move,
        )
        if operation is not None:
            self._operations.append(operation)

    @work
    async def action_delete_files(self) -> None:
        paths = list(self._last_active_pane.selected_path_items)

        operation = await delete_files_operation(
            paths=paths,
        )
        if operation is not None:
            self._operations.append(operation)

    @work
    async def action_open_editor(self) -> None:
        path = self._last_active_pane.path_item_under_cursor
        editor_screen = Editor()
        self.app.push_screen(editor_screen)
        editor_screen.open(path)

    async def action_bookmark(self) -> None:
        self._bookmark_dialog = BookmarksDialog(position=(2, 2))
        await self.mount(self._bookmark_dialog)
        self._bookmark_dialog.focus()


class NovaNavigator(App[None]):
    """Nova Navigator App."""

    CSS_PATH = "nn.tcss"

    def __init__(self) -> None:
        super().__init__()

    async def on_mount(self) -> None:
        self.log("Starting Nova Navigator...")
        self._main_screen = MainScreen()
        self.install_screen(self._main_screen, "main_screen")
        self.push_screen("main_screen")


def main() -> None:
    """Main function."""
    GLOBAL_CONFIG.load_all_configs()
    ICONS.load_icons(get_config_file_path("icons.csv"))
    ICONS.set_variant(IconSet.Variants.NERDFONT)

    NovaNavigator().run()
    # global_config.write_all_configs()


if __name__ == "__main__":
    main()
