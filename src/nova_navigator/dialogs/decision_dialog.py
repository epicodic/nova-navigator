import logging
from typing import Any, ClassVar

from textual import widgets
from textual.app import ComposeResult
from textual.binding import Binding
from textual.containers import Horizontal, Vertical
from textual.screen import ModalScreen

from ..decision import Decision
from ..scheduler import DecisionRequest

_logger = logging.getLogger(__name__)


class DecisionDialog(ModalScreen[Decision]):
    DEFAULT_CSS = """
    DecisionDialog {
        align: center middle;

        #dialog_box {
            border: round $accent;
            background: darkred;
            width: 50%;
            height: auto;
            padding: 1 1 0 1;
        }

        #button_box {
            height: auto;
            align-horizontal: center;
        }
        #button_box_to_all {
            height: auto;
            align-horizontal: center;
        }

        Button {
            width: auto;
            max-width: 15;
            margin: 0 2;
        }

    }
    """

    BINDINGS: ClassVar = [
        Binding(key="escape", action="abort", description="No", priority=True),
    ]

    _request: DecisionRequest

    def __init__(self, request: DecisionRequest, id: str | None = None, **kwargs: Any) -> None:
        super().__init__(id=id, **kwargs)
        self._request = request

    def compose(self) -> ComposeResult:
        buttons = []
        to_all_buttons = []
        for expected_decision in self._request.expected_decisions:
            text = expected_decision.tr
            _logger.warning(text)
            variant = "success" if expected_decision.is_positive else "error"
            btn = widgets.Button(text, id=expected_decision.name, variant=variant, flat=True)
            if expected_decision.is_to_all:
                to_all_buttons.append(btn)
            else:
                buttons.append(btn)

        button_boxes = []
        if buttons:
            button_boxes.append(Horizontal(*buttons, id="button_box"))
        if to_all_buttons:
            button_boxes.append(Horizontal(*to_all_buttons, id="button_box_to_all"))
        dialog_box = Vertical(
            widgets.Label(self._request.message),
            *button_boxes,
            id="dialog_box",
        )
        dialog_box.border_title = self._request.title
        yield dialog_box

    def on_button_pressed(self, event: widgets.Button.Pressed) -> None:
        chosen = Decision[event.button.id]
        self.dismiss(chosen)

    def action_abort(self) -> None:
        self.dismiss(Decision.NO)

    async def run(self) -> Decision:
        self.focus()
        return await self.app.push_screen_wait(screen=self)
