from textual import events
from textual.css.query import NoMatches
from textual.widgets import Collapsible, Static

from nova_navigator.file_filter import FileFilter, FilenamePatternFilter
from nova_navigator.vfs.filesystem import VPath
from nova_widgets import Input

from ..widgets import NoSelectListView
from .dialog import ComposeResult, DefaultButton, Dialog


class CopyMoveFilesDialog(Dialog):
    AUTO_FOCUS = "#filename_input"
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

        Collapsible {
            border: none;
            padding: 0;
            margin-top: 1;
        }

        #filter_pattern {
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

        self._source_files = NoSelectListView()
        yield Static(f"{move_or_copy} {len(self.source_paths)} files:")
        yield self._source_files

        yield Static("")  # Spacer
        yield Static("To:")
        yield Static(f"{self.destination_path.compact_path_str}", id="destination")

        if len(self.source_paths) == 1:
            yield Static("")  # Spacer
            yield Static("Filename:")
            yield Input(value=self.source_paths[0].name, placeholder="Enter filename", id="filename_input")

        yield Collapsible(
            Static("Pattern:"),
            Input(value="*", id="filter_pattern"),
            title="Filtering",
            collapsed=True,
        )

    @property
    def file_filter(self) -> FileFilter | None:
        """Return a FileFilter based on the pattern input, or None if the pattern matches everything."""
        try:
            collapsible = self.query_one(Collapsible)
        except NoMatches:
            return None
        if collapsible.collapsed:
            return None
        value = self.query_one("#filter_pattern", Input).value.strip()
        if not value or value == "*":
            return None
        return FilenamePatternFilter.from_pattern_string(value)

    def _capture_filename(self) -> None:
        """Read the filename Input value and store it in self.filename before dismissing."""
        if len(self.source_paths) == 1:
            try:
                inp = self.query_one("#filename_input", Input)
            except NoMatches:
                return
            value = inp.value.strip()
            self.filename = value if value else self.source_paths[0].name

    def action_accept_dialog(self) -> None:
        self._capture_filename()
        super().action_accept_dialog()

    def _on_mount(self, event: events.Mount) -> None:
        for path in self.source_paths[0 : self.MAX_DISPLAYED_FILES]:
            self._source_files.append(NoSelectListView.ListItem(Static(path.name)))
        if len(self.source_paths) > self.MAX_DISPLAYED_FILES:
            self._source_files.append(NoSelectListView.ListItem(Static(f"... and {len(self.source_paths) - self.MAX_DISPLAYED_FILES} more files")))


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
