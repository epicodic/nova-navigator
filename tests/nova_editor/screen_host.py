"""Test host application for EditorScreen testing."""

from __future__ import annotations

from pathlib import Path

from textual.app import App

from nova_editor.screen import EditorScreen
from nova_widgets.file_provider import FileProvider, InMemoryFileProvider
from nova_widgets.key_types import KeySequence
from nova_widgets.keybindings_config import KeybindingsConfig


class EditorScreenHost(App[None]):
    """Test host for the EditorScreen."""

    def __init__(
        self,
        path: Path | None = None,
        *,
        keybindings: KeybindingsConfig | None = None,
        keyboard_shortcuts_item: bool = True,
        standalone: bool = False,
        file_provider: FileProvider | None = None,
    ) -> None:
        super().__init__()
        self.path = path
        self.keybindings = keybindings
        self.keyboard_shortcuts_item = keyboard_shortcuts_item
        self.standalone = standalone
        self.file_provider = file_provider if file_provider is not None else InMemoryFileProvider()
        self.screen_instance: EditorScreen | None = None
        self.closed = 0

    def on_mount(self) -> None:
        self.screen_instance = EditorScreen(
            path=self.path,
            keybindings=self.keybindings,
            keyboard_shortcuts_item=self.keyboard_shortcuts_item,
            file_provider=self.file_provider,
            standalone=self.standalone,
        )
        self.push_screen(self.screen_instance)

    def on_editor_screen_closed(self, message: EditorScreen.Closed) -> None:
        """Count when the editor screen closes."""
        self.closed += 1


def write_keys(config_dir: Path, bindings: dict[str, KeySequence | None]) -> KeybindingsConfig:
    """Write a real key configuration file (a `None` value unmaps the action) and load it."""
    config = KeybindingsConfig(config_dir)
    config.save(bindings)
    return config
