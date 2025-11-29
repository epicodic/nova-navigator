from __future__ import annotations

import logging
import subprocess
from enum import Enum
from typing import ClassVar

from textual import events, work
from textual.app import App, ComposeResult
from textual.binding import Binding
from textual.containers import Horizontal
from textual.events import Key, Resize
from textual.logging import TextualHandler
from textual.screen import Screen
from textual.widgets import Footer, Input

# from nn.widgets.menu import MenuBar, MenuHeader
from nova_navigator import archive, vfs
from nova_navigator.config import global_config
from nova_navigator.file_operations import copy_or_move_files
from nova_navigator.widgets.directory_browser import DirectoryBrowser
from nova_navigator.widgets.terminal import Terminal, shell_clear_prompt, shell_cmd_cd, shell_init_code

logging.basicConfig(
    level="INFO",
    handlers=[TextualHandler()],
)


class CommandInput(Input):
    pass


class MainScreen(Screen[None]):
    pass


class NovaNavigator(App[None]):
    """Nova Navigator App."""

    CSS_PATH = "nn.tcss"

    BINDINGS: ClassVar = [
        Binding("^q", "request_quit", "Quit"),
        # Binding("tab", "tab_pressed", "Switch Panes", show=True, priority=True),
        Binding("shift+tab", "shift_tab_pressed", "Shift+Tab", priority=True),
        Binding("ctrl+o", "toggle_terminal", "Toggle Terminal", priority=True),
        Binding("ctrl+@", "focus_terminal", "Focus Terminal", priority=True),
        Binding("f6", "move_files", "Move"),
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

    def __init__(self) -> None:
        super().__init__()
        self._terminal_mode = self._TerminalMode.MINIMIZED

    def compose(self) -> ComposeResult:
        # yield Header()

        # yield MenuBar(
        #     MenuHeader(menu_id="left", name="Left"),
        #     MenuHeader(menu_id="file", name="File"),
        #     MenuHeader(menu_id="edit", name="Edit"),
        #     MenuHeader(menu_id="settings", name="Settings"),
        #     MenuHeader(menu_id="right", name="Right"),
        # )

        self._left_pane = DirectoryBrowser(id="pane-left", path=vfs.LocalPath.cwd())
        self._last_active_pane = self._left_pane
        self._right_pane = DirectoryBrowser(id="pane-right", path=vfs.LocalPath.cwd())
        yield Horizontal(
            self._left_pane,
            self._right_pane,
        )

        self._terminal = Terminal("/usr/bin/zsh", id="terminal")
        self._terminal.styles.height = 1
        self._terminal.start()

        yield self._terminal
        yield Footer()

    async def on_mount(self) -> None:
        self.log("Starting Nova Navigator...")
        self._main_screen = MainScreen()
        self.install_screen(self._main_screen, "main_screen")
        # self.push_screen("main")
        pre_cmd = shell_init_code(self._terminal.fd_pre_cmd_child)
        await self._terminal.send(pre_cmd)

    def on_resize(self, event: Resize) -> None:
        self._resize_terminal()

    async def action_shift_tab_pressed(self) -> None:
        if self._terminal_mode == self._TerminalMode.MAXIMIZED:
            return
        event = Key("tab", character="\t")
        await self._terminal.on_key(event)

    async def on_event(self, event: events.Event) -> None:
        if isinstance(event, Key):  # noqa: SIM102
            if await self.grab_key_events(event):
                return  # event was handled

        await super().on_event(event)

    async def grab_key_events(self, event: Key) -> bool:
        """Grab key events before they reach other widgets.

        Return True if the event was handled here, False to let it propagate.
        """
        # recover Ctrl+H (the "real" backspace has character \x7f)
        if event.key == "backspace" and event.character == "\x08":
            event.key = "ctrl+h"

        if event.key == "tab":
            self.log(self.screen)

            await self.action_tab_pressed()
            return True

        return False

    async def _on_key(self, event: Key) -> None:
        if self._terminal_mode == self._TerminalMode.MAXIMIZED:
            return
        if event.key == "ctrl+down":
            path = self._last_active_pane.path_item_under_cursor
            await self._terminal.send(f"{path.name}")
            return

        if event.key == "ctrl+shift+down":
            path = self._last_active_pane.path_item_under_cursor
            assert isinstance(path, vfs.LocalPath)
            await self._terminal.send(f"{path.path.as_posix()}")
            return

        if event.key == "enter" and self._terminal_waits_for_enter:
            event.stop()
            event.prevent_default()
            self._terminal_waits_for_enter = False
            await self._terminal.on_key(event)

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
            return

        if event.is_printable:
            self._terminal_waits_for_enter = True
            await self._terminal.on_key(event)

    # def action_test(self) -> None:
    #     # self.push_screen(QuitScreen())
    #     overlay = OverlayWidget()
    #     self.mount(overlay)
    #     overlay.focus()

    async def action_tab_pressed(self) -> None:
        if self._terminal_mode == self._TerminalMode.MAXIMIZED:
            event = Key("tab", character="\t")
            await self._terminal.on_key(event)
            return
        if self._left_pane.has_focus:
            self._right_pane.focus()
            self._last_active_pane = self._right_pane
        else:
            self._left_pane.focus()
            self._last_active_pane = self._left_pane
        await self._set_terminal_directory(self._last_active_pane.path)

    def action_toggle_terminal(self) -> None:
        if self._terminal_mode == self._TerminalMode.MAXIMIZED:
            self._terminal_mode = self._TerminalMode.MINIMIZED
            self._last_active_pane.focus()
        else:
            self._terminal_mode = self._TerminalMode.MAXIMIZED
            self._terminal.focus()
        self._resize_terminal()

    def action_focus_terminal(self) -> None:
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

    async def _set_terminal_directory(self, path: vfs.VFSPath) -> None:
        assert isinstance(path, vfs.LocalPath), "Only local paths are supported at the moment"
        await self._terminal.send(shell_clear_prompt() + " " + shell_cmd_cd(path.path) + "\n")

    async def _on_directory_browser_path_selected(self, event: DirectoryBrowser.PathSelected) -> None:
        if event.path.stats.is_directory:
            await self._set_terminal_directory(event.path)
            return

        # handle archives
        path = event.path
        assert isinstance(path, vfs.LocalPath)
        if archive.is_supported_archive(path.path):
            archive_path = vfs.ArchivePath("/", archive_parent=path.parent, archive=path)
            event.browser.set_path(archive_path)
            return

        # open file with xdg-open
        open_cmd = global_config.extensions.get_open_command_for_file_path(path.path)
        subprocess.Popen(open_cmd)

    def _on_terminal_pre_cmd(self, event: Terminal.PreCmd) -> None:
        self._last_active_pane.set_path(vfs.LocalPath(event.cwd))

    # operations

    @work
    async def action_move_files(self) -> None:
        source_paths = list(self._last_active_pane.selected_path_items)
        destination_path = self._left_pane.path if self._last_active_pane == self._right_pane else self._right_pane.path

        await copy_or_move_files(
            source_paths=source_paths,
            destination_path=destination_path,
            move=True,
        )


def main() -> None:
    """Main function."""
    global_config.load_all_configs()
    NovaNavigator().run()
    # global_config.write_all_configs()


if __name__ == "__main__":
    main()
