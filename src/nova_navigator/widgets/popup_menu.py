from textual import events
from textual.app import ComposeResult
from textual.containers import Grid
from textual.screen import ModalScreen
from textual.widgets import Button, Label, ListItem, ListView


class QuitScreen(ModalScreen[None]):
    """Screen with a dialog to quit."""

    def compose(self) -> ComposeResult:
        yield Grid(
            Label("Are you sure you want to quit?", id="question"),
            Button("Quit", variant="error", id="quit"),
            Button("Cancel", variant="primary", id="cancel"),
            id="dialog",
        )

    def on_button_pressed(self, event: Button.Pressed) -> None:
        if event.button.id == "quit":
            self.app.exit()
        else:
            self.app.pop_screen()


class OverlayWidget(ListView):
    """Custom widget for the overlay."""

    DEFAULT_CSS = """
    OverlayWidget {
        layer: above;
        position: absolute;
        offset: 0 0;
        width: 20;
        height: 10;
        background: $panel;
        color: white;
        border: solid white;
    }
    """

    def __init__(self) -> None:
        super().__init__(initial_index=None)
        self.offset = (10, 5)
        # self.can_focus = True

    def _on_mount(self, _: events.Mount) -> None:
        for i in range(5):
            self.append(ListItem(Label(f"Overlay Item {i + 1}")))
        # return super()._on_mount(_)

    def _on_blur(self, event: events.Blur) -> None:
        self.remove()
