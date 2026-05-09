import logging
from collections.abc import Sequence
from typing import Any, ClassVar, override

from textual import widgets
from textual.app import ComposeResult
from textual.binding import Binding
from textual.containers import Vertical
from textual.screen import ModalScreen
from textual.widget import Widget

from nova_widgets import ButtonBox

from ..format_utils import format_size
from ..response import Response
from ..scheduler import ResponseRequest

_logger = logging.getLogger(__name__)


def make_response_dialog(request: ResponseRequest) -> "ResponseDialog":
    """Return the appropriate dialog widget for *request*.

    Dispatches on ``request.dialog_type``; falls back to the generic
    :class:`ResponseDialog` for unknown or absent types.
    """
    if request.dialog_type == "overwrite":
        return OverwriteResponseDialog(request)
    return ResponseDialog(request)


class ResponseDialog(ModalScreen[Response]):
    DEFAULT_CSS = """
    ResponseDialog {
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

    _request: ResponseRequest

    def __init__(self, request: ResponseRequest, id: str | None = None, **kwargs: Any) -> None:
        super().__init__(id=id, **kwargs)
        self._request = request

    def _details_content(self) -> Sequence[Widget]:
        return []

    def compose(self) -> ComposeResult:
        buttons = []
        to_all_buttons = []
        for expected_response in self._request.expected_responses:
            text = expected_response.tr
            _logger.warning(text)
            variant = "success" if expected_response.is_accepted else "error"
            btn = widgets.Button(text, id=expected_response.name, variant=variant, flat=True)
            if expected_response.is_to_all:
                to_all_buttons.append(btn)
            else:
                buttons.append(btn)

        rows: list[list[widgets.Button]] = []
        if buttons:
            rows.append(buttons)
        if to_all_buttons:
            rows.append(to_all_buttons)
        dialog_box = Vertical(
            widgets.Label(self._request.message),
            *self._details_content(),
            ButtonBox(rows, id="button_box"),
            id="dialog_box",
        )
        dialog_box.border_title = self._request.title
        yield dialog_box

    def on_button_pressed(self, event: widgets.Button.Pressed) -> None:
        chosen = Response[event.button.id]
        self.dismiss(chosen)

    def action_abort(self) -> None:
        self.dismiss(Response.NO)

    async def run(self) -> Response:
        self.focus()
        return await self.app.push_screen_wait(screen=self)


class OverwriteResponseDialog(ResponseDialog):
    """A specialised response dialog for file-overwrite confirmations.

    Expects the following keys in ``request.details``:

    - ``"src_name"`` — source filename (str)
    - ``"src_size"`` — source file size in bytes (int)
    - ``"dst_name"`` — destination filename (str)
    - ``"dst_size"`` — destination file size in bytes (int)
    """

    DEFAULT_CSS = (
        ResponseDialog.DEFAULT_CSS
        + """
    OverwriteResponseDialog {
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
