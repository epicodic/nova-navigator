from __future__ import annotations

from pathlib import Path

from textual.app import App, ComposeResult
from textual.binding import Binding
from textual.containers import Horizontal
from textual.screen import Screen
from textual.widgets import Footer, Input, Static

from nn.widgets.directory_browser import DirectoryBrowser
from nn.widgets.menu import MenuBar, MenuHeader


class CommandInput(Input):
    pass


class MainScreen(Screen):
    _left_pane: DirectoryBrowser
    _right_pane: DirectoryBrowser
    _command_input: CommandInput

    BINDINGS = [
        Binding("tab", "tab_pressed", "Switch Panes", show=False),
        Binding("colon", "focus_command_input", "Command Input", show=True),
    ]

    def action_tab_pressed(self) -> None:
        if self._left_pane.has_focus:
            self._right_pane.focus()
        else:
            self._left_pane.focus()

    def action_focus_command_input(self) -> None:
        self._command_input.focus()

    def compose(self) -> ComposeResult:
        # yield Header()

        yield MenuBar(
            MenuHeader(menu_id="left", name="Left"),
            MenuHeader(menu_id="file", name="File"),
            MenuHeader(menu_id="edit", name="Edit"),
            MenuHeader(menu_id="settings", name="Settings"),
            MenuHeader(menu_id="right", name="Right"),
        )

        self._left_pane = DirectoryBrowser(id="pane-left", path=Path.home())
        self._right_pane = DirectoryBrowser(id="pane-right")
        yield Horizontal(
            self._left_pane,
            self._right_pane,
        )

        self._command_input = CommandInput(placeholder="Enter a command", id="command-line-input")
        yield Horizontal(
            Static(" ❯❯❯ ", id="command-line-prompt"),
            self._command_input,
            id="command-line-container",
        )
        yield Footer()

    def on_mount(self) -> None:
        # ROWS = list(csv.reader(io.StringIO(MOVIES)))
        # table = self.query_one(DataTable)
        # table.add_columns("aaa", "bbb", "ccc", "ddd")
        # table.add_rows(ROWS[1:])
        pass


class NovaNavigator(App):
    """Nova Navigator App."""

    CSS_PATH = "nn.tcss"
    SCREENS = {
        "main": MainScreen,
    }
    BINDINGS = [
        ("^q", "quit", "Quit"),
    ]

    def on_mount(self) -> None:
        self.log("Starting Nova Navigator...")
        self.push_screen("main")

    # @contextmanager
    # def suspend(self) -> Iterator[None]:
    #     driver = self._driver
    #     if driver is not None:
    #         driver.stop_application_mode()
    #         with redirect_stdout(sys.__stdout__), redirect_stderr(sys.__stderr__):
    #             yield
    #         driver.start_application_mode()


def main():
    """Main function."""

    NovaNavigator().run()


if __name__ == "__main__":
    main()
