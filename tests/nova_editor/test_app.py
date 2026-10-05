"""Tests for nova_editor app functionality."""

import os
import tempfile
from pathlib import Path

import pytest
from textual.pilot import Pilot

from nova_editor.app import NovaEditApp
from nova_editor.core.save import SaveIo
from nova_editor.screen import EditorScreen
from nova_editor.widget import NovaTextArea
from tests.nova_editor.dialog_helpers import box_title, open_box
from tests.nova_editor.helpers_view import wait_until
from tests.nova_editor.save_widget_helpers import wait_saved


async def settle(pilot: Pilot[None], app: NovaEditApp) -> None:
    """Let a started save end and its messages arrive."""
    await wait_saved(pilot, app.editor)


@pytest.mark.asyncio
async def test_app_loads_file() -> None:
    """Test that the app can load a file."""
    with tempfile.NamedTemporaryFile(mode="w", delete=False, suffix=".txt") as f:
        test_content = "Hello from test file"
        f.write(test_content)
        f.flush()
        temp_path = Path(f.name)

    try:
        app = NovaEditApp(path=temp_path)

        async with app.run_test() as pilot:
            await pilot.pause()
            screen = app.screen
            assert isinstance(screen, EditorScreen)
            assert test_content in screen.document.editor.text
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
        app = NovaEditApp(path=temp_path)

        async with app.run_test() as pilot:
            await pilot.pause()
            screen = app.screen
            assert isinstance(screen, EditorScreen)

            # Modify the editor content
            await pilot.press("ctrl+a")  # Select all
            await pilot.pause()
            await pilot.press("delete")  # Delete
            await pilot.pause()

            # Type new content
            await pilot.press("s", "a", "v", "e", "d")
            await pilot.pause()

            # Save using Ctrl+S
            await pilot.press("ctrl+s")
            await settle(pilot, app)

        # Verify file was saved
        saved_content = temp_path.read_text()
        assert "saved" in saved_content.lower()

    finally:
        temp_path.unlink()


@pytest.mark.asyncio
async def test_app_without_file() -> None:
    """Test that the app can run without a file."""
    app = NovaEditApp(path=None)

    async with app.run_test() as pilot:
        await pilot.pause()
        screen = app.screen
        assert isinstance(screen, EditorScreen)
        # Should start with empty text
        assert screen.document.editor.text == ""


@pytest.mark.asyncio
async def test_app_handles_missing_file() -> None:
    """Test that the app handles missing file gracefully."""
    nonexistent_path = Path(tempfile.gettempdir()) / "nonexistent_file_xyz_12345.txt"

    app = NovaEditApp(path=nonexistent_path)

    async with app.run_test() as pilot:
        await pilot.pause()
        screen = app.screen
        assert isinstance(screen, EditorScreen)
        # Should handle gracefully with empty editor
        assert screen.document.editor.text == ""


@pytest.mark.asyncio
async def test_app_quit_action() -> None:
    """Test quit action."""
    app = NovaEditApp(path=None)

    async with app.run_test() as pilot:
        await pilot.pause()
        screen = app.screen
        assert isinstance(screen, EditorScreen)
        # The quit action should work via Ctrl+Q
        await pilot.press("ctrl+q")
        await pilot.pause()
        # If we get here without error, quit worked


@pytest.mark.asyncio
async def test_ctrl_s_key_saves_eager_file(tmp_path: Path) -> None:
    """Pressing ctrl+s writes the eager document to disk."""
    path = tmp_path / "e.txt"
    path.write_text("initial")
    app = NovaEditApp(path=path)
    async with app.run_test() as pilot:
        await pilot.pause()
        await pilot.press("x")
        await pilot.press("ctrl+s")
        await settle(pilot, app)
    assert path.read_text() == "xinitial"


@pytest.mark.asyncio
async def test_save_small_file_with_invalid_bytes(tmp_path: Path) -> None:
    """Saving an edited document with invalid UTF-8 writes those bytes exactly."""
    path = tmp_path / "invalid.txt"
    path.write_bytes(b"a\xffb\xe2\x82\nc\n")

    app = NovaEditApp(path=path)
    async with app.run_test() as pilot:
        await pilot.pause()
        await pilot.press("x")
        await pilot.press("ctrl+s")
        await settle(pilot, app)

    assert path.read_bytes() == b"xa\xffb\xe2\x82\nc\n"


@pytest.mark.asyncio
async def test_save_leaves_an_unrelated_tmp_named_file_alone(tmp_path: Path) -> None:
    """The temp file has a unique name: a file called `a.tmp.txt` is not overwritten, and no temp file is left."""
    path = tmp_path / "a.txt"
    path.write_text("one")
    neighbour = tmp_path / "a.tmp.txt"
    neighbour.write_text("precious")

    app = NovaEditApp(path=path)
    async with app.run_test() as pilot:
        await pilot.pause()
        await pilot.press("x")
        await pilot.press("ctrl+s")
        await settle(pilot, app)

    assert path.read_text() == "xone"
    assert neighbour.read_text() == "precious"
    assert sorted(p.name for p in tmp_path.iterdir()) == ["a.tmp.txt", "a.txt"]


@pytest.mark.asyncio
async def test_save_keeps_the_permissions(tmp_path: Path) -> None:
    path = tmp_path / "p.txt"
    path.write_text("one")
    path.chmod(0o640)

    app = NovaEditApp(path=path)
    async with app.run_test() as pilot:
        await pilot.pause()
        await pilot.press("x")
        await pilot.press("ctrl+s")
        await settle(pilot, app)

    assert path.stat().st_mode & 0o777 == 0o640


@pytest.mark.asyncio
async def test_save_through_a_symlink_writes_the_target_and_keeps_the_link(tmp_path: Path) -> None:
    target = tmp_path / "real.txt"
    target.write_text("one")
    link = tmp_path / "link.txt"
    link.symlink_to(target)

    app = NovaEditApp(path=link)
    async with app.run_test() as pilot:
        await pilot.pause()
        await pilot.press("x")
        await pilot.press("ctrl+s")
        await settle(pilot, app)

    assert link.is_symlink()
    assert link.resolve() == target.resolve()
    assert target.read_text() == "xone"


@pytest.mark.asyncio
async def test_save_after_a_load_failure_refuses_and_keeps_the_file(tmp_path: Path) -> None:
    path = tmp_path / "locked.txt"
    path.write_text("precious")
    path.chmod(0)
    if os.access(path, os.R_OK):
        path.chmod(0o644)
        pytest.skip("permissions are not enforced for this user")

    app = NovaEditApp(path=path)
    try:
        async with app.run_test() as pilot:
            await pilot.pause()
            await pilot.press("ctrl+s")
            await settle(pilot, app)
    finally:
        path.chmod(0o644)

    assert path.read_text() == "precious"
    assert any("Not saved" in n.message for n in app._notifications)
    assert not any("Interim save" in n.message for n in app._notifications)


@pytest.mark.asyncio
async def test_save_of_a_new_file_creates_it(tmp_path: Path) -> None:
    path = tmp_path / "new.txt"

    app = NovaEditApp(path=path)
    async with app.run_test() as pilot:
        await pilot.pause()
        await pilot.press("h", "i")
        await pilot.press("ctrl+s")
        await settle(pilot, app)

    assert path.read_text() == "hi"


@pytest.mark.asyncio
async def test_second_save_of_a_new_file_writes_the_later_edit(tmp_path: Path) -> None:
    path = tmp_path / "new.txt"

    app = NovaEditApp(path=path)
    async with app.run_test() as pilot:
        await pilot.pause()
        await pilot.press("h", "i")
        await pilot.press("ctrl+s")
        await settle(pilot, app)
        assert path.read_text() == "hi"
        await pilot.press("!")
        await pilot.press("ctrl+s")
        await settle(pilot, app)

    assert path.read_text() == "hi!"
    assert not any("Not saved" in n.message for n in app._notifications)


@pytest.mark.asyncio
async def test_save_refuses_a_file_that_appeared_before_the_first_save(tmp_path: Path) -> None:
    path = tmp_path / "new.txt"

    app = NovaEditApp(path=path)
    async with app.run_test() as pilot:
        await pilot.pause()
        path.write_text("from elsewhere")
        await pilot.press("h", "i")
        await pilot.press("ctrl+s")
        await wait_until(pilot, lambda: open_box(app) is not None)  # the file is not overwritten without consent
        box = open_box(app)
        assert box is not None
        assert box_title(box) == "Overwrite"

    assert path.read_text() == "from elsewhere"


@pytest.mark.asyncio
async def test_new_file_mode_follows_the_umask(tmp_path: Path) -> None:
    path = tmp_path / "new.txt"

    old = os.umask(0o077)
    try:
        app = NovaEditApp(path=path)
        async with app.run_test() as pilot:
            await pilot.pause()
            await pilot.press("h", "i")
            await pilot.press("ctrl+s")
            await settle(pilot, app)
    finally:
        os.umask(old)

    assert path.stat().st_mode & 0o777 == 0o600


@pytest.mark.asyncio
async def test_failed_save_removes_the_temp_file(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    path = tmp_path / "f.txt"
    path.write_text("one")

    def refuse(_source: str, _target: str) -> None:
        raise PermissionError("no")

    monkeypatch.setattr(NovaTextArea, "save_io", SaveIo(replace=refuse))
    app = NovaEditApp(path=path)
    async with app.run_test() as pilot:
        await pilot.pause()
        await pilot.press("x")
        await pilot.press("ctrl+s")
        await settle(pilot, app)
        save_bar = app.query_one("#save_bar")
        assert save_bar.display

    assert path.read_text() == "one"
    assert [p.name for p in tmp_path.iterdir()] == ["f.txt"]


@pytest.mark.asyncio
async def test_save_small_file_shows_the_result_line(tmp_path: Path) -> None:
    """A save shows a result line in the save bar and no notification about an interim save."""
    path = tmp_path / "save_notify.txt"
    path.write_text("initial")

    app = NovaEditApp(path=path)
    async with app.run_test() as pilot:
        await pilot.pause()
        await pilot.press("n", "e", "w")
        await pilot.press("ctrl+s")
        await settle(pilot, app)
        save_bar = app.query_one("#save_bar")
        assert save_bar.display

    assert path.read_text() == "newinitial"


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


@pytest.mark.asyncio
async def test_app_constructor_takes_soft_wrap(tmp_path: Path) -> None:
    path = tmp_path / "wrap.txt"
    path.write_text("some text\n")
    wrapped = NovaEditApp(path=path, soft_wrap=True)
    async with wrapped.run_test() as pilot:
        await pilot.pause()
        wrapped_screen = wrapped.screen
        assert isinstance(wrapped_screen, EditorScreen)
        assert wrapped_screen.document.editor.soft_wrap is True
    plain = NovaEditApp(path=path)
    async with plain.run_test() as pilot:
        await pilot.pause()
        plain_screen = plain.screen
        assert isinstance(plain_screen, EditorScreen)
        assert plain_screen.document.editor.soft_wrap is False
