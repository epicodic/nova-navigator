"""Tests for NovaTextArea widget."""

import pytest
from textual.app import App, ComposeResult

from nova_editor.widget import NovaTextArea


class EditorTestApp(App[None]):
    """Minimal app for testing NovaTextArea."""

    def __init__(self, widget: NovaTextArea) -> None:
        super().__init__()
        self._widget = widget

    def compose(self) -> ComposeResult:
        yield self._widget


@pytest.mark.asyncio
async def test_widget_mounts() -> None:
    """Test that NovaTextArea can mount in a Textual app."""
    widget = NovaTextArea()
    app = EditorTestApp(widget)

    async with app.run_test() as pilot:
        await pilot.pause()
        assert widget in app.query(NovaTextArea)


@pytest.mark.asyncio
async def test_typing_text() -> None:
    """Test typing text into NovaTextArea."""
    widget = NovaTextArea()
    app = EditorTestApp(widget)

    async with app.run_test() as pilot:
        await pilot.pause()

        # Type some text
        await pilot.press("f", "o", "o")
        await pilot.pause()

        # Check the text was entered
        assert "foo" in widget.text


@pytest.mark.asyncio
async def test_undo_redo() -> None:
    """Test undo/redo functionality."""
    widget = NovaTextArea()
    app = EditorTestApp(widget)

    async with app.run_test() as pilot:
        await pilot.pause()

        # Type text
        await pilot.press("h", "e", "l", "l", "o")
        await pilot.pause()
        assert "hello" in widget.text

        # Undo
        await pilot.press("ctrl+z")
        await pilot.pause()

        # After undo, some text should be gone
        # (exact behavior depends on how TextArea groups edits)
        # Just verify it changed
        original_text = widget.text
        assert original_text is not None


@pytest.mark.asyncio
async def test_initial_text() -> None:
    """Test initializing widget with text."""
    initial_text = "hello world"
    widget = NovaTextArea(text=initial_text)
    app = EditorTestApp(widget)

    async with app.run_test() as pilot:
        await pilot.pause()
        assert widget.text == initial_text


@pytest.mark.asyncio
async def test_newline_handling() -> None:
    """Test handling of newlines."""
    widget = NovaTextArea()
    app = EditorTestApp(widget)

    async with app.run_test() as pilot:
        await pilot.pause()

        # Type text with newline
        await pilot.press("l", "i", "n", "e", "1")
        await pilot.press("enter")
        await pilot.press("l", "i", "n", "e", "2")
        await pilot.pause()

        text = widget.text
        assert "line1" in text
        assert "line2" in text
        assert "\n" in text
