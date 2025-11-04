from dataclasses import dataclass
from enum import StrEnum, auto
from typing import Any, ClassVar

from textual import widgets
from textual.app import ComposeResult
from textual.binding import Binding
from textual.containers import (
    Horizontal,
    Vertical,
)
from textual.screen import ModalScreen
from textual.widgets.button import ButtonVariant


@dataclass
class Button:
    label: str
    id: str
    variant: ButtonVariant = "default"


class DefaultButton(StrEnum):
    """Default button IDs."""

    OK = auto()
    CANCEL = auto()
    YES = auto()
    NO = auto()
    RETRY = auto()


def _default_button(id: DefaultButton) -> Button:
    match id:
        case DefaultButton.OK:
            return Button("Ok", DefaultButton.OK, "primary")
        case DefaultButton.CANCEL:
            return Button("Cancel", DefaultButton.CANCEL, "error")
        case DefaultButton.YES:
            return Button("Yes", DefaultButton.YES, "success")
        case DefaultButton.NO:
            return Button("No", DefaultButton.NO, "error")
        case DefaultButton.RETRY:
            return Button("Retry", DefaultButton.RETRY, "warning")
        case _:
            raise ValueError("Unrecognized DefaultButton ID")


class Dialog(ModalScreen[str]):
    DEFAULT_CSS = """
    Dialog {
        align: center middle;

        #dialog_box {
            border: round $accent;
            width: 50%;
            height: auto;
        }

        #button_box {
            height: auto;
            align-horizontal: center;
        }

        Button {
            width: auto;
            max-width: 20;
            margin: 0 2;
        }
    }


    """

    BINDINGS: ClassVar = [Binding(key="escape", action="app.pop_screen", description="Close")]

    _title: str = ""
    _buttons: list[Button]
    _dialog_box: Vertical | None = None
    _button_box: Horizontal | None = None

    def __init__(
        self,
        title: str,
        id: str | None = None,
        buttons: list[Button | DefaultButton] | None = None,
        **kwargs: Any,
    ) -> None:
        super().__init__(**kwargs, id=id)
        self._title = title
        self._buttons = [
            (_default_button(button) if isinstance(button, DefaultButton) else button)
            for button in (buttons or [DefaultButton.OK])
        ]
        self._dialog_box = None
        self._button_box = None

    async def run(self) -> str:
        self.focus()
        return await self.app.push_screen_wait(screen=self)

    def compose_content(self) -> ComposeResult:
        yield widgets.Label(self._title)

    def compose(self) -> ComposeResult:
        self._button_box = Horizontal(
            *[
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
