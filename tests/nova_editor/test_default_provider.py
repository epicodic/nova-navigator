"""The editor browses the local file system unless the host passes a provider (ADR-2, DEC-32 A3)."""

from __future__ import annotations

from nova_editor.app import NovaEditApp
from nova_editor.screen import EditorScreen
from nova_widgets.file_provider import InMemoryFileProvider, LocalFileProvider, default_file_provider


def test_the_screen_defaults_to_the_local_provider() -> None:
    screen = EditorScreen()
    assert isinstance(screen.file_provider, LocalFileProvider)
    assert screen.file_provider is default_file_provider()


def test_a_given_provider_is_kept() -> None:
    provider = InMemoryFileProvider()
    assert EditorScreen(file_provider=provider).file_provider is provider


def test_the_standalone_app_hands_the_default_on() -> None:
    screen = NovaEditApp().get_default_screen()
    assert isinstance(screen.file_provider, LocalFileProvider)
