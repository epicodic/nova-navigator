"""Test timing file handling in EditorScreen (fix for lost NOVA_EDIT_TIMING_FILE env var)."""

from __future__ import annotations

from pathlib import Path

import pytest

from tests.nova_editor.screen_host import EditorScreenHost


@pytest.mark.asyncio
async def test_editor_screen_writes_first_content_timing_when_env_var_is_set(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    """With NOVA_EDIT_TIMING_FILE set, mounting the editor screen should write a FIRST_CONTENT line."""
    # Create a small test file
    test_file = tmp_path / "test.txt"
    test_file.write_text("Hello, World!")

    # Create a temporary file for timing output
    timing_file = tmp_path / "timing.txt"

    # Set the env var
    monkeypatch.setenv("NOVA_EDIT_TIMING_FILE", str(timing_file))

    # Mount the editor screen with the test file
    host = EditorScreenHost(path=test_file)
    async with host.run_test() as pilot:
        await pilot.pause()  # Let the app settle after mount

        # The timing file should have been written with FIRST_CONTENT
        assert timing_file.exists(), f"timing_file {timing_file} was not created"
        content = timing_file.read_text()
        assert content.startswith("FIRST_CONTENT "), f"Expected 'FIRST_CONTENT' line, got: {content}"
