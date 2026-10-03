"""Test host application for EditorScreen testing."""

from __future__ import annotations

from pathlib import Path

from textual.app import App

from nova_editor.screen import EditorScreen
from nova_widgets.file_provider import InMemoryFileProvider
from nova_widgets.keybindings_config import KeybindingsConfig


class EditorScreenHost(App[None]):
    """Test host for the EditorScreen."""

    def __init__(
        self,
        path: Path | None = None,
        *,
        keybindings: KeybindingsConfig | None = None,
        standalone: bool = False,
    ) -> None:
        super().__init__()
        self.path = path
        self.keybindings = keybindings
        self.standalone = standalone
        self.screen_instance: EditorScreen | None = None

    def on_mount(self) -> None:
        self.screen_instance = EditorScreen(
            path=self.path,
            keybindings=self.keybindings,
            file_provider=InMemoryFileProvider(),
            standalone=self.standalone,
        )
        self.push_screen(self.screen_instance)
