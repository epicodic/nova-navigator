from dataclasses import dataclass

from nova_navigator.operations.operation import Operation
from textual import events
from textual.app import ComposeResult
from textual.containers import Horizontal, Vertical
from textual.content import Content
from textual.message import Message
from textual.widgets import Button, Label, ListItem, ListView, ProgressBar

from ..config import conf_
from ..icons import ico_
from ..widgets.overlay_widget import OverlayWidget


class ProcessesDialog(OverlayWidget, can_focus=True):
    """Processes dialog overlay widget."""

    DEFAULT_CSS = """
        ProcessesDialog {
            width: 40;
            height: 40;

            ListView {
                ListItem {
                    height: 3;
                    Button {
                        height: 1;
                        max-width: 3;
                        background: #00000000;
                        &:hover {
                            background: $secondary;
                            border: block $secondary;
                        }
                    }
                }
            }
        }
    """

    @dataclass
    class OperationItem:
        operation: Operation
        item: ListItem
        progress_bar: ProgressBar
        # undo_button: Button
        abort_button: Button

    _process_list_view: ListView
    _operation_items: dict[int, OperationItem]

    def __init__(self, position: tuple[int, int], operations: list[Operation]) -> None:
        super().__init__("Processes", position, close_action=OverlayWidget.CloseAction.REMOVE)
        self._process_list_view = ListView()
        self._operations = operations
        self._operation_items = {}

    def compose(self) -> ComposeResult:
        yield self._process_list_view

    def _get_op_item(self, operation: Operation) -> OperationItem:
        op_item = self._operation_items.get(operation.id)
        if op_item is not None:
            return op_item

        progress_bar = ProgressBar(total=100.0)
        abort_icon = "x"  # Content("x", cell_length=1)
        abort_button = Button(abort_icon, compact=True)
        item = ListItem(
            Vertical(
                Label(f"{operation.title}"),
                Horizontal(
                    abort_button,
                ),
                progress_bar,
            )
        )
        self._process_list_view.append(item)
        op_item = ProcessesDialog.OperationItem(
            operation=operation,
            item=item,
            progress_bar=progress_bar,
            abort_button=abort_button,
        )
        self._operation_items[operation.id] = op_item
        return op_item

    def _update(self) -> None:
        for operation in self._operations:
            op_item = self._get_op_item(operation)
            progress = operation.progress
            op_item.progress_bar.update(total=progress.total, progress=progress.completed)

    def _on_mount(self, event: events.Mount) -> None:
        self._update()
        # start update timer
        self._timer = self.set_interval(1.0, self._update_progress)

    def on_focus(self, event: events.Focus) -> None:
        self._process_list_view.focus()

    def _update_progress(self) -> None:
        self._update()

    def _on_button_pressed(self, event: Button.Pressed) -> None:
        for op_item in self._operation_items.values():
            if event.button is op_item.abort_button:
                op_item.operation.abort()
                self._update()
