from textual import work
from textual.app import App
from textual.widgets import Button, Footer, Header

from nova_widgets import Menu, MenuBar


class ExampleApp(App):
    """A minimal Textual application with a button and a counter."""

    def __init__(self):
        super().__init__()
        self._menu = Menu()
        self._menu.add_item("Option 1", icon="o", shortcut="F1")
        self._menu.add_item("Option 2", shortcut="F2", disabled=True)
        self._menu.add_item("Option 3", shortcut="F3")
        self._menu.add_separator()
        submenu = self._menu.add_menu("Submenu")
        submenu.add_item("Sub-option 1", shortcut="F4")
        submenu.add_item("Sub-option 2", shortcut="F5")

        self._menu.add_separator()
        self._menu.add_item("Exit", icon="x", shortcut="Ctrl+Q")

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
