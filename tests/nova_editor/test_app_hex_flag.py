"""The `--hex` flag of `ned` (S0003 ACT7, REQ-4)."""

from __future__ import annotations

from pathlib import Path

import pytest

from nova_editor.app import NovaEditApp, build_app
from nova_editor.screen import EditorScreen
from tests.nova_editor.helpers_view import wait_until

SIZE = (100, 30)


@pytest.fixture(autouse=True)
def quiet(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    """No poll during a test, and no key file of the real home."""
    monkeypatch.setattr(NovaEditApp, "POLL_SECONDS", 3600.0)
    monkeypatch.setenv("HOME", str(tmp_path / "home"))


def screen_of(app: NovaEditApp) -> EditorScreen:
    screen = app.screen
    assert isinstance(screen, EditorScreen)
    return screen


def start_hex_of(app: NovaEditApp) -> bool:
    return app.start_hex


def test_hex_flag_parses_with_a_file(tmp_path: Path) -> None:
    path = tmp_path / "t.txt"
    path.write_text("x\n")
    app = build_app(["--hex", str(path)])
    assert start_hex_of(app) is True
    assert app.path == path


def test_hex_flag_parses_without_a_file() -> None:
    app = build_app(["--hex"])
    assert start_hex_of(app) is True
    assert app.path is None


def test_no_flag_means_detection(tmp_path: Path) -> None:
    path = tmp_path / "t.txt"
    path.write_text("x\n")
    assert start_hex_of(build_app([str(path)])) is False
    assert start_hex_of(build_app([])) is False


def test_help_lists_the_flag(capsys: pytest.CaptureFixture[str]) -> None:
    with pytest.raises(SystemExit) as exit_info:
        build_app(["--help"])
    assert exit_info.value.code == 0
    assert "--hex" in capsys.readouterr().out


@pytest.mark.asyncio
async def test_a_text_file_with_the_flag_opens_in_hex(tmp_path: Path) -> None:
    path = tmp_path / "t.txt"
    path.write_text("hello\n")
    app = build_app(["--hex", str(path)])
    async with app.run_test(size=SIZE) as pilot:
        await pilot.pause()
        screen = screen_of(app)
        assert screen.document.hex_mode
        await wait_until(pilot, lambda: screen.focused is screen.document.hex_view)
        assert screen.document.hex_view.length == 6


@pytest.mark.asyncio
async def test_the_empty_document_with_the_flag_opens_in_hex() -> None:
    app = build_app(["--hex"])
    async with app.run_test(size=SIZE) as pilot:
        await pilot.pause()
        screen = screen_of(app)
        assert screen.document.hex_mode
        assert screen.document.hex_view.length == 0


@pytest.mark.asyncio
async def test_a_missing_file_with_the_flag_opens_the_empty_document_in_hex(tmp_path: Path) -> None:
    app = build_app(["--hex", str(tmp_path / "missing.bin")])
    async with app.run_test(size=SIZE) as pilot:
        await pilot.pause()
        screen = screen_of(app)
        assert screen.document.load_state == "new"
        assert screen.document.hex_mode


@pytest.mark.asyncio
async def test_f4_after_the_flag_returns_to_text(tmp_path: Path) -> None:
    path = tmp_path / "t.txt"
    path.write_text("hello\n")
    app = build_app(["--hex", str(path)])
    async with app.run_test(size=SIZE) as pilot:
        await pilot.pause()
        screen = screen_of(app)
        await pilot.press("f4")
        await wait_until(pilot, lambda: not screen.document.hex_mode and screen.focused is screen.document.editor)
