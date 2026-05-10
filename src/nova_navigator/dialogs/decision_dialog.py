import logging
from typing import Any, ClassVar, Sequence, override

from textual import widgets
from textual.app import ComposeResult
from textual.binding import Binding
from textual.containers import Horizontal, Vertical
from textual.screen import ModalScreen
from textual.widget import Widget

from ..decision import Decision
from ..format_utils import format_size
from ..scheduler import DecisionRequest

_logger = logging.getLogger(__name__)


def make_decision_dialog(request: DecisionRequest) -> "DecisionDialog":
    """Return the appropriate dialog widget for *request*.

    Dispatches on ``request.dialog_type``; falls back to the generic
    :class:`DecisionDialog` for unknown or absent types.
    """
    if request.dialog_type == "overwrite":
        return OverwriteDecisionDialog(request)
    return DecisionDialog(request)


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
        Binding(key="left", action="app.focus_previous", show=False),
        Binding(key="right", action="app.focus_next", show=False),
        Binding(key="up", action="focus_up", show=False),
        Binding(key="down", action="focus_down", show=False),
    ]

    _request: DecisionRequest

    def __init__(self, request: DecisionRequest, id: str | None = None, **kwargs: Any) -> None:
        super().__init__(id=id, **kwargs)
        self._request = request

    def _details_content(self) -> Sequence[Widget]:
        return []

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
            *self._details_content(),
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

    def action_focus_up(self) -> None:
        self._move_focus_vertical(-1)

    def action_focus_down(self) -> None:
        self._move_focus_vertical(1)

    def _move_focus_vertical(self, direction: int) -> None:
        focused = self.focused
        if not isinstance(focused, widgets.Button):
            return
        top_buttons = list(self.query("#button_box Button").results(widgets.Button))
        bottom_buttons = list(self.query("#button_box_to_all Button").results(widgets.Button))
        if focused in top_buttons and direction > 0 and bottom_buttons:
            idx = min(top_buttons.index(focused), len(bottom_buttons) - 1)
            bottom_buttons[idx].focus()
        elif focused in bottom_buttons and direction < 0 and top_buttons:
            idx = min(bottom_buttons.index(focused), len(top_buttons) - 1)
            top_buttons[idx].focus()

    async def run(self) -> Decision:
        self.focus()
        return await self.app.push_screen_wait(screen=self)


class OverwriteDecisionDialog(DecisionDialog):
    """A specialised decision dialog for file-overwrite confirmations.

    Expects the following keys in ``request.details``:

    - ``"src_name"`` — source filename (str)
    - ``"src_size"`` — source file size in bytes (int)
    - ``"dst_name"`` — destination filename (str)
    - ``"dst_size"`` — destination file size in bytes (int)
    """

    DEFAULT_CSS = (
        DecisionDialog.DEFAULT_CSS
        + """
    OverwriteDecisionDialog {
        #file_info {
            color: $text;
            padding: 0 1;
            margin: 1 0;
        }
    }
    """
    )

    @override
    def _details_content(self) -> Sequence[Widget]:
        details = self._request.details
        src_name = details.get("src_name", "?")
        src_size = format_size(details.get("src_size", 0))
        dst_name = details.get("dst_name", "?")
        dst_size = format_size(details.get("dst_size", 0))
        file_info = f"  Source:       {src_name}  ({src_size})\n  Destination:  {dst_name}  ({dst_size})"
        return [widgets.Static(file_info, id="file_info")]
