from dataclasses import dataclass
from typing import Any, ClassVar

from textual import events, widgets
from textual.app import ComposeResult
from textual.binding import Binding
from textual.containers import Vertical
from textual.screen import ModalScreen
from textual.widgets.button import ButtonVariant

from nova_widgets import Button, ButtonBox

from ..response import Response


@dataclass
class ButtonSpec:
    response: Response
    label: str | None = None
    variant: ButtonVariant | None = None

    @property
    def id(self) -> str:
        return self.response.name

    @property
    def display_label(self) -> str:
        return self.label if self.label is not None else self.response.tr

    @property
    def display_variant(self) -> ButtonVariant:
        if self.variant is not None:
            return self.variant
        return "primary" if self.response.is_accepted else "error"


DefaultButton = Response


class Dialog(ModalScreen[Response | None]):
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
        Binding(key="enter", action="accept_dialog", description="Accept"),
    ]

    _title: str = ""
    _buttons: list[ButtonSpec]
    _dialog_box: Vertical | None = None
    _button_box: ButtonBox | None = None
    _button_accept: ButtonSpec | None = None
    _button_dismiss: ButtonSpec | None = None

    def __init__(
        self,
        title: str,
        id: str | None = None,
        buttons: list[ButtonSpec | DefaultButton] | None = None,
        **kwargs: Any,
    ) -> None:
        super().__init__(**kwargs, id=id)
        self._title = title

        self._buttons = []
        for button in buttons or [DefaultButton.OK]:
            if isinstance(button, Response):
                button_to_add = ButtonSpec(response=button)
            else:
                button_to_add = button  # already a ButtonSpec
            if button_to_add.response.is_accepted:
                self._button_accept = button_to_add
            elif button_to_add.response.is_rejected:
                self._button_dismiss = button_to_add
            self._buttons.append(button_to_add)

        self._dialog_box = None
        self._button_box = None

    async def run(self) -> Response | None:
        self.focus()
        return await self.app.push_screen_wait(screen=self)

    def compose_content(self) -> ComposeResult:
        yield widgets.Label(self._title)

    def compose(self) -> ComposeResult:
        self._button_box = ButtonBox(
            [
                Button(
                    button.display_label,
                    id=button.id,
                    variant=button.display_variant,
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

    def on_button_pressed(self, event: Button.Pressed) -> None:
        _dialog_button_ids = {b.id for b in self._buttons}
        if event.button.id not in _dialog_button_ids:
            return  # custom action button — handled by subclass @on handler
        if self._button_accept and event.button.id == self._button_accept.id:
            self.action_accept_dialog()
        elif self._button_dismiss and event.button.id == self._button_dismiss.id:
            self.action_dismiss_dialog()
        else:
            self.dismiss(Response[event.button.id])

    async def _on_key(self, event: events.Key) -> None:
        """Handle Enter before non-priority bindings are checked.

        If a Button has focus, press it (so a focused Cancel button cancels,
        not the default accept button).  For every other focus state, accept
        the dialog with the default accept button.
        """
        if event.key != "enter":
            return
        focused = self.focused
        if isinstance(focused, Button):
            focused.press()
        else:
            self.action_accept_dialog()
        event.stop()

    def action_accept_dialog(self) -> None:
        if self._button_accept:
            self.dismiss(self._button_accept.response)

    def action_dismiss_dialog(self) -> None:
        if self._button_dismiss:
            self.dismiss(self._button_dismiss.response)
        else:
            self.dismiss(None)
