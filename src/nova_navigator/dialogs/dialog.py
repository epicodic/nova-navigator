from dataclasses import dataclass
from typing import Any, ClassVar

from textual import widgets
from textual.app import ComposeResult
from textual.binding import Binding
from textual.containers import Vertical
from textual.screen import ModalScreen
from textual.widgets.button import ButtonVariant

from nova_widgets import ButtonBox

from ..decision import Decision


@dataclass
class Button:
    label: str
    id: str
    variant: ButtonVariant = "default"


DefaultButton = Decision


def _default_button(button: DefaultButton) -> Button:
    return Button(label=button.tr, id=button.name, variant="primary" if button.is_positive else "error")


class Dialog(ModalScreen[str]):
    DEFAULT_CSS = """
    Dialog {
        align: center middle;

        #dialog_box {
            border: round $accent;
            width: 50%;
            height: auto;
            padding: 1;
        }

        #button_box {
            height: auto;
        }

        Button {
            width: auto;
            max-width: 20;
            margin: 0 2;
        }
    }


    """

    BINDINGS: ClassVar = [
        Binding(key="escape", action="dismiss_dialog", description="Close", priority=True),
        Binding(key="enter", action="accept_dialog", description="Accept", priority=True),
    ]

    _title: str = ""
    _buttons: list[Button]
    _dialog_box: Vertical | None = None
    _button_box: ButtonBox | None = None
    _button_accept: Button | None = None
    _button_dismiss: Button | None = None

    def __init__(
        self,
        title: str,
        id: str | None = None,
        buttons: list[Button | DefaultButton] | None = None,
        **kwargs: Any,
    ) -> None:
        super().__init__(**kwargs, id=id)
        self._title = title

        self._buttons = []
        for button in buttons or [DefaultButton.OK]:
            if isinstance(button, DefaultButton):
                button_to_add = _default_button(button)
                if button.is_positive:
                    self._button_accept = button_to_add
                elif button.is_negative:
                    self._button_dismiss = button_to_add
                self._buttons.append(button_to_add)
            else:
                self._buttons.append(button)

        self._dialog_box = None
        self._button_box = None

    async def run(self) -> str:
        self.focus()
        return await self.app.push_screen_wait(screen=self)

    def compose_content(self) -> ComposeResult:
        yield widgets.Label(self._title)

    def compose(self) -> ComposeResult:
        self._button_box = ButtonBox(
            [
                widgets.Button(
                    button.label,
                    id=button.id,
                    variant=button.variant,
                    flat=True,
                )
                for button in self._buttons
            ],
            id="button_box",
        )

        self._dialog_box = Vertical(
            *self.compose_content(),
            self._button_box,
            id="dialog_box",
        )
        self._dialog_box.border_title = self._title
        yield self._dialog_box
        yield widgets.Footer()

    def on_button_pressed(self, event: widgets.Button.Pressed) -> None:
        self.dismiss(event.button.id)

    def action_accept_dialog(self) -> None:
        if self._button_accept:
            self.dismiss(self._button_accept.id)

    def action_dismiss_dialog(self) -> None:
        if self._button_dismiss:
            self.dismiss(self._button_dismiss.id)
