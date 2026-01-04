from __future__ import annotations

import asyncio
import logging
import re
import subprocess
from enum import Enum
from pathlib import PurePath
from typing import ClassVar, NamedTuple

# import paramiko
from textual import events, work
from textual.app import App, ComposeResult
from textual.binding import Binding
from textual.containers import Horizontal
from textual.logging import TextualHandler
from textual.screen import Screen
from textual.widgets import Input

# from nn.widgets.menu import MenuBar, MenuHeader
from nova_navigator import archive
from nova_navigator.config import conf_, get_config_file_path
from nova_navigator.dialogs import BookmarksDialog
from nova_navigator.dialogs.decision_dialog import DecisionDialog
from nova_navigator.editor import Editor
from nova_navigator.icons import ICONS, IconSet
from nova_navigator.task import Decision, DecisionRequest

# from nova_navigator.operations.file_operations import copy_or_move_files_operation, delete_files_operation
from nova_navigator.uri import register_common_schemes, vfspath_from_uri
from nova_navigator.vfs2 import VPath
from nova_navigator.vfs2.filesystems import ArchiveFilesystem, LocalFilesystem
from nova_navigator.widgets import DirectoryBrowser, Footer
from nova_navigator.widgets.terminal import Terminal, shell_clear_prompt, shell_cmd_cd, shell_init_code
from nova_widgets.menu import SYMBOL_TABLE, Action, Menu, MenuBar, set_icon_provider
from nova_widgets.menu import constructor as mc

from .filemanager.jobs import copy_or_move_files_job

logging.basicConfig(
    level="INFO",
    handlers=[TextualHandler()],
)


class CommandInput(Input):
    pass


class MainScreen(Screen[None]):
    BINDINGS: ClassVar = [
        Binding("^q", "quit", "Quit"),
        Binding("ctrl+o", "toggle_maximized_terminal", "Maximize Terminal", priority=True),
        Binding("ctrl+l", "toggle_terminal", "Enlarge Terminal", priority=True),
        Binding("f4", "open_editor", "Edit"),
        Binding("f5", "copy_or_move_files(False)", "Copy"),
        # Binding("f6", "copy_or_move_files(True)", "Move"),
        # Binding("f8", "delete_files", "Delete"),
        Binding("ctrl+b", "show_bookmarks", "Bookmark"),
        Binding("ctrl+h", "toggle_hidden", description="Show/Hide Hidden Files", show=False),
        # Binding("ctrl+s", "suspend", "Suspend"),
        Binding("ctrl+k", "show_processes", "Processes"),
        Binding("ctrl+d", "start_dummy_operation", "Start Dummy Operation"),
    ]

    class _TerminalMode(Enum):
        MINIMIZED = 0
        ENLARGED = 1
        MAXIMIZED = 2

    _left_panel: DirectoryBrowser
    _right_panel: DirectoryBrowser
    _terminal: Terminal
    _terminal_waits_for_enter: bool = False
    _terminal_mode: _TerminalMode
    _last_active_panel: DirectoryBrowser

    _bookmark_dialog: BookmarksDialog

    def __init__(self) -> None:
        super().__init__()
        self._terminal_mode = self._TerminalMode.MINIMIZED
        # self._operations = []

    def compose(self) -> ComposeResult:
        self._menu_bar = MenuBar()

        self._menu_bar.add_menu(
            "Ⓝ ",
            mc.action("Settings", icon="gear", shortcut="Ctrl+F1"),
            mc.separator(),
            mc.action("About", icon="info"),
            mc.separator(),
            mc.action("Quit", shortcut="Ctrl+Q", action="quit"),
        )

        self._menu_bar.add_menu("File", name="file").add(
            mc.menu(
                "New",
                mc.action("Directory", icon="folder", shortcut="F7", name="directory"),
                mc.action("File", icon="text", name="file"),
                name="new",
            ),
            mc.separator(),
            mc.action("Open", shortcut="Enter", action="open_path", name="open"),
            mc.action("Open in Other Panel", action="open_in_other_panel", name="open_in_other_panel"),
            mc.action("Edit", shortcut="F4", action="open_editor", name="edit"),
            mc.separator(),
            mc.action("Copy", shortcut="Ctrl+C", name="copy"),
            mc.action("Cut", shortcut="Ctrl+X", name="cut"),
            mc.action("Copy Names", name="copy_names"),
            mc.action("Paste", name="paste"),
            mc.separator(),
            mc.action("Delete", shortcut="F8", name="delete"),
            mc.action("Rename", name="rename"),
            mc.separator(),
            mc.action("Filter", shortcut="Ctrl+F", action="filter", name="filter"),
        )

        self._menu_bar.add_menu("Selection", name="selection").add(
            mc.action("Select All", shortcut="Ctrl+A", action="select_all"),
            mc.action("Select None", shortcut="Ctrl+S,N", action="select_none"),
            mc.action("Invert Selection", shortcut="Ctrl+S,I", action="invert_selection"),
            mc.separator(),
            mc.action(
                "Toggle Selection",
                name="toggle_selection",
                action="toggle_selection_under_cursor",
                shortcut="Ctrl+Click/Ins",
            ),
            mc.separator(),
            mc.action("Select By Pattern...", name="select_by_pattern"),
        )

        self._menu_bar.add_menu("View", name="view").add(
            mc.action("Refresh", name="refresh"),
            mc.separator(),
            mc.action(
                "Show Hidden Files",
                checkable=True,
                checked=False,
                shortcut="Ctrl+H",
                action="show_hidden_files",
                name="show_hidden_files",
            ),
            mc.separator(),
            mc.action("Synchronized Browsing", checkable=True),
            mc.menu(
                "Compare Directories",
                mc.action("Enable", checkable=True),
                mc.separator(),
                *mc.group(
                    mc.action("By File Size", checkable=True),
                    mc.action("By Modification Time", checkable=True),
                ),
                mc.separator(),
                mc.action("Hide Identical Files", checkable=True),
            ),
        )

        self.action_processes = self._menu_bar.add_menu("Processes")

        yield self._menu_bar

        self._left_panel = DirectoryBrowser(id="pane-left", path=LocalFilesystem.singleton().cwd())
        self._last_active_panel = self._left_panel

        # client = paramiko.SSHClient()
        # client.load_system_host_keys()
        # client.connect("127.0.0.1")
        # fs = SSHFilesystem(client)
        # self._right_pane = DirectoryBrowser(id="pane-right", path=fs.cwd())
        self._right_panel = DirectoryBrowser(id="pane-right", path=LocalFilesystem.singleton().cwd())
        yield Horizontal(
            self._left_panel,
            self._right_panel,
        )

        self._terminal = Terminal("/usr/bin/zsh", id="terminal")
        self._terminal.styles.height = 1
        self._terminal.start()

        yield self._terminal
        yield Footer()

    def _act(self, name: str) -> Action:
        action = self._menu_bar.find_action(name)
        assert action is not None, f"Action '{name}' not found"
        return action

    def active_panel(self) -> DirectoryBrowser:
        return self._last_active_panel

    def other_panel(self) -> DirectoryBrowser:
        if self._last_active_panel == self._left_panel:
            return self._right_panel
        return self._left_panel

    async def on_mount(self) -> None:
        pre_cmd = shell_init_code(self._terminal.fd_pre_cmd_child)
        await self._terminal.send(pre_cmd)

    def on_resize(self, event: events.Resize) -> None:
        self._resize_terminal()

    def _action_quit(self) -> None:
        self.app.exit()

    async def on_event(self, event: events.Event) -> None:
        if isinstance(event, events.Key):  # noqa: SIM102
            # recover Ctrl+H (the "real" backspace has character \x7f)
            if event.key == "backspace" and event.character == "\x08":
                event.key = "ctrl+h"

        await super().on_event(event)

    async def _on_key(self, event: events.Key) -> None:
        self.log(f"MainScreen _on_key: {event.key}")
        if await self._handle_key(event):
            # if the event was handled, stop its propagation
            event.stop()
            return

    def _on_directory_browser_focus(self, event: DirectoryBrowser.Focus) -> None:
        self._last_active_panel = event.browser

    async def _handle_key(self, event: events.Key) -> bool:
        if self._terminal_mode == self._TerminalMode.MAXIMIZED:
            return False

        if not self._left_panel.has_focus and not self._right_panel.has_focus:
            return False  # special handling only when a pane has focus

        match event.key:
            case "tab":
                await self.action_toggle_panels()
                return True

            case "shift+tab":
                await self._terminal.on_key(events.Key("tab", character="\t"))
                return True

            case "enter":
                if self._terminal_waits_for_enter:
                    self._terminal_waits_for_enter = False
                    await self._terminal.on_key(event)
                    return True

            case "ctrl+down":
                path = self.active_panel().path_item_under_cursor
                await self._terminal.send(f"{path.name}")
                return True

            case "ctrl+shift+down":
                path = self.active_panel().path_item_under_cursor
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
            mapped_key_event = events.Key(mapped_key, character=event.character)
            await self._terminal.on_key(mapped_key_event)
            return True

        if event.is_printable:
            self._terminal_waits_for_enter = True
            await self._terminal.on_key(event)
            return True

        return False

    async def action_toggle_panels(self) -> None:
        self.log("Toggling panels")
        if self._last_active_panel == self._left_panel:
            self._right_panel.focus()
            self._last_active_panel = self._right_panel
        else:
            self._left_panel.focus()
            self._last_active_panel = self._left_panel
        await self._set_terminal_directory(self._last_active_panel.path)

    def action_toggle_maximized_terminal(self) -> None:
        if self._terminal_mode == self._TerminalMode.MAXIMIZED:
            self._terminal_mode = self._TerminalMode.MINIMIZED
            self.active_panel().focus()
        else:
            self._terminal_mode = self._TerminalMode.MAXIMIZED
            self._terminal.focus()
        self._resize_terminal()

    def action_toggle_terminal(self) -> None:
        if self._terminal_mode == self._TerminalMode.ENLARGED:
            self._terminal_mode = self._TerminalMode.MINIMIZED
            self.active_panel().focus()
        else:
            self._terminal_mode = self._TerminalMode.ENLARGED
            self.active_panel().focus()
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

    async def _set_terminal_directory(self, path: VPath) -> None:
        # assert isinstance(path, LocalPath), "Only local paths are supported at the moment"
        if not isinstance(path.filesystem, LocalFilesystem):
            return
        await self._terminal.send(shell_clear_prompt() + " " + shell_cmd_cd(path.path) + "\n")

    async def _on_directory_browser_path_selected(self, event: DirectoryBrowser.PathSelected) -> None:
        vpath = event.path
        await self._open_path(vpath, event.browser)

    async def _on_directory_browser_item_changed(self, event: DirectoryBrowser.ItemChanged) -> None:
        self._update_actions(event.path)

    def _on_terminal_pre_cmd(self, event: Terminal.PreCmd) -> None:
        # TODO handle non-local paths
        self.active_panel().set_path(VPath(event.cwd, LocalFilesystem.singleton()))

    # jobs and tasks

    async def request_callback(
        self,
        request: DecisionRequest,
        future: asyncio.Future[Decision],
    ) -> None:
        dialog = DecisionDialog(request.message)
        result = await dialog.run()
        future.set_result(result)

    @work
    async def action_copy_or_move_files(self, move: bool) -> None:
        source_paths = list(self.active_panel().selected_path_items)
        destination_path = self.other_panel().path

        job = await copy_or_move_files_job(
            src_paths=source_paths,
            dst_path=destination_path,
            move=move,
        )
        if job is not None:
            await job.start(self.request_callback)

        # operation = await copy_or_move_files_job(
        #     src_paths=source_paths,
        #     dst_path=destination_path,
        #     move=move,
        # )
        # if operation is not None:
        #     self.append_operation(operation)

    # @work
    # async def action_delete_files(self) -> None:
    #     paths = list(self.active_panel().selected_path_items)

    #     operation = await delete_files_operation(
    #         paths=paths,
    #     )
    #     if operation is not None:
    #         self.append_operation(operation)

    def on_bookmarks_dialog_bookmark_selected(self, event: BookmarksDialog.BookmarkSelected) -> None:
        vpath = vfspath_from_uri(event.bookmark_path)
        self.active_panel().set_path(vpath)
        # self._last_active_pane.focus()

    def _update_actions(self, path: VPath | None) -> None:
        class AKey(NamedTuple):
            is_empty: bool | None = None
            is_directory: bool | None = None
            is_file: bool | None = None
            is_executable: bool | None = None
            is_path_in_clipboard: bool | None = None

            def matches(self, other: AKey) -> bool:
                if self.is_empty is not None and self.is_empty == other.is_empty:
                    return True
                if self.is_directory is not None and self.is_directory == other.is_directory:
                    return True
                if self.is_file is not None and self.is_file == other.is_file:
                    return True
                if self.is_executable is not None and self.is_executable == other.is_executable:
                    return True
                if self.is_path_in_clipboard is not None and self.is_path_in_clipboard == other.is_path_in_clipboard:
                    return True

                if all(  # noqa: SIM103
                    v is None
                    for v in (
                        self.is_empty,
                        self.is_directory,
                        self.is_file,
                        self.is_executable,
                        self.is_path_in_clipboard,
                    )
                ):
                    return True  # matches anything

                return False

        actions: list[tuple[AKey, str]] = [
            (AKey(is_directory=True, is_file=True), "file.open"),
            (AKey(is_directory=True), "file.open_in_other_panel"),
            (AKey(is_file=True), "file.edit"),
            (AKey(is_directory=True, is_file=True), "file.cut"),
            (AKey(is_directory=True, is_file=True), "file.copy"),
            (AKey(is_directory=True, is_file=True), "file.copy_names"),
            (AKey(is_path_in_clipboard=True), "file.paste"),
            (AKey(is_directory=True, is_file=True), "file.delete"),
            (AKey(is_directory=True, is_file=True), "file.rename"),
        ]

        for key, action_name in actions:
            a = self._act(action_name)
            a.set_enabled(
                key.matches(
                    AKey(
                        is_empty=path is None,
                        is_directory=path is not None and path.stat.is_directory,
                        is_file=path is not None and not path.stat.is_directory,
                        is_executable=path is not None and path.stat.is_executable and not path.stat.is_directory,
                    )
                )
            )

    @work
    async def on_directory_browser_context_menu(self, event: DirectoryBrowser.ContextMenu) -> None:
        items: list[tuple[str, int]]

        if event.path is None:
            items = [
                ("file.new", 0),
                ("view.show_hidden_files", 6),
            ]
        else:
            items = [
                ("file.open", 2),
                ("file.open_in_other_panel", 2),
                ("file.edit", 2),
                ("file.cut", 3),
                ("file.copy", 3),
                ("file.copy_names", 4),
                ("file.paste", 4),
                ("file.delete", 5),
                ("file.rename", 5),
                ("view.show_hidden_files", 6),
            ]

        menu = Menu()
        last_group = None
        for action_name, group in items:
            a = self._act(action_name)
            if not a.enabled:
                continue

            if last_group and group != last_group:
                menu.add_separator()
            last_group = group
            menu.add(a)

        res = await menu.exec()
        if res is not None:
            await self._run_action(res)

    # region -------------------- Actions --------------------

    # TODO: this is a current workaround for invoking actions from menus
    #       the menu system should run actions directly instead
    async def _run_action(self, action: Action) -> None:
        if action.action:
            await self.app.run_action(action.action, self)

    async def _on_menu_triggered(self, event: Menu.Triggered) -> None:
        if event.action and event.action:
            await self._run_action(event.action)

    def _action_toggle_hidden(self) -> None:
        a = self._act("view.show_hidden_files")
        a.set_checked(not a.checked)
        self._action_show_hidden_files()

    def _action_show_hidden_files(self) -> None:
        a = self._act("view.show_hidden_files")
        self._left_panel.show_hidden_files = a.checked
        self._right_panel.show_hidden_files = a.checked

    async def _action_open_editor(self) -> None:
        path = self.active_panel().path_item_under_cursor
        self._open_editor(path)

    async def _action_open_path(self) -> None:
        path = self.active_panel().path_item_under_cursor
        await self._open_path(path, self.active_panel())

    async def _action_open_in_other_panel(self) -> None:
        path = self.active_panel().path_item_under_cursor
        self.other_panel().set_path(path)

    async def _action_show_bookmarks(self) -> None:
        self._bookmark_dialog = BookmarksDialog(position=(2, 2))
        await self.mount(self._bookmark_dialog)
        self._bookmark_dialog.focus()

    async def _action_filter(self) -> None:
        await self.active_panel().action_filter()

    async def _action_toggle_selection_under_cursor(self) -> None:
        self.active_panel().action_toggle_selection_under_cursor()

    async def _action_select_all(self) -> None:
        self.active_panel().action_select_all()

    async def _action_select_none(self) -> None:
        self.active_panel().action_select_none()

    async def _action_invert_selection(self) -> None:
        self.active_panel().action_invert_selection()

    # async def _action_show_processes(self) -> None:
    #     processes_dialog = ProcessesDialog(position=(4, 4), operations=self._operations)
    #     await self.mount(processes_dialog)
    #     processes_dialog.focus()

    # endregion

    # region -------------------- Processing --------------------

    @work
    async def _open_editor(self, path: VPath) -> None:
        editor_screen = Editor()
        self.app.push_screen(editor_screen)
        editor_screen.open(path)

    async def _open_path(self, path: VPath, browser: DirectoryBrowser) -> None:
        if path.stat.is_directory:
            await self._set_terminal_directory(path)
            return

        if archive.is_supported_archive(path.path):
            archive_path = VPath("/", ArchiveFilesystem(archive_parent=path.parent, archive=path))
            browser.set_path(archive_path)
            return

        if path.stat.is_executable:
            mimetype = path.guess_mimetype()
            # if mimetype matches ".*/x-.*", run it directly
            if mimetype is None or re.match(r".*/x-.*$", mimetype) is not None:
                self._execute_command(args=[path.path.as_posix()], cwd=path.parent.path)
                return

        # open file with xdg-open
        open_cmd = conf_.filetypes.get_open_command_for_file_path(path.path)
        self._execute_command(args=open_cmd, cwd=path.parent.path)

    def _execute_command(self, args: list[str], cwd: PurePath) -> None:
        with self.app.suspend():
            process = subprocess.Popen(args=args, cwd=cwd)
            process.wait()

    # def _action_suspend(self) -> None:
    #     with self.app.suspend():
    #         sleep(10)


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
    conf_.load_all_configs()
    ICONS.load_icons(get_config_file_path("icons.csv"))
    ICONS.set_variant(IconSet.Variants.NERDFONT)
    set_icon_provider(ICONS.get_icon)
    SYMBOL_TABLE["checkbox"] = (ICONS.get_icon("checkbox"), ICONS.get_icon("checkbox_checked"))
    SYMBOL_TABLE["radio"] = (ICONS.get_icon("radio"), ICONS.get_icon("radio_checked"))

    register_common_schemes()

    NovaNavigator().run()
    # global_config.write_all_configs()


if __name__ == "__main__":
    main()
