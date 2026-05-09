from textual import events
from textual.widgets import Static

from nova_navigator.vfs.filesystem import VPath
from nova_widgets import Input

from ..widgets import NoSelectListView
from .dialog import ComposeResult, DefaultButton, Dialog


class CopyMoveFilesDialog(Dialog):
    AUTO_FOCUS = "Input"
    MAX_DISPLAYED_FILES = 10

    filename: str | None = None  # set to the entered filename when OK is confirmed

    DEFAULT_CSS = """
    CopyMoveFilesDialog {
        NoSelectListView {
            height: auto;
            max-height: 7;
            background: $surface;
            border: inner $surface;
        }

        #destination {
            border: inner $surface;
            background: $surface;
           }
    }
    """

    def __init__(
        self,
        source_paths: list[VPath],
        destination_path: VPath,
        move: bool,
        id: str | None = None,
    ) -> None:
        self.source_paths = source_paths
        self.destination_path = destination_path
        self.move = move
        super().__init__(title="Move" if move else "Copy", id=id, buttons=[DefaultButton.OK, DefaultButton.CANCEL])

    def compose_content(self) -> ComposeResult:
        move_or_copy = "Move" if self.move else "Copy"

        # file = self.source_paths[0] if len(self.source_paths) == 1 else f"{len(self.source_paths)} files"
        self._source_files = NoSelectListView()
        yield Static(f"{move_or_copy} {len(self.source_paths)} files:")  # Spacer
        yield self._source_files

        yield Static("")  # Spacer
        yield Static("To:")
        yield Static(f"{self.destination_path.compact_path_str}", id="destination")

        if len(self.source_paths) == 1:
            yield Static("")  # Spacer
            yield Static("Filename:")
            yield Input(value=self.source_paths[0].name, placeholder="Enter filename")

    def _capture_filename(self) -> None:
        """Read the Input value and store it in self.filename before dismissing."""
        if len(self.source_paths) == 1:
            value = self.query_one(Input).value.strip()
            self.filename = value if value else self.source_paths[0].name

    def action_accept_dialog(self) -> None:
        self._capture_filename()
        super().action_accept_dialog()

    def _on_mount(self, event: events.Mount) -> None:
        for path in self.source_paths[0 : self.MAX_DISPLAYED_FILES]:
            self._source_files.append(NoSelectListView.ListItem(Static(path.name)))
        if len(self.source_paths) > self.MAX_DISPLAYED_FILES:
            self._source_files.append(
                NoSelectListView.ListItem(
                    Static(f"... and {len(self.source_paths) - self.MAX_DISPLAYED_FILES} more files")
                )
            )


class DeleteFilesDialog(Dialog):
    AUTO_FOCUS = "Button"

    def __init__(
        self,
        paths: list[VPath],
        id: str | None = None,
    ) -> None:
        self.paths = paths
        super().__init__(title="Delete", id=id, buttons=[DefaultButton.YES, DefaultButton.NO])

    def compose_content(self) -> ComposeResult:
        file = self.paths[0] if len(self.paths) == 1 else f"{len(self.paths)} files"
        yield Static(f"Delete {file}?")
