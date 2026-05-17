from __future__ import annotations

import asyncio
import copy
import logging
import subprocess
import sys
from enum import Enum
from pathlib import PurePath
from typing import ClassVar, NamedTuple, cast

from textual import events, work
from textual.app import App, ComposeResult
from textual.binding import Binding
from textual.containers import Horizontal
from textual.logging import TextualHandler
from textual.screen import Screen
from textual.widgets import Input

from nova_navigator import debug_analytics
from nova_navigator.config import conf_
from nova_navigator.dialogs import (
    BookmarksDialog,
    ConnectToDialog,
    EditBookmarksDialog,
    EditRemotesDialog,
    JobsDialog,
)
from nova_navigator.dialogs.constants import DEFAULT_BOOKMARKS_GROUP
from nova_navigator.dialogs.response_dialog import make_response_dialog
from nova_navigator.dialogs.settings_dialog import SettingsDialog
from nova_navigator.editor import Editor
from nova_navigator.filemanager.jobs import copy_or_move_files_job, delete_files_job
from nova_navigator.filemanager.tasks import dummy_task
from nova_navigator.nova_navigator_core import (
    NovaNavigatorCore,
    PanelRef,
)
from nova_navigator.remotes.azure import connect_azure
from nova_navigator.remotes.ssh import connect_ssh
from nova_navigator.response import Response
from nova_navigator.scheduler import Job, ResponseRequest
from nova_navigator.terminal import Terminal
from nova_navigator.vfs import VPath
from nova_navigator.vfs.filesystems import LocalFilesystem
from nova_navigator.vfs.scheme_registry import vfspath_from_uri
from nova_navigator.widgets import DirectoryBrowser, Footer, JobStatusIcon
from nova_widgets.menu import Action, Menu, MenuBar
from nova_widgets.menu import constructor as mc

logging.basicConfig(
    level="INFO",
    handlers=[TextualHandler()],
)

_logger = logging.getLogger("nova_navigator.nova_navigator")


class CommandInput(Input):
    pass


class MainScreen(Screen[None]):
    BINDINGS: ClassVar = [
        Binding("^q", "quit", "Quit"),
        Binding("ctrl+o", "toggle_maximized_terminal", "Maximize Terminal", priority=True),
        Binding("ctrl+l", "toggle_terminal", "Enlarge Terminal", priority=True),
        Binding("f4", "open_editor", "Edit"),
        Binding("f5", "copy_or_move_files(False)", "Copy"),
        Binding("f6", "copy_or_move_files(True)", "Move"),
        Binding("f8", "delete_files", "Delete"),
        Binding("ctrl+b", "show_bookmarks", "Bookmark"),
        Binding("ctrl+h", "toggle_hidden", description="Show/Hide Hidden Files", show=False),
        Binding("ctrl+d", "start_dummy_operation", "Start Dummy Operation"),
        Binding("ctrl+g", "go_to_path", "Go to Path", show=False),
        Binding("alt+left", "go_back", "Go Back", show=False),
        Binding("alt+right", "go_forward", "Go Forward", show=False),
        Binding("alt+up", "go_up", "Go Up"),
        Binding("ctrl+shift+g", "connect_to", "Connect to Remote", show=False),
        Binding("ctrl+r", "refresh", "Refresh", show=False, priority=True),
        Binding("ctrl+f1", "settings", "Settings", show=False),
    ]

    class _TerminalMode(Enum):
        MINIMIZED = 0
        ENLARGED = 1
        MAXIMIZED = 2

    _left_panel: DirectoryBrowser
    _right_panel: DirectoryBrowser
    _terminal: Terminal
    _terminal_mode: _TerminalMode
    _last_active_panel: DirectoryBrowser

    _bookmark_dialog: BookmarksDialog
    _jobs_dialog: JobsDialog
    _job_status_icon: JobStatusIcon

    def __init__(self) -> None:
        super().__init__()
        self._terminal_mode = self._TerminalMode.MINIMIZED

    @property
    def app(self) -> NovaNavigator:  # type: ignore[override]
        return cast("NovaNavigator", super().app)

    def compose(self) -> ComposeResult:
        self._menu_bar = MenuBar()

        self._menu_bar.add_menu(
            "𑁔",
            mc.action("Settings", icon="gear", shortcut="Ctrl+F1", action="settings"),
            mc.separator(),
            mc.action("About", icon="info"),
            mc.separator(),
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
            mc.action("Follow Symlink", shortcut="Shift+Enter", action="follow_symlink", name="follow_symlink"),
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
            mc.action("Select By Pattern…", name="select_by_pattern"),
        )

        self._menu_bar.add_menu("Go", name="go").add(
            mc.action("Go to Path…", shortcut="Ctrl+G", action="go_to_path", name="go_to_path"),
            mc.separator(),
            mc.action("Go Back", shortcut="Alt+Left", action="go_back", name="go_back"),
            mc.action("Go Forward", shortcut="Alt+Right", action="go_forward", name="go_forward"),
            mc.action("Go Up", shortcut="Alt+Up", action="go_up", name="go_up"),
            mc.separator(),
            mc.action("Connect to…", shortcut="Ctrl+Shift+G", action="connect_to", name="connect_to"),
            mc.separator(),
            mc.action("Manage Remotes…", action="manage_remotes", name="manage_remotes"),
        )

        self._menu_bar.add_menu("Bookmarks", name="bookmarks").add(
            mc.action("Show Bookmarks", shortcut="Ctrl+B", action="show_bookmarks", name="show_bookmarks"),
            mc.separator(),
            mc.action("Add to Bookmarks", action="add_to_bookmarks", name="add_to_bookmarks"),
            mc.action("Manage Bookmarks", action="edit_bookmarks", name="edit_bookmarks"),
        )
        self._menu_bar.add_menu("View", name="view").add(
            mc.action("Refresh", shortcut="Ctrl+R", action="refresh", name="refresh"),
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

        self._job_status_icon = JobStatusIcon(
            registry=self.app.job_registry,
            action="show_processes",
        )
        self._menu_bar.add_right_widget(self._job_status_icon)

        yield self._menu_bar

        self._left_panel = DirectoryBrowser(id="pane-left", path=LocalFilesystem.singleton().cwd())
        self._last_active_panel = self._left_panel

        self._right_panel = DirectoryBrowser(id="pane-right", path=LocalFilesystem.singleton().cwd())
        yield Horizontal(
            self._left_panel,
            self._right_panel,
        )

        self._terminal = Terminal("/usr/bin/zsh", id="terminal", keep_alive=True)
        self._terminal.styles.height = 1
        self._terminal.start()

        yield self._terminal
        self._jobs_dialog = JobsDialog(position=(0, 0), registry=self.app.job_registry)
        yield self._jobs_dialog
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

    def _resolve_panel(self, panel: PanelRef) -> DirectoryBrowser:
        match panel:
            case PanelRef.LEFT:
                return self._left_panel
            case PanelRef.RIGHT:
                return self._right_panel
            case PanelRef.ACTIVE:
                return self._last_active_panel

    def on_resize(self, event: events.Resize) -> None:
        self._resize_terminal()
        if self._jobs_dialog.display:
            self._jobs_dialog._update_position()

    def _action_quit(self) -> None:
        self.app.exit()

    async def on_event(self, event: events.Event) -> None:
        if isinstance(event, events.Key):  # noqa: SIM102
            # recover Ctrl+H (the "real" backspace has character \x7f)
            if event.key == "backspace" and event.character == "\x08":
                event.key = "ctrl+h"

        await super().on_event(event)

    async def _on_key(self, event: events.Key) -> None:
        # self.log(f"MainScreen _on_key: {event.key}")
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
                if self._terminal.has_input():
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
        self._set_terminal_directory(self._last_active_panel.path)

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

    def _set_terminal_directory(self, path: VPath) -> None:
        if not isinstance(path.filesystem, LocalFilesystem):
            return
        self._terminal.request_cd(path.path)

    async def _on_directory_browser_path_selected(self, event: DirectoryBrowser.PathSelected) -> None:
        vpath = event.path
        await self._open_path(vpath, event.browser)

    def _on_directory_browser_path_changed(self, event: DirectoryBrowser.PathChanged) -> None:
        self._set_terminal_directory(event.path)

    async def _on_directory_browser_item_changed(self, event: DirectoryBrowser.ItemChanged) -> None:
        self._update_actions(event.path)

    def _on_terminal_path_changed(self, event: Terminal.PathChanged) -> None:
        if event.user_initiated:
            self.active_panel().set_path(VPath(event.cwd, LocalFilesystem.singleton()))

    # jobs and tasks

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
            self.app.job_registry.add_job(job)
            await job.start(self.app.request_callback)

    @work
    async def action_delete_files(self) -> None:
        paths = list(self.active_panel().selected_path_items)

        job = await delete_files_job(
            paths=paths,
        )
        if job is not None:
            self.app.job_registry.add_job(job)
            await job.start(self.app.request_callback)

    @work
    async def action_start_dummy_operation(self) -> None:
        job = Job("Dummy Operation", dummy_task)
        self.app.job_registry.add_job(job)
        await job.start(self.app.request_callback)

    async def on_bookmarks_dialog_bookmark_selected(self, event: BookmarksDialog.BookmarkSelected) -> None:
        vpath = vfspath_from_uri(event.bookmark_path)
        _logger.info("Bookmark selected: %s", vpath)
        self.active_panel().set_path(vpath)

    def _update_actions(self, path: VPath | None) -> None:
        class AKey(NamedTuple):
            is_empty: bool | None = None
            is_directory: bool | None = None
            is_file: bool | None = None
            is_executable: bool | None = None
            is_path_in_clipboard: bool | None = None
            is_symlink: bool | None = None

            def matches(self, other: AKey) -> bool:
                if self.is_empty == other.is_empty:
                    return True
                if self.is_directory is not None and self.is_directory == other.is_directory:
                    return True
                if self.is_file is not None and self.is_file == other.is_file:
                    return True
                if self.is_executable is not None and self.is_executable == other.is_executable:
                    return True
                if self.is_path_in_clipboard is not None and self.is_path_in_clipboard == other.is_path_in_clipboard:
                    return True
                if self.is_symlink is not None and self.is_symlink == other.is_symlink:
                    return True

                if all(  # noqa: SIM103
                    v is None
                    for v in (
                        self.is_empty,
                        self.is_directory,
                        self.is_file,
                        self.is_executable,
                        self.is_path_in_clipboard,
                        self.is_symlink,
                    )
                ):
                    return True  # matches anything

                return False

        actions: list[tuple[AKey, str]] = [
            (AKey(is_directory=True, is_file=True), "file.open"),
            (AKey(is_directory=True), "file.open_in_other_panel"),
            (AKey(is_symlink=True), "file.follow_symlink"),
            (AKey(is_file=True), "file.edit"),
            (AKey(is_directory=True, is_file=True), "file.cut"),
            (AKey(is_directory=True, is_file=True), "file.copy"),
            (AKey(is_directory=True, is_file=True), "file.copy_names"),
            (AKey(is_path_in_clipboard=True), "file.paste"),
            (AKey(is_directory=True, is_file=True), "file.delete"),
            (AKey(is_directory=True, is_file=True), "file.rename"),
            (AKey(is_directory=True, is_file=False, is_empty=True), "bookmarks.add_to_bookmarks"),
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
                        is_symlink=path is not None and path.stat.is_symlink,
                    )
                )
            )

    @work
    async def on_directory_browser_context_menu(self, event: DirectoryBrowser.ContextMenu) -> None:
        items: list[tuple[str, int]]

        self._update_actions(event.path)
        # if event.path is None:
        #     items = [
        #         ("file.new", 0),
        #         ("view.show_hidden_files", 6),
        #     ]
        # else:
        items = [
            ("file.new", 0),
            ("file.open", 2),
            ("file.open_in_other_panel", 2),
            ("file.follow_symlink", 2),
            ("file.edit", 2),
            ("file.cut", 3),
            ("file.copy", 3),
            ("file.copy_names", 4),
            ("file.paste", 4),
            ("file.delete", 5),
            ("file.rename", 5),
            ("bookmarks.add_to_bookmarks", 6),
            ("view.show_hidden_files", 7),
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
        await self.app.open_editor(path)

    async def _action_open_path(self) -> None:
        path = self.active_panel().path_item_under_cursor
        await self._open_path(path, self.active_panel())

    async def _action_open_in_other_panel(self) -> None:
        path = self.active_panel().path_item_under_cursor
        self.other_panel().set_path(path)

    def _action_follow_symlink(self) -> None:
        self.active_panel().action_follow_symlink()

    async def _action_show_bookmarks(self) -> None:
        panel = self.active_panel()
        region = panel.region
        self._bookmark_dialog = BookmarksDialog(position=(region.x + 1, region.y + 1))
        await self.mount(self._bookmark_dialog)
        self._bookmark_dialog.focus()

    def _action_go_back(self) -> None:
        self.active_panel().go_back()

    def _action_go_forward(self) -> None:
        self.active_panel().go_forward()

    async def _action_go_up(self) -> None:
        panel = self.active_panel()
        parent_path = panel.path.parent
        if parent_path is not None:
            await self._open_path(parent_path, panel)

    @work
    async def _action_settings(self) -> None:
        dialog = SettingsDialog(copy.deepcopy(conf_.settings))
        if await dialog.run() == Response.OK:
            conf_.settings = dialog.config
            conf_.settings.save()

    @work
    async def _action_edit_bookmarks(self) -> None:
        dialog = EditBookmarksDialog(copy.deepcopy(conf_.bookmarks))
        if await dialog.run() == Response.OK:
            conf_.bookmarks = dialog.config
            conf_.bookmarks.save()

    @work
    async def _action_manage_remotes(self) -> None:
        dialog = EditRemotesDialog(copy.deepcopy(conf_.remotes))
        if await dialog.run() == Response.OK:
            conf_.remotes = dialog.config
            conf_.remotes.save()

    @work
    async def _action_connect_to(self) -> None:
        dialog = ConnectToDialog(conf_.remotes)
        result = await dialog.run()
        if result != Response.OK:
            return
        conn = dialog.selected_connection
        if conn is None:
            return
        if conn.ssh is not None and conn.ssh.host:
            fs = await connect_ssh(conn)
        elif conn.azure is not None and conn.azure.account_url:
            fs = await connect_azure(conn)
        else:
            _logger.warning(
                "Connection %r has no usable settings (ssh.host=%r, azure.account_url=%r)",
                conn.name,
                conn.ssh.host if conn.ssh else None,
                conn.azure.account_url if conn.azure else None,
            )
            return
        if fs is None:
            return
        start_path = await asyncio.to_thread(fs.cwd)
        self.active_panel().set_path(start_path)

    @work
    async def _action_add_to_bookmarks(self) -> None:
        path = self.active_panel().path_item_under_cursor
        if path is None:
            return
        dialog = EditBookmarksDialog(
            copy.deepcopy(conf_.bookmarks),
            prefill=(DEFAULT_BOOKMARKS_GROUP, path.name, str(path.path)),
        )
        if await dialog.run() == Response.OK:
            conf_.bookmarks = dialog.config
            conf_.bookmarks.save()

    def _action_refresh(self) -> None:
        self._left_panel.reload()
        self._right_panel.reload()

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

    def action_show_processes(self) -> None:
        self._jobs_dialog.show()
        self._jobs_dialog.focus()

    # endregion

    # region -------------------- Processing --------------------

    async def _open_path(self, path: VPath, browser: DirectoryBrowser) -> None:
        panel = PanelRef.LEFT if browser is self._left_panel else PanelRef.RIGHT
        await self.app.open_path(path, panel)

    # endregion


class NovaNavigator(NovaNavigatorCore, App[None]):
    """Nova Navigator App."""

    CSS_PATH = "nn.tcss"
    ENABLE_COMMAND_PALETTE = False
    _main_screen: MainScreen

    def __init__(self) -> None:
        super().__init__()

    def action_help_quit(self) -> None:
        pass

    def _handle_exception(self, error: Exception) -> None:
        sys.settrace(debug_analytics.trace_handler)
        debug_analytics.write_crash(error)
        raise error

    async def on_mount(self) -> None:
        debug_analytics.install()
        self.log("Starting Nova Navigator...")
        self._main_screen = MainScreen()
        self.install_screen(self._main_screen, "main_screen")
        self.push_screen("main_screen")

    async def open_editor(self, path: VPath) -> None:
        editor_screen = Editor()
        self.push_screen(editor_screen)
        editor_screen.open(path)

    async def execute_command(self, args: list[str], cwd: PurePath) -> None:
        with self.suspend():
            process = subprocess.Popen(args=args, cwd=cwd)  # noqa: ASYNC220
            process.wait()

    async def set_panel_directory(self, path: VPath, panel: PanelRef) -> None:
        self._main_screen._resolve_panel(panel).set_path(path)

    async def request_callback(
        self,
        request: ResponseRequest,
        future: asyncio.Future[Response],
    ) -> None:
        _logger.warning(request.expected_responses)
        dialog = make_response_dialog(request)
        result = await dialog.run()
        future.set_result(result)
