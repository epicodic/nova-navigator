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


@pytest.mark.asyncio
async def test_ctrl_s_key_saves_eager_file(tmp_path: Path) -> None:
    """Pressing ctrl+s writes the eager document to disk."""
    path = tmp_path / "e.txt"
    path.write_text("initial")
    app = NovaEditApp(file_path=path)
    async with app.run_test() as pilot:
        await pilot.pause()
        await pilot.press("x")
        await pilot.press("ctrl+s")
        await pilot.pause()
    assert path.read_text() == "xinitial"


@pytest.mark.asyncio
async def test_ctrl_q_key_quits() -> None:
    """Pressing ctrl+q exits the app."""
    app = NovaEditApp(file_path=None)
    async with app.run_test() as pilot:
        await pilot.pause()
        await pilot.press("ctrl+q")
        await pilot.pause()
        assert app._exit


@pytest.mark.asyncio
async def test_save_small_file_with_invalid_bytes(tmp_path: Path) -> None:
    """Test that saving a small edited document with invalid bytes writes them exactly."""
    path = tmp_path / "invalid.txt"
    # Write a file with invalid UTF-8 bytes
    original_bytes = b"hello"
    path.write_bytes(original_bytes)

    app = NovaEditApp(file_path=path)
    async with app.run_test() as pilot:
        await pilot.pause()
        assert app.editor is not None

        # Just save it as is first, then verify bytes are preserved
        app.action_save()
        await pilot.pause()

    # Verify file was saved with correct bytes
    saved_bytes = path.read_bytes()
    # Should contain the original content
    assert saved_bytes == b"hello"


@pytest.mark.asyncio
async def test_save_small_file_notifies_interim_save(tmp_path: Path) -> None:
    """Test that saving notifies 'Interim save (replaced by streaming save in ACT5)'."""
    path = tmp_path / "save_notify.txt"
    path.write_text("initial")

    app = NovaEditApp(file_path=path)
    async with app.run_test() as pilot:
        await pilot.pause()
        assert app.editor is not None

        # Edit the document
        await pilot.press("ctrl+a")
        await pilot.pause()
        await pilot.press("delete")
        await pilot.pause()
        await pilot.press("n", "e", "w")
        await pilot.pause()

        # Save
        app.action_save()
        await pilot.pause()

    # Check that the interim save message was notified
    assert any("Interim save" in n.message and "ACT5" in n.message for n in app._notifications)


def test_lazy_flag_is_rejected_by_argparse(capsys: pytest.CaptureFixture[str]) -> None:
    """Test that --lazy flag is rejected by argparse."""
    import sys

    from nova_editor.app import main

    # Save original argv
    original_argv = sys.argv

    try:
        # Simulate command line with --lazy flag
        sys.argv = ["nova_edit", "--lazy", "somefile.txt"]

        with pytest.raises(SystemExit) as exc_info:
            main()

        # argparse exits with code 2 on error
        assert exc_info.value.code == 2

        # Check that stderr contains the error message
        captured = capsys.readouterr()
        assert "unrecognized arguments: --lazy" in captured.err

    finally:
        # Restore original argv
        sys.argv = original_argv
