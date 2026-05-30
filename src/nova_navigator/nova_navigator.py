from __future__ import annotations

import asyncio
import copy
import importlib.metadata
import logging
import os
import subprocess
import sys
from dataclasses import dataclass, field
from enum import Enum
from pathlib import Path, PurePath, PurePosixPath
from typing import ClassVar, NamedTuple, cast

from textual import events, work
from textual.app import App, ComposeResult
from textual.containers import Horizontal
from textual.logging import TextualHandler
from textual.screen import Screen
from textual.widgets import Input

from nova_navigator import debug_analytics
from nova_navigator.clipboard import ClipboardOperation, PathClipboard
from nova_navigator.config import conf_
from nova_navigator.dialogs import (
    BookmarksDialog,
    ConnectToDialog,
    EditBookmarksDialog,
    EditRemotesDialog,
    InputNameDialog,
    JobsDialog,
    MessageBox,
)
from nova_navigator.dialogs.constants import DEFAULT_BOOKMARKS_GROUP
from nova_navigator.dialogs.dialog import ButtonSpec
from nova_navigator.dialogs.keybindings_dialog import KeybindingsDialog
from nova_navigator.dialogs.response_dialog import make_response_dialog
from nova_navigator.dialogs.settings_dialog import SettingsDialog
from nova_navigator.editor import Editor
from nova_navigator.filemanager.compare import CompareMode, compare_directories
from nova_navigator.filemanager.jobs import copy_or_move_files_job, delete_files_job
from nova_navigator.filemanager.tasks import dummy_task
from nova_navigator.keymap import KeybindingsConfig
from nova_navigator.nova_navigator_core import (
    NovaNavigatorCore,
    PanelRef,
)
from nova_navigator.plugins import PluginRegistry
from nova_navigator.remotes.azure import AZURE_PLUGIN
from nova_navigator.remotes.remote import RemoteConnector, register_remote_scheme
from nova_navigator.remotes.ssh import SSH_PLUGIN
from nova_navigator.response import Response
from nova_navigator.runtime_patches import apply_runtime_patches
from nova_navigator.scheduler import Job, ResponseRequest
from nova_navigator.terminal import Terminal, TerminalPool
from nova_navigator.vfs import VPath
from nova_navigator.vfs.filesystems import LocalFilesystem
from nova_navigator.vfs.parse_uri import parse_uri
from nova_navigator.vfs.scheme_registry import SCHEME_REGISTRY, vfspath_from_uri
from nova_navigator.widgets import DirectoryBrowser, JobStatusIcon
from nova_navigator.widgets.directory_browser import GoToPathWidget, UpPath
from nova_widgets.actions_support import ActionsSupport
from nova_widgets.keymap import HintBar, HintsChanged, KeymapRegistry
from nova_widgets.menu import Action, Menu, MenuBar
from nova_widgets.menu import constructor as mc

logging.basicConfig(
    level="INFO",
    handlers=[TextualHandler()],
)

_logger = logging.getLogger("nova_navigator.nova_navigator")


class CommandInput(Input):
    pass


@dataclass
class SyncBrowsing:
    """State for synchronized browsing mode."""

    left_base: VPath
    right_base: VPath
    left_prev: VPath = field(init=False)
    right_prev: VPath = field(init=False)

    def __post_init__(self) -> None:
        self.left_prev = self.left_base
        self.right_prev = self.right_base


@dataclass
class _CompareConfig:
    """State for Compare Directories mode."""

    mode: CompareMode | None  # None = name-presence only


class MainScreen(ActionsSupport, Screen[None]):
    ACTIONS: ClassVar[list[Action]] = [
        Action("Quit", id="app.quit", action="quit", description="Quit Nova Navigator", shortcut="ctrl+q", show=True, bar_priority=90),
        Action("Maximize Terminal", id="app.toggle_maximized_terminal", action="toggle_maximized_terminal", description="Toggle terminal full-screen mode", shortcut="ctrl+o", show=False),
        Action("Enlarge Terminal", id="app.toggle_terminal", action="toggle_terminal", description="Enlarge the terminal panel", shortcut="ctrl+l", show=False),
        Action("Rename", id="browser.rename", action="rename", description="Rename the file or directory under the cursor", shortcut="f2", show=True, bar_priority=10),
        Action("Edit", id="browser.open_editor", action="open_editor", description="Open the file under the cursor in an editor", shortcut="f4", show=True, bar_priority=15),
        Action("Copy", id="browser.copy", action="copy_or_move_files(False)", description="Copy selected files to the other panel", shortcut="f5", show=True, bar_priority=20),
        Action("Move", id="browser.move", action="copy_or_move_files(True)", description="Move selected files to the other panel", shortcut="f6", show=True, bar_priority=25),
        Action("Directory", id="browser.new_directory", action="new_directory", description="Create a new directory", shortcut="f7", show=True, bar_priority=30, icon="folder"),
        Action("Delete", id="browser.delete", action="delete_files", description="Delete selected files", shortcut="f8", show=True, bar_priority=35),
        Action("Bookmarks", id="app.show_bookmarks", action="show_bookmarks", description="Open bookmarks dialog", shortcut="ctrl+b", show=True, bar_priority=80),
        Action("Hidden Files", id="browser.toggle_hidden", action="toggle_hidden", description="Toggle display of hidden files", shortcut="ctrl+h", show=False),
        Action("Dummy Op", id="app.start_dummy_operation", action="start_dummy_operation", description="Start dummy operation (development)", shortcut="ctrl+d", show=False),
        Action("Go to Path…", id="browser.go_to_path", action="go_to_path", description="Navigate to a typed path", shortcut="ctrl+g", show=False),
        Action("Settings", id="app.settings", action="settings", description="Open application settings", shortcut="ctrl+f1", show=False, icon="gear"),
        Action("Connect to…", id="app.connect_to", action="connect_to", description="Connect to a remote server", shortcut="ctrl+shift+g", show=False),
        Action("Refresh", id="browser.refresh", action="refresh", description="Refresh the file list", shortcut="ctrl+r", show=False),
        Action("Go Back", id="browser.go_back", action="go_back", description="Navigate back in history", shortcut="alt+left", show=False),
        Action("Go Forward", id="browser.go_forward", action="go_forward", description="Navigate forward in history", shortcut="alt+right", show=False),
        Action("Go Up", id="browser.go_up", action="go_up", description="Navigate to the parent directory", shortcut="alt+up", show=False),
        Action("Follow Symlink", id="browser.follow_symlink", action="follow_symlink", description="Follow symlink to destination", shortcut="alt+down", show=False),
        Action("Key Bindings…", id="app.keybindings", action="keybindings", description="Open keybinding editor", show=False, icon="keyboard"),
        Action("Invert Selection", id="selection.invert", action="invert_selection", description="Invert the current selection", shortcut="ctrl+s i", show=True),
        Action("Select All", id="selection.select_all", action="select_all", description="Select all items in the current directory", shortcut="ctrl+s a", show=True),
        Action("Select None", id="selection.select_none", action="select_none", description="Deselect all items in the current directory", shortcut="ctrl+s n", show=True),
        # View toggles (checkable)
        Action("Show Hidden Files", id="view.show_hidden_files", action="show_hidden_files", description="Toggle display of hidden files", checkable=True, checked=False, show=False),
        Action("Synchronized Browsing", id="view.sync_browsing", action="toggle_sync_browsing", description="Mirror navigation between panels", checkable=True, show=False),
        Action("Enable Compare", id="view.compare_enable", action="toggle_compare_enable", description="Enable directory comparison", checkable=True, show=False),
        # File operations (clipboard / open — no keyboard shortcut)
        Action("New File", id="file.new_file", action="new_file", description="Create a new file", show=False, icon="text"),
        Action("Open", id="file.open", action="open_path", description="Open the item under the cursor", show=False),
        Action("Open in Other Panel", id="file.open_in_other_panel", action="open_in_other_panel", description="Open item in the other panel", show=False),
        Action("Cut", id="file.cut", action="cut", description="Cut to clipboard", show=False),
        Action("Copy", id="file.copy", action="copy", description="Copy to clipboard", show=False),
        Action("Copy Names", id="file.copy_names", action="copy_names", description="Copy file names to clipboard", show=False),
        Action("Paste", id="file.paste", action="paste", description="Paste from clipboard", show=False),
        Action("Add to Bookmarks", id="bookmarks.add_to_bookmarks", action="add_to_bookmarks", description="Add current item to bookmarks", show=False),
    ]

    class _TerminalMode(Enum):
        MINIMIZED = 0
        ENLARGED = 1
        MAXIMIZED = 2

    _left_panel: DirectoryBrowser
    _right_panel: DirectoryBrowser
    _terminal_pool: TerminalPool
    _terminal_mode: _TerminalMode
    _last_active_panel: DirectoryBrowser

    _bookmark_dialog: BookmarksDialog
    _jobs_dialog: JobsDialog
    _job_status_icon: JobStatusIcon

    _sync_state: SyncBrowsing | None
    _compare_config: _CompareConfig | None

    def __init__(self, config_dir: Path | None = None) -> None:
        super().__init__()
        self._terminal_mode = self._TerminalMode.MINIMIZED
        self._sync_state = None
        self._compare_config = None
        self._terminal_pool = TerminalPool()
        self._provisioning: set[int] = set()
        self._keymap_config = KeybindingsConfig(config_dir=config_dir)
        self._keymap_registry: KeymapRegistry | None = None
        self._hint_bar = HintBar()

    @property
    def app(self) -> NovaNavigator:  # type: ignore[override]
        return cast("NovaNavigator", super().app)

    def compose(self) -> ComposeResult:
        self._menu_bar = MenuBar()

        self._menu_bar.add_menu(
            "𑁔",
            self._act("app.settings"),
            self._act("app.keybindings"),
            mc.separator(),
            mc.action("About", icon="info", action="about"),
            mc.separator(),
            self._act("app.quit"),
        )

        self._menu_bar.add_menu("File", name="file").add(
            mc.menu(
                "New",
                self._act("browser.new_directory"),
                self._act("file.new_file"),
                name="new",
            ),
            mc.separator(),
            self._act("file.open"),
            self._act("file.open_in_other_panel"),
            self._act("browser.follow_symlink"),
            self._act("browser.open_editor"),
            mc.separator(),
            self._act("file.copy"),
            self._act("file.cut"),
            self._act("file.copy_names"),
            self._act("file.paste"),
            mc.separator(),
            self._act("browser.delete"),
            self._act("browser.rename"),
            mc.separator(),
            mc.action("Filter", action="filter", name="filter"),
        )

        self._menu_bar.add_menu("Selection", name="selection").add(
            self._act("selection.select_all"),
            self._act("selection.select_none"),
            self._act("selection.invert"),
            mc.separator(),
            mc.action(
                "Toggle Selection",
                name="toggle_selection",
                action="toggle_selection_under_cursor",
            ),
            mc.separator(),
            mc.action("Select By Pattern…", name="select_by_pattern"),
        )

        self._menu_bar.add_menu("Go", name="go").add(
            self._act("browser.go_to_path"),
            mc.separator(),
            self._act("browser.go_back"),
            self._act("browser.go_forward"),
            self._act("browser.go_up"),
            mc.separator(),
            self._act("app.connect_to"),
            mc.separator(),
            mc.action("Manage Remotes…", action="manage_remotes", name="manage_remotes"),
        )

        self._menu_bar.add_menu("Bookmarks", name="bookmarks").add(
            self._act("app.show_bookmarks"),
            mc.separator(),
            self._act("bookmarks.add_to_bookmarks"),
            mc.action("Manage Bookmarks", action="edit_bookmarks", name="edit_bookmarks"),
        )
        self._menu_bar.add_menu("View", name="view").add(
            self._act("browser.refresh"),
            mc.separator(),
            self._act("view.show_hidden_files"),
            mc.separator(),
            self._act("view.sync_browsing"),
            mc.menu(
                "Compare Directories",
                self._act("view.compare_enable"),
                mc.separator(),
                *mc.group(
                    mc.action("By File Size", checkable=True, action="compare_by_size", name="compare_by_size"),
                    mc.action(
                        "By Modification Time",
                        checkable=True,
                        action="compare_by_mtime",
                        name="compare_by_mtime",
                    ),
                ),
                mc.separator(),
                mc.action("Hide Identical Files", checkable=True),
                name="compare_directories",
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

        local_terminal = Terminal("/usr/bin/zsh", id="terminal", keep_alive=True)
        local_terminal.styles.height = 1
        local_terminal.start()
        self._terminal_pool.set_local(local_terminal)

        yield local_terminal
        self._jobs_dialog = JobsDialog(position=(0, 0), registry=self.app.job_registry)
        yield self._jobs_dialog
        yield self._hint_bar

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

    async def on_mount(self) -> None:
        self._keymap_registry = KeymapRegistry(self._hint_bar)
        self._reload_keymap()

    def _reload_keymap(self) -> None:
        assert self._keymap_registry is not None
        all_actions: list[Action] = list(type(self).ACTIONS)
        if hasattr(DirectoryBrowser, "ACTIONS"):
            all_actions.extend(DirectoryBrowser.ACTIONS)
        bindings = self._keymap_config.resolve(all_actions)
        style = conf_.settings.general.key_display_style
        self._keymap_registry.set_key_display_style(style)
        self._keymap_registry.reload(bindings, all_actions)

    def on_focus(self, event: events.Focus) -> None:
        if self._keymap_registry is not None:
            self._keymap_registry.on_focus_changed(self.app.focused)

    def on_hints_changed(self, event: HintsChanged) -> None:
        if self._keymap_registry is not None:
            self._keymap_registry.update_hint_priorities(event.widget, event.priorities)

    def on_resize(self, event: events.Resize) -> None:
        self._resize_terminal()
        if self._jobs_dialog.display:
            self._jobs_dialog._update_position()

    def _action_quit(self) -> None:
        self.app.exit()

    async def _on_key(self, event: events.Key) -> None:
        if await self._handle_key(event):
            event.stop()

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
                await self._terminal_pool.active_terminal.on_key(events.Key("tab", character="\t"))
                return True

            case "enter":
                if self._terminal_mode != self._TerminalMode.MINIMIZED and self._terminal_pool.active_terminal.has_input():
                    await self._terminal_pool.active_terminal.on_key(event)
                    return True

            case "ctrl+down":
                path = self.active_panel().path_item_under_cursor
                await self._terminal_pool.active_terminal.send(f"{path.name}")
                return True

            case "ctrl+shift+down":
                path = self.active_panel().path_item_under_cursor
                await self._terminal_pool.active_terminal.send(f"{path}")
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
            await self._terminal_pool.active_terminal.on_key(mapped_key_event)
            return True

        if event.is_printable:
            await self._terminal_pool.active_terminal.on_key(event)
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
        panel_id = PanelRef.LEFT.value if self._last_active_panel is self._left_panel else PanelRef.RIGHT.value
        self._switch_terminal(self._last_active_panel.path, panel_id)

    def action_toggle_maximized_terminal(self) -> None:
        if self._terminal_mode == self._TerminalMode.MAXIMIZED:
            self._terminal_mode = self._TerminalMode.MINIMIZED
            self.active_panel().focus()
        else:
            self._terminal_mode = self._TerminalMode.MAXIMIZED
            self._terminal_pool.active_terminal.focus()
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
        t = self._terminal_pool.active_terminal
        t.styles.width = int(self.size.width)
        match self._terminal_mode:
            case self._TerminalMode.MINIMIZED:
                t.styles.height = 1
            case self._TerminalMode.ENLARGED:
                t.styles.height = self.size.height // 2
            case self._TerminalMode.MAXIMIZED:
                t.styles.height = self.size.height - 2

    def _switch_terminal(self, path: VPath, panel_id: str = "") -> None:
        self._terminal_pool.switch_to(path.filesystem)
        self._terminal_pool.active_terminal.request_cd(path.path, panel_id)

    async def _on_directory_browser_path_selected(self, event: DirectoryBrowser.PathSelected) -> None:
        vpath = event.path
        if not vpath.stat.is_directory:
            await self._open_path(vpath, event.browser)

    @work
    async def _on_directory_browser_path_changed(self, event: DirectoryBrowser.PathChanged) -> None:
        await self._ensure_terminal_for(event.path)
        panel_id = PanelRef.LEFT.value if event.browser is self._left_panel else PanelRef.RIGHT.value
        self._switch_terminal(event.path, panel_id)
        if self._sync_state is not None:
            self._mirror_sync(event.browser, event.path)

    async def _ensure_terminal_for(self, path: VPath) -> None:
        """Provision and register a terminal for path.filesystem if not yet registered."""
        fs_key = id(path.filesystem.unwrap())
        if self._terminal_pool.has_terminal(path.filesystem) or fs_key in self._provisioning:
            return
        self._provisioning.add(fs_key)
        try:
            terminal = await self._terminal_pool.create_for(path.filesystem)
            if terminal is None:
                return
            self._terminal_pool.register(path.filesystem, terminal)
            await self.mount(terminal, after=self._terminal_pool.active_terminal)
            terminal.start()
        finally:
            self._provisioning.discard(fs_key)

    def on_directory_browser_load_failed(self, event: DirectoryBrowser.LoadFailed) -> None:
        self.notify(str(event.error), title="Cannot read directory", severity="error")

    def on_directory_browser_load_complete(self, event: DirectoryBrowser.LoadComplete) -> None:
        self._refresh_compare()

    def _refresh_compare(self) -> None:
        if self._compare_config is None:
            return
        if self._left_panel._loading or self._right_panel._loading:
            return
        left_colors, right_colors = compare_directories(
            self._left_panel.items,
            self._right_panel.items,
            self._compare_config.mode,
        )
        self._left_panel.set_item_colors(left_colors)
        self._right_panel.set_item_colors(right_colors)

    def _action_toggle_compare_enable(self) -> None:
        a = self._act("view.compare_enable")
        if a.checked:
            self._compare_config = _CompareConfig(mode=None)
            self._refresh_compare()
        else:
            self._compare_config = None
            self._left_panel.set_item_colors(None)
            self._right_panel.set_item_colors(None)

    def _action_compare_by_size(self) -> None:
        if self._compare_config is not None:
            self._compare_config.mode = CompareMode.BY_SIZE
            self._refresh_compare()

    def _action_compare_by_mtime(self) -> None:
        if self._compare_config is not None:
            self._compare_config.mode = CompareMode.BY_MODIFICATION_TIME
            self._refresh_compare()

    async def _on_directory_browser_item_changed(self, event: DirectoryBrowser.ItemChanged) -> None:
        self._update_actions(event.path)

    def _on_terminal_path_changed(self, event: Terminal.PathChanged) -> None:
        if not event.user_initiated:
            return
        fs = self._terminal_pool.filesystem_for(event.terminal_widget)
        if fs is None:
            return
        if event.panel_id == PanelRef.LEFT.value:
            panel = self._left_panel
        elif event.panel_id == PanelRef.RIGHT.value:
            panel = self._right_panel
        else:
            # _NN_PANEL was unset (startup) or FallbackDriver — fall back to active panel.
            panel = self.active_panel()
        panel.set_path(VPath(event.cwd, fs))

    def _action_toggle_sync_browsing(self) -> None:
        a = self._act("view.sync_browsing")
        if a.checked:
            self._sync_state = SyncBrowsing(self._left_panel.path, self._right_panel.path)
            self._left_panel.add_class("-sync-active")
            self._right_panel.add_class("-sync-active")
        else:
            self._sync_state = None
            self._left_panel.remove_class("-sync-active")
            self._right_panel.remove_class("-sync-active")

    def _disable_sync_browsing(self, reason: str) -> None:
        self._sync_state = None
        self._act("view.sync_browsing").set_checked(False)
        self._left_panel.remove_class("-sync-active")
        self._right_panel.remove_class("-sync-active")
        self.notify(reason, title="Synchronized Browsing Disabled", severity="warning")

    def _mirror_sync(self, source: DirectoryBrowser, new_path: VPath) -> None:
        sync = self._sync_state
        assert sync is not None
        if source is self._left_panel:
            src_base, other_base, other = sync.left_base, sync.right_base, self._right_panel
            prev_path = sync.left_prev
        else:
            src_base, other_base, other = sync.right_base, sync.left_base, self._left_panel
            prev_path = sync.right_prev

        if new_path.filesystem is not src_base.filesystem:
            self._disable_sync_browsing("Synchronized browsing was disabled because the active panel switched filesystem.")
            return

        rel = new_path.path.relative_to(src_base.path, walk_up=True)
        target_path = PurePosixPath(os.path.normpath(str(other_base.path / rel)))
        target = VPath(target_path, other_base.filesystem)

        stat = target.stat_or_none
        if stat is None or not stat.is_directory:
            source.set_path(prev_path, record_history=False)
            if isinstance(prev_path.filesystem, LocalFilesystem):
                self._terminal_pool.active_terminal.request_cd(prev_path.path)
            msg = f"Mirror path does not exist: {target_path}"
            self.app.call_after_refresh(self.app.notify, msg, title="Synchronized Browsing", severity="warning")
            return

        other.set_path(target, record_history=False)
        if source is self._left_panel:
            sync.left_prev = new_path
        else:
            sync.right_prev = new_path

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

    @work
    async def on_bookmarks_dialog_bookmark_selected(self, event: BookmarksDialog.BookmarkSelected) -> None:
        try:
            vpath = await vfspath_from_uri(event.bookmark_path)
        except ValueError as exc:
            self.notify(str(exc), title="Cannot open bookmark", severity="error")
            return
        if vpath is None:
            return
        _logger.info("Bookmark selected: %s", vpath)
        self.active_panel().set_path(vpath)

    @work
    async def on_go_to_path_widget_submitted(self, event: GoToPathWidget.Submitted) -> None:
        try:
            submitted = parse_uri(event.path).components[0]
            current = parse_uri(event.browser.path.uri).components[0]
            if submitted.scheme == current.scheme and submitted.netloc == current.netloc:
                # Same scheme + authority — reuse the existing filesystem, no new connection.
                vpath = event.browser.path.filesystem.path(submitted.path or "/")
                event.browser.set_path(vpath)
                return
            vpath = await vfspath_from_uri(event.path)
        except ValueError as exc:
            self.notify(str(exc), title="Cannot navigate", severity="error")
            return
        if vpath is None:
            return
        event.browser.set_path(vpath)

    async def _action_go_to_path(self) -> None:
        await self.active_panel().action_go_to_path()

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

        actions: list[tuple[AKey, Action]] = [
            (AKey(is_directory=True, is_file=True), self._act("file.open")),
            (AKey(is_directory=True), self._act("file.open_in_other_panel")),
            (AKey(is_symlink=True), self._act("browser.follow_symlink")),
            (AKey(is_file=True), self._act("browser.open_editor")),
            (AKey(is_directory=True, is_file=True), self._act("file.cut")),
            (AKey(is_directory=True, is_file=True), self._act("file.copy")),
            (AKey(is_directory=True, is_file=True), self._act("file.copy_names")),
            (AKey(is_path_in_clipboard=True), self._act("file.paste")),
            (AKey(is_directory=True, is_file=True), self._act("browser.delete")),
            (AKey(is_directory=True, is_file=True), self._act("browser.rename")),
            (AKey(is_directory=True, is_file=False, is_empty=True), self._act("bookmarks.add_to_bookmarks")),
        ]

        for key, a in actions:
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

    @work
    async def on_directory_browser_context_menu(self, event: DirectoryBrowser.ContextMenu) -> None:
        self._update_actions(event.path)
        items: list[tuple[Action, int]] = [
            (self._act("file.new_file"), 0),
            (self._act("file.open"), 2),
            (self._act("file.open_in_other_panel"), 2),
            (self._act("browser.follow_symlink"), 2),
            (self._act("browser.open_editor"), 2),
            (self._act("file.cut"), 3),
            (self._act("file.copy"), 3),
            (self._act("file.copy_names"), 4),
            (self._act("file.paste"), 4),
            (self._act("browser.delete"), 5),
            (self._act("browser.rename"), 5),
            (self._act("bookmarks.add_to_bookmarks"), 6),
            (self._act("view.show_hidden_files"), 7),
        ]

        menu = Menu()
        last_group = None
        for a, group in items:
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
        new_state = not self._left_panel.show_hidden_files
        self._act("view.show_hidden_files").set_checked(new_state)
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

    def _action_copy_names(self) -> None:
        names = "\n".join(p.name for p in self.active_panel().selected_path_items)
        self.app.copy_to_clipboard(names)

    @work
    async def _action_rename(self) -> None:
        panel = self.active_panel()
        source = panel.path_item_under_cursor
        if isinstance(source, UpPath):
            return
        job = await copy_or_move_files_job(
            src_paths=[source],
            dst_path=panel.path,
            move=True,
        )
        if job is not None:
            self.app.job_registry.add_job(job)
            await job.start(self.app.request_callback)

    @work
    async def _action_new_directory(self) -> None:
        dialog = InputNameDialog("New Directory", "Directory name:")
        if await dialog.run() != Response.OK:
            return
        name = dialog.value.strip()
        if not name:
            return
        new_path = self.active_panel().path / name
        try:
            new_path.filesystem.mkdir(new_path)
        except FileExistsError:
            self.notify(f"{name!r} already exists.", title="Cannot Create Directory", severity="error")
            return
        except OSError as exc:
            self.notify(str(exc), title="Cannot Create Directory", severity="error")
            return
        self.active_panel().reload()

    @work
    async def _action_new_file(self) -> None:
        dialog = InputNameDialog("New File", "File name:")
        if await dialog.run() != Response.OK:
            return
        name = dialog.value.strip()
        if not name:
            return
        new_path = self.active_panel().path / name
        try:
            writer = new_path.filesystem.write(new_path)
            writer.close()
        except OSError as exc:
            self.notify(str(exc), title="Cannot Create File", severity="error")
            return
        self.active_panel().reload()

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
    async def _action_about(self) -> None:
        version = importlib.metadata.version("nova-navigator")
        await MessageBox(f"Nova Navigator {version}\nhttps://github.com/epicodic/nova-navigator/", title="About").run()

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
        vpath = await RemoteConnector(conf_.remotes).resolve("/", conn.name)
        if vpath is None:
            return
        start_path = await asyncio.to_thread(vpath.filesystem.cwd)
        self.active_panel().set_path(start_path)

    @work
    async def _action_add_to_bookmarks(self) -> None:
        path = self.active_panel().path_item_under_cursor
        if path is None:
            return
        dialog = EditBookmarksDialog(
            copy.deepcopy(conf_.bookmarks),
            prefill=(DEFAULT_BOOKMARKS_GROUP, path.name, path.uri),
        )
        if await dialog.run() == Response.OK:
            conf_.bookmarks = dialog.config
            conf_.bookmarks.save()

    def _action_refresh(self) -> None:
        self._left_panel.reload()
        self._right_panel.reload()

    @work
    async def action_keybindings(self) -> None:
        dialog = KeybindingsDialog(
            actions=list(type(self).ACTIONS),
            config=self._keymap_config,
        )
        result = await self.app.push_screen_wait(dialog)
        if result is not None:
            self._reload_keymap()

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

    def __init__(self, config_dir: Path | None = None) -> None:
        apply_runtime_patches()
        super().__init__()
        self._config_dir = config_dir
        self._path_clipboard = PathClipboard(self)
        self._showing_exception_dialog = False
        register_remote_scheme(conf_.remotes)

    def action_help_quit(self) -> None:
        pass

    async def on_event(self, event: events.Event) -> None:
        if isinstance(event, events.Key):
            # Recover Ctrl+H (the "real" backspace sends character \x08)
            if event.key == "backspace" and event.character == "\x08":
                event.key = "ctrl+h"
            screen = self.screen
            if isinstance(screen, MainScreen) and screen._keymap_registry is not None and await screen._keymap_registry.handle_key(event.key, self):
                return
        await super().on_event(event)

    def _handle_exception(self, error: Exception) -> None:
        sys.settrace(debug_analytics.trace_handler)
        debug_analytics.write_crash(error)
        super()._handle_exception(error)

    async def _handle_exception_recoverable(self, error: Exception) -> bool:
        """Show an error dialog for an unhandled exception.

        Returns False — the error dialog is shown asynchronously via a worker so
        that the App's message loop is not blocked.  If the user chooses Abort,
        the exception is passed to ``_handle_exception`` which terminates the app
        with full crash reporting.
        """
        sys.settrace(debug_analytics.trace_handler)
        debug_analytics.write_crash(error)
        if self._showing_exception_dialog:
            # Avoid recursive error dialogs — fall back to termination.
            return True
        self._showing_exception_dialog = True

        async def _show_dialog() -> None:
            result = await MessageBox(
                str(error),
                title=type(error).__name__,
                buttons=[
                    ButtonSpec(response=Response.OK, label="Continue"),
                    ButtonSpec(response=Response.DISCARD, label="Abort", variant="error"),
                ],
                variant="error",
            ).run()
            self._showing_exception_dialog = False
            if result == Response.DISCARD:
                raise error

        self.run_worker(_show_dialog)
        return False

    async def on_mount(self) -> None:
        debug_analytics.install()
        self.log("Starting Nova Navigator...")
        self._main_screen = MainScreen(config_dir=self._config_dir)
        self.install_screen(self._main_screen, "main_screen")
        plugin_registry = PluginRegistry(SCHEME_REGISTRY, self._main_screen._terminal_pool)
        plugin_registry.register(SSH_PLUGIN)
        plugin_registry.register(AZURE_PLUGIN)
        self.push_screen("main_screen")

    async def open_editor(self, path: VPath) -> None:
        editor_screen = Editor()
        self.push_screen(editor_screen)
        try:
            editor_screen.open(path)
        except Exception:
            self.pop_screen()
            raise

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
