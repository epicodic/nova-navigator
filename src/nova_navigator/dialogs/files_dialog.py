from textual import events
from textual.app import ComposeResult
from textual.containers import Horizontal
from textual.css.query import NoMatches
from textual.widgets import Collapsible, Rule, Static

from nova_navigator.file_filter import FileFilter, FilenamePatternFilter
from nova_navigator.vfs.filesystem import VPath
from nova_widgets import Checkbox, DefaultButton, Dialog, Input

from ..widgets import NoSelectListView


class CopyMoveFilesDialog(Dialog):
    AUTO_FOCUS = "#filename_input"
    MAX_DISPLAYED_FILES = 10

    filename: str | None = None  # set to the entered filename when OK is confirmed
    follow_links: bool = False  # initial state; after OK it holds the chosen state
    stable_symlinks: bool = False  # initial state; after OK it holds the chosen state

    DEFAULT_CSS = """
    CopyMoveFilesDialog {
        #dialog_box {
            min-width: 46;  /* room for both link options side by side */
            padding: 0 1;  /* fit the single-file Move dialog in an 80x24 terminal */
        }

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
            margin-top: 0;
        }

        #filter_pattern {
            border: inner $surface;
            background: $surface;
        }

        Rule.-separator {
            margin: 0;
            color: $foreground 30%;
        }

        #link_options {
            height: auto;

            Checkbox {
                width: 1fr;
                height: 1;
                border: none;

                &:focus {
                    border: none;
                }
            }
        }
    }
    """

    def __init__(
        self,
        source_paths: list[VPath],
        destination_path: VPath,
        move: bool,
        follow_links: bool = False,
        stable_symlinks: bool = False,
        id: str | None = None,
    ) -> None:
        self.source_paths = source_paths
        self.destination_path = destination_path
        self.move = move
        self.follow_links = follow_links
        self.stable_symlinks = stable_symlinks
        super().__init__(title="Move" if move else "Copy", id=id, buttons=[DefaultButton.OK, DefaultButton.CANCEL])

    def compose_content(self) -> ComposeResult:
        move_or_copy = "Move" if self.move else "Copy"

        self._source_files = NoSelectListView()
        count = len(self.source_paths)
        yield Static(f"{move_or_copy} {count} {'file' if count == 1 else 'files'}:")
        yield self._source_files

        yield Static("")  # Spacer
        yield Static("To:")
        yield Static(f"{self.destination_path.compact_path_str}", id="destination")

        if len(self.source_paths) == 1:
            yield Static("Filename:")
            yield Input(value=self.source_paths[0].name, placeholder="Enter filename", id="filename_input")

        yield Rule(classes="-separator")
        yield Horizontal(
            Checkbox("Follow links", self.follow_links, id="follow_links"),
            Checkbox("Stable symlinks", self.stable_symlinks, id="stable_symlinks"),
            id="link_options",
        )
        yield Rule(classes="-separator")

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

    def _capture_link_options(self) -> None:
        """Read the two link checkboxes and store their states before dismissing."""
        self.follow_links = self.query_one("#follow_links", Checkbox).value
        self.stable_symlinks = self.query_one("#stable_symlinks", Checkbox).value

    def action_accept_dialog(self) -> None:
        self._capture_filename()
        self._capture_link_options()
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

    @staticmethod
    def _is_link(path: VPath) -> bool:
        """Return whether *path* is known to be a link; uses only the cached stat (no I/O on the UI thread)."""
        stat = path.cached_stat
        return stat is not None and stat.is_symlink is True

    def compose_content(self) -> ComposeResult:
        link_count = sum(1 for path in self.paths if self._is_link(path))
        if len(self.paths) == 1:
            if link_count == 1:
                yield Static(f"Delete link '{self.paths[0]}'?")
                yield Static("Only the link is deleted; what it points to is not touched.")
            else:
                yield Static(f"Delete {self.paths[0]}?")
        else:
            yield Static(f"Delete {len(self.paths)} files?")
            if link_count > 0:
                yield Static(f"{link_count} of them are links; only the links are deleted, not what they point to.")
