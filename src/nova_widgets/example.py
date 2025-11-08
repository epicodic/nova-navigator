from textual import work
from textual.app import App, ComposeResult
from textual.widgets import Button, Footer

from nova_widgets import Menu, MenuBar
from nova_widgets.menu import constructor as mc


class ExampleApp(App):
    """A minimal Textual application with a button and a counter."""

    def __init__(self):
        super().__init__()
        self._menu = Menu(
            "Example Menu",
            mc.action("Option 1", icon="o", shortcut="F1"),
            mc.action("Option 2", shortcut="F2", enabled=False),
            mc.action("Option 3", shortcut="F3"),
            mc.separator(),
            mc.menu(
                "Submenu",
                mc.action("Sub-option 1", shortcut="F4"),
                mc.action("Sub-option 2", shortcut="F5"),
                mc.menu(
                    "Sub-submenu",
                    mc.action("Sub-sub-option 1", shortcut="F6"),
                    mc.action("Sub-sub-option 2", shortcut="F7"),
                ),
            ),
            mc.separator(),
            mc.action("Exit", icon="x", shortcut="Ctrl+Q"),
        )

    def compose(self) -> ComposeResult:
        menu_bar = MenuBar()
        file_menu = menu_bar.add_menu("File")
        file_menu.add_action("New", shortcut="Ctrl+N", action="new_file")
        file_menu.add_action("Open", shortcut="Ctrl+O")
        file_menu.add_action("Save", shortcut="Ctrl+S")
        file_menu.add_separator()
        file_menu.add_action("Exit", shortcut="Ctrl+Q")

        edit_menu = menu_bar.add_menu("Edit")
        edit_menu.add_action("Undo", shortcut="Ctrl+Z")
        edit_menu.add_action("Redo", shortcut="Ctrl+Y")
        find_menu = edit_menu.add_menu("Find")
        find_menu.add_action("Find...", shortcut="Ctrl+F")
        find_menu.add_action("Replace...", shortcut="Ctrl+H")

        find_menu.add(
            mc.menu(
                "... in Files",
                mc.action("Find in Files", shortcut="Ctrl+Shift+F"),
                mc.action("Replace in Files", shortcut="Ctrl+Shift+H"),
            )
        )

        edit_menu.add_separator()
        edit_menu.add_action("Cut", shortcut="Ctrl+X")
        edit_menu.add_action("Copy", shortcut="Ctrl+C")
        edit_menu.add_action("Paste", shortcut="Ctrl+V")

        options_menu = menu_bar.add_menu("Options")
        options_menu.add(
            mc.action("Checkbox", checkable=True),
            mc.action("Checkbox2", checkable=True),
            mc.action("Checkbox3", checkable=True),
            mc.separator(),
            *mc.group(
                mc.action("Radio 1", checkable=True),
                mc.action("Radio 2", checkable=True),
                mc.action("Radio 3", checkable=True),
            ),
        )

        help_menu = menu_bar.add_menu("Help")
        help_menu.add_action("Documentation", shortcut="F1")
        help_menu.add_action("About", shortcut="F2")
        yield menu_bar
        yield Button(label="Click Me!", id="click_button")
        yield Footer()


    async def _on_menu_triggered(self, event: Menu.Triggered) -> None:
        if event.action and event.action.action:
            await self.app.run_action(event.action.action)
    
    def action_new_file(self) -> None:
        raise NotImplementedError("New file action not implemented yet.")

    @work
    async def on_button_pressed(self, event: Button.Pressed) -> None:
        button = event.button
        button.label = "Clicked!"
        res = await self._menu.exec()
        self.log(f"Menu selection: {res}")


if __name__ == "__main__":
    ExampleApp().run()
