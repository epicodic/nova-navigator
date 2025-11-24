from __future__ import annotations

import logging
import subprocess
from typing import ClassVar

from textual.app import App, ComposeResult
from textual.binding import Binding
from textual.containers import Horizontal
from textual.events import Key, Resize
from textual.logging import TextualHandler
from textual.screen import Screen
from textual.widgets import Footer, Input, Static

# from nn.widgets.menu import MenuBar, MenuHeader
from nova_navigator import archive, vfs
from nova_navigator.config import global_config
from nova_navigator.widgets.directory_browser import DirectoryBrowser
from nova_navigator.widgets.terminal import Terminal, _get_precmd

logging.basicConfig(
    level="NOTSET",
    handlers=[TextualHandler()],
)


class CommandInput(Input):
    pass


class NovaNavigator(App[None]):
    """Nova Navigator App."""

    CSS_PATH = "nn.tcss"

    BINDINGS: ClassVar = [
        Binding("^q", "request_quit", "Quit"),
        Binding("tab", "tab_pressed", "Switch Panes", show=True, priority=True),
        Binding("shift+tab", "shift_tab_pressed", "Shift+Tab", priority=True),
        # Binding("enter", "enter_pressed", "Enter", priority=True),
        # Bindingenterst", "Test", show=True),
        Binding("colon", "focus_terminal", "Command Input", show=True),
    ]

    _left_pane: DirectoryBrowser
    _right_pane: DirectoryBrowser
    _terminal: Terminal
    _terminal_waits_for_enter: bool = False

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
        # self.push_screen("main")
        pre_cmd = _get_precmd(self._terminal.fd_pre_cmd_child)
        await self._terminal.send(pre_cmd)

    def on_resize(self, event: Resize) -> None:
        self._terminal.styles.width = int(event.size.width)

    async def action_shift_tab_pressed(self) -> None:
        event = Key("tab", character="\t")
        await self._terminal.on_key(event)

    # async def action_enter_pressed(self) -> None:
    #     event = Key("enter", character="\r")
    #     if not self._terminal_waits_for_enter:
    #         self.post_message(event)
    #         return
    #     await self._terminal.on_key(event)
    #     self._terminal_waits_for_enter = False

    async def _on_key(self, event: Key) -> None:
        if event.key == "enter" and self._terminal_waits_for_enter:
            event.stop()
            event.prevent_default()
            self._terminal_waits_for_enter = False
            await self._terminal.on_key(event)

        KEYS_TO_FORWARD_TO_TERMINAL = {"backspace", "delete", "left", "right"}

        if event.is_printable or event.key in KEYS_TO_FORWARD_TO_TERMINAL:
            self._terminal_waits_for_enter = True
            await self._terminal.on_key(event)

    # def action_test(self) -> None:
    #     # self.push_screen(QuitScreen())
    #     overlay = OverlayWidget()
    #     self.mount(overlay)
    #     overlay.focus()

    def action_tab_pressed(self) -> None:
        if self._left_pane.has_focus:
            self._right_pane.focus()
        else:
            self._left_pane.focus()

    def action_focus_terminal(self) -> None:
        self._terminal.focus()

    async def _on_directory_browser_path_selected(self, event: DirectoryBrowser.PathSelected) -> None:
        if event.path.stats.is_directory:
            # return  # do not handle directories here
            if isinstance(event.path, vfs.LocalPath):
                await self._terminal.send(f" cd '{event.path.path.as_posix()}'\r")
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
        # TODO: update last active pane instead of always left pane
        self._left_pane.set_path(vfs.LocalPath(event.cwd))


def main() -> None:
    """Main function."""
    global_config.load_all_configs()
    NovaNavigator().run()
    # global_config.write_all_configs()


if __name__ == "__main__":
    main()
