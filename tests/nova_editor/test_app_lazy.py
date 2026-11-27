"""Tests for lazy loading in nova_edit app."""

from __future__ import annotations

from pathlib import Path

import pytest

from nova_editor.app import NovaEditApp
from nova_editor.document._lazy_document import LazyDocument
from tests.nova_editor.helpers_view import make_mixed
from tests.nova_editor.save_widget_helpers import wait_saved


@pytest.mark.asyncio
async def test_large_file_opens_lazily_and_is_editable(tmp_path: Path) -> None:
    """Test that large files open lazily and are editable."""
    path = make_mixed(tmp_path / "m.txt")
    app = NovaEditApp(file_path=path)
    async with app.run_test(size=(80, 20)) as pilot:
        await pilot.pause()
        assert app.editor is not None
        assert isinstance(app.editor.document, LazyDocument)
        assert not app.editor.read_only
        await pilot.press("f10")
        await pilot.pause()
        assert app.editor.soft_wrap is True
        # Test goto popup visibility
        await pilot.press("ctrl+g")
        await pilot.pause()
        assert app.goto_popup.display is True
        # Try goto line 3
        await pilot.press(*"3", "enter")
        await pilot.pause()
        assert app.editor.cursor_location[0] == 2


@pytest.mark.asyncio
async def test_small_file_opens_on_the_lazy_document_and_is_editable(tmp_path: Path) -> None:
    """Test that small files use the same lazy document (DEC-17) and are editable."""
    path = tmp_path / "s.txt"
    path.write_text("hello")
    app = NovaEditApp(file_path=path)
    async with app.run_test() as pilot:
        await pilot.pause()
        assert app.editor is not None
        assert isinstance(app.editor.document, LazyDocument)
        assert app.editor.text == "hello"
        assert not app.editor.read_only


@pytest.mark.asyncio
async def test_ctrl_s_saves_a_lazy_document(tmp_path: Path) -> None:
    """Ctrl+S of a lazily opened file streams it out: the edit is in the file and the rest is unchanged."""
    path = make_mixed(tmp_path / "m.txt")
    app = NovaEditApp(file_path=path)
    async with app.run_test() as pilot:
        await pilot.pause()
        assert app.editor is not None
        assert isinstance(app.editor.document, LazyDocument)

        before = path.read_bytes()
        await pilot.press("x")
        await pilot.press("ctrl+s")
        await wait_saved(pilot, app.editor)

        assert path.read_bytes() == b"x" + before


@pytest.mark.asyncio
async def test_timing_hook_writes_file(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    """Test that NOVA_EDIT_TIMING_FILE env var causes timing hook to write."""
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
    path = make_mixed(tmp_path / "m.txt")
    timing_file = tmp_path / "timing_no_env.txt"

    app = NovaEditApp(file_path=path)
    async with app.run_test() as pilot:
        await pilot.pause()
        await pilot.pause(delay=0.5)

    # Timing file should NOT have been created
    assert not timing_file.exists()
