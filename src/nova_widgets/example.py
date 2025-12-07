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
        self._menu.add_item("Exit", icon="x", shortcut="Ctrl+Q")

    def compose(self):
        yield MenuBar()
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
