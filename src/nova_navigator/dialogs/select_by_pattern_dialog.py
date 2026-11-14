from textual.app import ComposeResult
from textual.widgets import Static

from nova_navigator.file_filter import FilenamePatternFilter
from nova_widgets import Checkbox, DefaultButton, Dialog, Input


class SelectByPatternDialog(Dialog):
    """Ask for a file name pattern to select or unselect files in a panel.

    The pattern uses the copy-filter syntax (patterns separated by ``;``).
    The "Include folders" state is shared between the select and the unselect
    dialog; the caller passes it in and saves it after OK.
    After OK, ``pattern`` and ``include_folders`` hold the entered values.
    """

    AUTO_FOCUS = "#pattern_input"

    pattern: str
    include_folders: bool

    def __init__(self, select: bool, include_folders: bool = False, pattern: str = "*", id: str | None = None) -> None:
        self.pattern = pattern
        self.include_folders = include_folders
        super().__init__(
            title="Select By Pattern" if select else "Unselect By Pattern",
            id=id,
            buttons=[DefaultButton.OK, DefaultButton.CANCEL],
        )

    def compose_content(self) -> ComposeResult:
        yield Static("Pattern:")
        yield Static("Separate patterns with ;")
        yield Input(value=self.pattern, id="pattern_input")
        yield Checkbox("Include folders", self.include_folders, id="include_folders")

    @property
    def file_filter(self) -> FilenamePatternFilter:
        """The filter built from the current pattern; an empty pattern matches everything."""
        return FilenamePatternFilter.from_pattern_string(self.pattern)

    def action_accept_dialog(self) -> None:
        self.pattern = self.query_one("#pattern_input", Input).value
        self.include_folders = self.query_one("#include_folders", Checkbox).value
        super().action_accept_dialog()
