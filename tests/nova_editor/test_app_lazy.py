"""Tests for lazy loading in nova_edit app."""

from __future__ import annotations

from pathlib import Path

import pytest

from nova_editor import app as app_module
from nova_editor.app import NovaEditApp
from tests.nova_editor.helpers_view import make_mixed


@pytest.mark.asyncio
async def test_large_file_opens_lazy_and_read_only(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    """Test that large files open lazily and are read-only."""
    monkeypatch.setattr(app_module, "EAGER_LIMIT", 100)
    path = make_mixed(tmp_path / "m.txt")
    app = NovaEditApp(file_path=path)
    async with app.run_test(size=(80, 20)) as pilot:
        await pilot.pause()
        assert app.editor is not None
        assert app.editor.is_lazy
        assert app.editor.read_only
        await pilot.press("f4")
        await pilot.pause()
        assert app.editor.soft_wrap is True
        # Test goto bar visibility
        await pilot.press("ctrl+g")
        await pilot.pause()
        assert app.goto_bar is not None
        assert app.goto_bar.display is True
        # Try goto line 3
        await pilot.press(*"3", "enter")
        await pilot.pause()
        assert app.editor.cursor_location[0] == 2


@pytest.mark.asyncio
async def test_small_file_keeps_stock_editable_path(tmp_path: Path) -> None:
    """Test that small files use stock editable path."""
    path = tmp_path / "s.txt"
    path.write_text("hello")
    app = NovaEditApp(file_path=path)
    async with app.run_test() as pilot:
        await pilot.pause()
        assert app.editor is not None
        assert not app.editor.is_lazy
        assert not app.editor.read_only


@pytest.mark.asyncio
async def test_lazy_flag_forces_lazy_open(tmp_path: Path) -> None:
    """Test that --lazy flag forces lazy loading even for small files."""
    # Create a small file
    path = tmp_path / "s.txt"
    path.write_text("small")

    # Force lazy mode regardless of size
    app = NovaEditApp(file_path=path, lazy=True)
    async with app.run_test() as pilot:
        await pilot.pause()
        assert app.editor is not None
        assert app.editor.is_lazy
        assert app.editor.read_only


@pytest.mark.asyncio
async def test_f4_toggles_wrap(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    """Test that F4 toggle wrap action works."""
    monkeypatch.setattr(app_module, "EAGER_LIMIT", 50)
    path = make_mixed(tmp_path / "m.txt")
    app = NovaEditApp(file_path=path)
    async with app.run_test() as pilot:
        await pilot.pause()
        assert app.editor is not None

        # Initial wrap state is False
        initial_wrap = app.editor.soft_wrap

        await pilot.press("f4")
        await pilot.pause()

        # Wrap state should be toggled
        assert app.editor.soft_wrap != initial_wrap

        # Press again to toggle back
        await pilot.press("f4")
        await pilot.pause()

        # Should be back to initial state
        assert app.editor.soft_wrap == initial_wrap


@pytest.mark.asyncio
async def test_ctrl_g_shows_goto_bar(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    """Test that Ctrl+G show goto bar action works."""
    monkeypatch.setattr(app_module, "EAGER_LIMIT", 50)
    path = make_mixed(tmp_path / "m.txt")
    app = NovaEditApp(file_path=path)
    async with app.run_test() as pilot:
        await pilot.pause()
        assert app.goto_bar is not None

        # GotoBar should be hidden initially
        initial_visible = app.goto_bar.display

        await pilot.press("ctrl+g")
        await pilot.pause()

        # GotoBar should now be visible and focused
        assert initial_visible is False
        assert app.goto_bar.display is True
        assert app.focused is app.goto_bar


@pytest.mark.asyncio
async def test_escape_closes_goto_bar_and_refocuses_editor(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    """Escape hides the GotoBar and returns the focus to the editor."""
    monkeypatch.setattr(app_module, "EAGER_LIMIT", 50)
    path = make_mixed(tmp_path / "m.txt")
    app = NovaEditApp(file_path=path)
    async with app.run_test(size=(80, 20)) as pilot:
        await pilot.pause()
        assert app.goto_bar is not None
        await pilot.press("ctrl+g")
        await pilot.pause()
        assert app.goto_bar.display is True
        await pilot.press("4", "escape")
        await pilot.pause()
        assert app.goto_bar.display is False
        assert app.focused is app.editor
        assert app.editor is not None
        assert app.editor.cursor_location[0] == 0


@pytest.mark.asyncio
async def test_goto_line_navigation(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    """Test goto line navigation."""
    monkeypatch.setattr(app_module, "EAGER_LIMIT", 50)
    path = make_mixed(tmp_path / "m.txt")
    app = NovaEditApp(file_path=path)
    async with app.run_test(size=(80, 20)) as pilot:
        await pilot.pause()
        assert app.editor is not None

        # Go to line 5 (0-indexed, so line index 4)
        app.editor.goto_line(5)
        await pilot.pause()

        # Cursor should be at line 4 (0-indexed)
        assert app.editor.cursor_location[0] == 4


@pytest.mark.asyncio
async def test_goto_byte_navigation(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    """Test goto byte offset navigation."""
    monkeypatch.setattr(app_module, "EAGER_LIMIT", 50)
    path = make_mixed(tmp_path / "m.txt")
    app = NovaEditApp(file_path=path)
    async with app.run_test(size=(80, 20)) as pilot:
        await pilot.pause()
        assert app.editor is not None

        assert app.editor.cursor_location == (0, 0)
        await pilot.press("ctrl+g")
        await pilot.press("@", "1", "0", "enter")
        await pilot.pause(delay=0.2)

        # make_mixed starts with ASCII text, so byte 10 of line 0 is column 10
        assert app.editor.cursor_location == (0, 10)


@pytest.mark.asyncio
async def test_ctrl_s_on_lazy_notifies_read_only(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    """Test that Ctrl+S on a lazy widget notifies about read-only."""
    monkeypatch.setattr(app_module, "EAGER_LIMIT", 50)
    path = make_mixed(tmp_path / "m.txt")
    app = NovaEditApp(file_path=path)
    async with app.run_test() as pilot:
        await pilot.pause()
        assert app.editor is not None
        assert app.editor.is_lazy

        before = path.read_bytes()
        await pilot.press("ctrl+s")
        await pilot.pause()

        assert path.read_bytes() == before
        assert any("Read-only" in n.message for n in app._notifications)


@pytest.mark.asyncio
async def test_timing_hook_writes_file(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    """Test that NOVA_EDIT_TIMING_FILE env var causes timing hook to write."""
    monkeypatch.setattr(app_module, "EAGER_LIMIT", 50)
    path = make_mixed(tmp_path / "m.txt")
    timing_file = tmp_path / "timing.txt"

    # Set the environment variable
    monkeypatch.setenv("NOVA_EDIT_TIMING_FILE", str(timing_file))

    app = NovaEditApp(file_path=path)
    async with app.run_test() as pilot:
        await pilot.pause()
        # Wait a bit for rendering to happen
        await pilot.pause(delay=0.5)

    assert timing_file.exists()
    assert timing_file.read_text().startswith("FIRST_CONTENT ")


@pytest.mark.asyncio
async def test_timing_hook_no_file_without_env(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    """Test that timing hook does nothing when env var is not set."""
    monkeypatch.delenv("NOVA_EDIT_TIMING_FILE", raising=False)
    monkeypatch.setattr(app_module, "EAGER_LIMIT", 50)
    path = make_mixed(tmp_path / "m.txt")
    timing_file = tmp_path / "timing_no_env.txt"

    app = NovaEditApp(file_path=path)
    async with app.run_test() as pilot:
        await pilot.pause()
        await pilot.pause(delay=0.5)

    # Timing file should NOT have been created
    assert not timing_file.exists()
