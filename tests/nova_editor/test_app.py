"""Tests for nova_editor app functionality."""

import tempfile
from pathlib import Path

import pytest

from nova_editor.app import NovaEditApp


@pytest.mark.asyncio
async def test_app_loads_file() -> None:
    """Test that the app can load a file."""
    with tempfile.NamedTemporaryFile(mode="w", delete=False, suffix=".txt") as f:
        test_content = "Hello from test file"
        f.write(test_content)
        f.flush()
        temp_path = Path(f.name)

    try:
        app = NovaEditApp(file_path=temp_path)

        async with app.run_test() as pilot:
            await pilot.pause()
            assert app.editor is not None
            assert test_content in app.editor.text
    finally:
        temp_path.unlink()


@pytest.mark.asyncio
async def test_app_saves_file() -> None:
    """Test that the app can save a file."""
    with tempfile.NamedTemporaryFile(mode="w", delete=False, suffix=".txt") as f:
        f.write("initial content")
        f.flush()
        temp_path = Path(f.name)

    try:
        app = NovaEditApp(file_path=temp_path)

        async with app.run_test() as pilot:
            await pilot.pause()
            assert app.editor is not None

            # Modify the editor content
            await pilot.press("ctrl+a")  # Select all
            await pilot.pause()
            await pilot.press("delete")  # Delete
            await pilot.pause()

            # Type new content
            await pilot.press("s", "a", "v", "e", "d")
            await pilot.pause()

            # Save
            app.action_save()
            await pilot.pause()

        # Verify file was saved
        saved_content = temp_path.read_text()
        assert "saved" in saved_content.lower()

    finally:
        temp_path.unlink()


@pytest.mark.asyncio
async def test_app_without_file() -> None:
    """Test that the app can run without a file."""
    app = NovaEditApp(file_path=None)

    async with app.run_test() as pilot:
        await pilot.pause()
        assert app.editor is not None
        # Should start with empty text
        assert app.editor.text == ""


@pytest.mark.asyncio
async def test_app_handles_missing_file() -> None:
    """Test that the app handles missing file gracefully."""
    nonexistent_path = Path(tempfile.gettempdir()) / "nonexistent_file_xyz_12345.txt"

    app = NovaEditApp(file_path=nonexistent_path)

    async with app.run_test() as pilot:
        await pilot.pause()
        assert app.editor is not None
        # Should handle gracefully with empty editor
        assert app.editor.text == ""


@pytest.mark.asyncio
async def test_app_quit_action() -> None:
    """Test quit action."""
    app = NovaEditApp(file_path=None)

    async with app.run_test() as pilot:
        await pilot.pause()
        # The quit action calls app.exit(), which should end the test
        await app.action_quit()
        # If we get here without error, quit worked
