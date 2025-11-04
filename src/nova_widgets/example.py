from textual import work
from textual.app import App
from textual.widgets import Button, Footer, Header

from nova_widgets import Menu, MenuBar
from nova_widgets.menu import constructor as mc


class ExampleApp(App):
    """A minimal Textual application with a button and a counter."""

    def __init__(self):
        super().__init__()
        self._menu = Menu(
            items=[
                mc.item("Option 1", icon="o", shortcut="F1"),
                mc.item("Option 2", shortcut="F2", disabled=True),
                mc.item("Option 3", shortcut="F3"),
                mc.separator(),
                mc.submenu(
                    "Submenu",
                    items=[
                        mc.item("Sub-option 1", shortcut="F4"),
                        mc.item("Sub-option 2", shortcut="F5"),
                        mc.submenu(
                            "Sub-submenu",
                            items=[
                                mc.item("Sub-sub-option 1", shortcut="F6"),
                                mc.item("Sub-sub-option 2", shortcut="F7"),
                            ],
                        ),
                    ],
                ),
                mc.separator(),
                mc.item("Exit", icon="x", shortcut="Ctrl+Q"),
            ]
        )

    def compose(self):
        menu_bar = MenuBar()
        file_menu = menu_bar.add_menu("File")
        file_menu.add_item("New", shortcut="Ctrl+N")
        file_menu.add_item("Open", shortcut="Ctrl+O")
        file_menu.add_item("Save", shortcut="Ctrl+S")
        file_menu.add_separator()
        file_menu.add_item("Exit", shortcut="Ctrl+Q")

        edit_menu = menu_bar.add_menu("Edit")
        edit_menu.add_item("Undo", shortcut="Ctrl+Z")
        edit_menu.add_item("Redo", shortcut="Ctrl+Y")
        find_menu = edit_menu.add_menu("Find")
        find_menu.add_item("Find...", shortcut="Ctrl+F")
        find_menu.add_item("Replace...", shortcut="Ctrl+H")

        edit_menu.add_separator()
        edit_menu.add_item("Cut", shortcut="Ctrl+X")
        edit_menu.add_item("Copy", shortcut="Ctrl+C")
        edit_menu.add_item("Paste", shortcut="Ctrl+V")

        help_menu = menu_bar.add_menu("Help")
        help_menu.add_item("Documentation", shortcut="F1")
        help_menu.add_item("About", shortcut="F2")
        yield menu_bar
        yield Button(label="Click Me!", id="click_button")
        yield Footer()

    @work
    async def on_button_pressed(self, event: Button.Pressed) -> None:
        button = event.button
        button.label = "Clicked!"
        res = await self._menu.exec()
        self.log(f"Menu selection: {res}")


if __name__ == "__main__":
    ExampleApp().run()
