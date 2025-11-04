from textual.widgets import Input, Static

from .dialog import ComposeResult, DefaultButton, Dialog


class CopyMoveFilesDialog(Dialog):
    AUTO_FOCUS = "Button"

    def __init__(
        self,
        source_paths: list[str],
        destination_path: str,
        move: bool,
        id: str | None = None,
    ) -> None:
        self.source_paths = source_paths
        self.destination_path = destination_path
        self.move = move
        super().__init__(title="Move" if move else "Copy", id=id, buttons=[DefaultButton.OK, DefaultButton.CANCEL])

    def compose_content(self) -> ComposeResult:
        move_or_copy = "Move" if self.move else "Copy"
        file = self.source_paths[0] if len(self.source_paths) == 1 else f"{len(self.source_paths)} files"
        yield Static(f"{move_or_copy} {file}")
        yield Static("to:")
        yield Input(f"{self.destination_path}")


class DeleteFilesDialog(Dialog):
    AUTO_FOCUS = "Button"

    def __init__(
        self,
        paths: list[str],
        id: str | None = None,
    ) -> None:
        self.paths = paths
        super().__init__(title="Delete", id=id, buttons=[DefaultButton.YES, DefaultButton.NO])

    def compose_content(self) -> ComposeResult:
        file = self.paths[0] if len(self.paths) == 1 else f"{len(self.paths)} files"
        yield Static(f"Delete {file}?")
