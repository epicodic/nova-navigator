"""F4 opens the built-in editor screen over the file list: local files directly, other files through a local copy (REQ-20, REQ-21)."""

from __future__ import annotations

import asyncio
import zipfile
from pathlib import Path

import pytest

from nova_editor.core.byte_source import ChangeKind
from nova_editor.screen import EditorScreen
from nova_navigator.dialogs.response_dialog import ResponseDialog
from nova_navigator.local_copies import CopyEntry, CopyStatus, LocalCopyManager
from nova_navigator.nova_navigator import MainScreen
from nova_navigator.vfs.filesystems import ArchiveFilesystem, LocalFilesystem
from nova_navigator.vfs.vpath import VPath
from nova_widgets import DataTable, MessageBox, Response
from tests._utils.local_copy_helpers import RefusingFs, SchemeFs, ScriptedRunner, overwrite
from tests.integration.conftest import AppCtx, poll_until, set_panels


def editor_of(ctx: AppCtx) -> EditorScreen:
    screen = ctx.app.screen
    assert isinstance(screen, EditorScreen)
    return screen


async def open_with_f4(ctx: AppCtx) -> EditorScreen:
    await set_panels(ctx)
    await ctx.pilot.press("f4")
    await poll_until(ctx.pilot, lambda: isinstance(ctx.app.screen, EditorScreen))
    return editor_of(ctx)


async def back_in_the_list(ctx: AppCtx) -> None:
    await poll_until(ctx.pilot, lambda: isinstance(ctx.app.screen, MainScreen))
    assert isinstance(ctx.app.screen, MainScreen)


@pytest.mark.asyncio
@pytest.mark.integration
async def test_f4_opens_a_local_file_directly(app_ctx: AppCtx) -> None:
    (app_ctx.src_dir / "a.txt").write_text("hello\n")

    screen = await open_with_f4(app_ctx)

    assert screen.document.file_path == app_ctx.src_dir / "a.txt"
    assert app_ctx.app.local_copies.entries == []
    assert [m.text for m in screen.menu_bar.actions] == ["File", "Edit", "Search", "View"]
    assert screen.document.editor.text == "hello\n"


@pytest.mark.asyncio
@pytest.mark.integration
async def test_typing_and_ctrl_s_write_the_local_file(app_ctx: AppCtx) -> None:
    (app_ctx.src_dir / "a.txt").write_text("hello\n")
    screen = await open_with_f4(app_ctx)

    await app_ctx.pilot.press("x", "ctrl+s")
    await poll_until(app_ctx.pilot, lambda: (app_ctx.src_dir / "a.txt").read_text() == "xhello\n")

    assert not screen.document.editor.modified


@pytest.mark.asyncio
@pytest.mark.integration
async def test_close_returns_to_the_list_and_reloads_the_panels(app_ctx: AppCtx) -> None:
    file = app_ctx.src_dir / "a.txt"
    file.write_text("hello\n")
    await open_with_f4(app_ctx)
    file.write_text("hello, much longer now\n")
    other = app_ctx.src_dir / "b.txt"
    other.write_text("b")

    await app_ctx.pilot.press("ctrl+w")
    await back_in_the_list(app_ctx)

    names = [item.name for item in app_ctx.screen.active_panel().items]
    assert "b.txt" in names
    assert app_ctx.app.sub_title == ""


@pytest.mark.asyncio
@pytest.mark.integration
async def test_closing_with_edits_asks_and_cancel_keeps_the_editor(app_ctx: AppCtx) -> None:
    (app_ctx.src_dir / "a.txt").write_text("hello\n")
    screen = await open_with_f4(app_ctx)

    await app_ctx.pilot.press("x", "ctrl+w")
    await poll_until(app_ctx.pilot, lambda: isinstance(app_ctx.app.screen, MessageBox))
    await app_ctx.pilot.press("escape")
    await poll_until(app_ctx.pilot, lambda: app_ctx.app.screen is screen)

    assert screen.document.editor.modified
    await app_ctx.pilot.press("ctrl+w")
    await poll_until(app_ctx.pilot, lambda: isinstance(app_ctx.app.screen, MessageBox))
    await app_ctx.pilot.press("right", "enter")
    await back_in_the_list(app_ctx)
    assert (app_ctx.src_dir / "a.txt").read_text() == "hello\n"


@pytest.mark.asyncio
@pytest.mark.integration
async def test_f4_on_a_file_that_vanished_shows_the_error_dialog_and_opens_no_editor(app_ctx: AppCtx) -> None:
    file = app_ctx.src_dir / "a.txt"
    file.write_text("hello\n")
    await set_panels(app_ctx)
    file.unlink()

    await app_ctx.pilot.press("f4")
    await poll_until(app_ctx.pilot, lambda: isinstance(app_ctx.app.screen, MessageBox))

    assert not any(isinstance(s, EditorScreen) for s in app_ctx.app.screen_stack)
    await app_ctx.pilot.press("escape")
    await back_in_the_list(app_ctx)


@pytest.mark.asyncio
@pytest.mark.integration
async def test_enter_on_a_text_file_still_runs_the_filetype_command(app_ctx: AppCtx, monkeypatch: pytest.MonkeyPatch) -> None:
    (app_ctx.src_dir / "a.txt").write_text("hello\n")
    commands: list[list[str]] = []

    async def record(args: list[str], _cwd: object) -> None:
        commands.append(args)

    monkeypatch.setattr(app_ctx.app, "execute_command", record)
    await set_panels(app_ctx)

    await app_ctx.pilot.press("enter")
    await poll_until(app_ctx.pilot, lambda: bool(commands))

    assert any("a.txt" in part for part in commands[0])
    assert isinstance(app_ctx.app.screen, MainScreen)


@pytest.mark.asyncio
@pytest.mark.integration
async def test_edit_user_menu_file_opens_the_editor_screen_on_the_config_file(app_ctx: AppCtx) -> None:
    await app_ctx.app.run_action("edit_user_menu", app_ctx.screen)
    await poll_until(app_ctx.pilot, lambda: isinstance(app_ctx.app.screen, EditorScreen))

    screen = editor_of(app_ctx)

    assert screen.document.file_path == app_ctx.src_dir.parent / "config" / "usermenu.toml"
    assert app_ctx.app.local_copies.entries == []


# ---------------------------------------------------------------------------
# Non-local files: the editor edits a local copy and a save is written back (REQ-21)
# ---------------------------------------------------------------------------


def install_manager(ctx: AppCtx, tmp_path: Path, runner: ScriptedRunner) -> LocalCopyManager:
    """Give the app a real copy manager rooted in `tmp_path` whose jobs run without dialogs."""
    manager = LocalCopyManager(tmp_path / "copies", runner, poll_interval=0.05, settle_time=0.05)
    ctx.app.local_copies = manager
    return manager


async def open_in_editor(ctx: AppCtx, source: VPath) -> EditorScreen:
    await ctx.app.open_editor(source)
    await poll_until(ctx.pilot, lambda: isinstance(ctx.app.screen, EditorScreen))
    return editor_of(ctx)


def capture_notices(ctx: AppCtx, monkeypatch: pytest.MonkeyPatch) -> list[str]:
    """Record the toasts of the app instead of showing them."""
    notices: list[str] = []

    def record(message: str, **_options: object) -> None:
        notices.append(message)

    monkeypatch.setattr(ctx.app, "notify", record)
    return notices


def only_entry(manager: LocalCopyManager) -> CopyEntry:
    assert len(manager.entries) == 1
    return manager.entries[0]


def content(fs: SchemeFs, path: str) -> bytes:
    return fs.read(fs.path(path)).read(1000)


@pytest.mark.asyncio
@pytest.mark.integration
async def test_an_ssh_file_is_edited_through_its_copy_and_written_back_once(app_ctx: AppCtx, tmp_path: Path) -> None:
    fs = SchemeFs({"/d/f.txt": b"remote\n"})
    runner = ScriptedRunner()
    manager = install_manager(app_ctx, tmp_path, runner)

    screen = await open_in_editor(app_ctx, fs.path("/d/f.txt"))
    entry = only_entry(manager)
    assert screen.document.file_path == entry.copy.path
    assert screen.document.editor.text == "remote\n"

    await app_ctx.pilot.press("x", "ctrl+s")
    await poll_until(app_ctx.pilot, lambda: content(fs, "/d/f.txt") == b"xremote\n")
    await poll_until(app_ctx.pilot, lambda: entry.status is CopyStatus.SYNCED)
    assert screen.document.editor.check_external_change() is ChangeKind.UNCHANGED
    assert isinstance(app_ctx.app.screen, EditorScreen)

    await app_ctx.pilot.press("ctrl+w")
    await back_in_the_list(app_ctx)
    await poll_until(app_ctx.pilot, lambda: entry.detector is None)

    assert entry.status is CopyStatus.SYNCED
    assert len(runner.sync_jobs) == 1
    assert manager.unsynced() == []
    assert content(fs, "/d/f.txt") == b"xremote\n"


@pytest.mark.asyncio
@pytest.mark.integration
async def test_closing_right_after_a_save_loses_nothing_and_syncs_once(app_ctx: AppCtx, tmp_path: Path) -> None:
    fs = SchemeFs({"/d/f.txt": b"remote\n"})
    runner = ScriptedRunner()
    manager = install_manager(app_ctx, tmp_path, runner)
    await open_in_editor(app_ctx, fs.path("/d/f.txt"))

    await app_ctx.pilot.press("x", "ctrl+s", "ctrl+w")
    await back_in_the_list(app_ctx)
    await poll_until(app_ctx.pilot, lambda: content(fs, "/d/f.txt") == b"xremote\n")
    await manager.wait_idle(max_wait=3)

    assert len(runner.sync_jobs) == 1
    assert only_entry(manager).status is CopyStatus.SYNCED


@pytest.mark.asyncio
@pytest.mark.integration
async def test_the_local_copies_dialog_shows_the_closed_copy(app_ctx: AppCtx, tmp_path: Path) -> None:
    fs = SchemeFs({"/d/f.txt": b"remote\n"})
    manager = install_manager(app_ctx, tmp_path, ScriptedRunner())
    await open_in_editor(app_ctx, fs.path("/d/f.txt"))
    await app_ctx.pilot.press("ctrl+w")
    await back_in_the_list(app_ctx)
    entry = only_entry(manager)
    await poll_until(app_ctx.pilot, lambda: entry.detector is None)

    await app_ctx.pilot.press("ctrl+e")
    await poll_until(app_ctx.pilot, lambda: not isinstance(app_ctx.app.screen, MainScreen))
    table = app_ctx.app.screen.query(DataTable).first()

    assert table.get_row_at(0)[2] == "synced (closed)"
    await app_ctx.pilot.press("escape")


@pytest.mark.asyncio
@pytest.mark.integration
async def test_a_member_of_a_writable_zip_is_written_back_into_the_zip(app_ctx: AppCtx, tmp_path: Path) -> None:
    zip_path = tmp_path / "x.zip"
    with zipfile.ZipFile(zip_path, "w") as zf:
        zf.writestr("a.txt", "member\n")
    local = LocalFilesystem.singleton()
    member = ArchiveFilesystem(local.path(tmp_path), local.path(zip_path)).path("/a.txt")
    manager = install_manager(app_ctx, tmp_path, ScriptedRunner())

    await open_in_editor(app_ctx, member)
    await app_ctx.pilot.press("x", "ctrl+s", "ctrl+w")
    await back_in_the_list(app_ctx)
    await manager.wait_idle(max_wait=5)

    with zipfile.ZipFile(zip_path) as zf:
        assert zf.read("a.txt") == b"xmember\n"
    assert only_entry(manager).status is CopyStatus.SYNCED


@pytest.mark.asyncio
@pytest.mark.integration
async def test_a_jar_member_opens_view_only_and_nothing_is_written(app_ctx: AppCtx, tmp_path: Path) -> None:
    jar = tmp_path / "x.jar"
    with zipfile.ZipFile(jar, "w") as zf:
        zf.writestr("a.txt", "member\n")
    before = jar.read_bytes()
    local = LocalFilesystem.singleton()
    member = ArchiveFilesystem(local.path(tmp_path), local.path(jar)).path("/a.txt")
    runner = ScriptedRunner()
    manager = install_manager(app_ctx, tmp_path, runner)

    screen = await open_in_editor(app_ctx, member)
    assert screen.document.editor.read_only
    await app_ctx.pilot.press("x", "ctrl+s")
    await app_ctx.pilot.pause()

    assert screen.document.editor.text == "member\n"
    assert not screen.document.editor.modified
    await app_ctx.pilot.press("ctrl+w")
    await back_in_the_list(app_ctx)
    assert only_entry(manager).status is CopyStatus.READ_ONLY
    assert runner.sync_jobs == []
    assert jar.read_bytes() == before


@pytest.mark.asyncio
@pytest.mark.integration
async def test_a_conflict_answered_with_skip_leaves_the_source_and_is_reported_at_close(app_ctx: AppCtx, tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    notices = capture_notices(app_ctx, monkeypatch)
    fs = SchemeFs({"/d/f.txt": b"remote\n"})
    runner = ScriptedRunner([Response.SKIP])
    manager = install_manager(app_ctx, tmp_path, runner)
    await open_in_editor(app_ctx, fs.path("/d/f.txt"))
    entry = only_entry(manager)
    overwrite(fs, "/d/f.txt", b"server\n")

    await app_ctx.pilot.press("x", "ctrl+s")
    await poll_until(app_ctx.pilot, lambda: entry.status is CopyStatus.CONFLICT)
    assert isinstance(app_ctx.app.screen, EditorScreen)
    await app_ctx.pilot.press("ctrl+w")
    await back_in_the_list(app_ctx)

    await poll_until(app_ctx.pilot, lambda: any("changed on the source" in n for n in notices))
    assert content(fs, "/d/f.txt") == b"server\n"
    assert entry.status is CopyStatus.CONFLICT
    assert manager.unsynced() == [entry]


@pytest.mark.asyncio
@pytest.mark.integration
async def test_a_failed_write_back_is_reported_at_close_and_the_copy_keeps_the_text(app_ctx: AppCtx, tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    notices = capture_notices(app_ctx, monkeypatch)
    fs = RefusingFs({"/d/f.txt": b"remote\n"})
    manager = install_manager(app_ctx, tmp_path, ScriptedRunner())
    await open_in_editor(app_ctx, fs.path("/d/f.txt"))
    entry = only_entry(manager)

    await app_ctx.pilot.press("x", "ctrl+s", "ctrl+w")
    await back_in_the_list(app_ctx)
    await poll_until(app_ctx.pilot, lambda: entry.status is CopyStatus.FAILED)

    await poll_until(app_ctx.pilot, lambda: any("Could not write f.txt back" in n for n in notices))
    assert entry.error is not None
    assert entry.copy.path.read_bytes() == b"xremote\n"
    assert app_ctx.app.is_running


@pytest.mark.asyncio
@pytest.mark.integration
async def test_opening_the_same_file_again_reuses_the_copy_and_shows_the_saved_text(app_ctx: AppCtx, tmp_path: Path) -> None:
    fs = SchemeFs({"/d/f.txt": b"remote\n"})
    manager = install_manager(app_ctx, tmp_path, ScriptedRunner())
    await open_in_editor(app_ctx, fs.path("/d/f.txt"))
    await app_ctx.pilot.press("x", "ctrl+s", "ctrl+w")
    await back_in_the_list(app_ctx)
    await manager.wait_idle(max_wait=3)

    screen = await open_in_editor(app_ctx, fs.path("/d/f.txt"))

    assert len(manager.entries) == 1
    assert screen.document.editor.text == "xremote\n"


@pytest.mark.asyncio
@pytest.mark.integration
async def test_a_sync_question_over_the_editor_is_answered_before_the_editor_is_popped(app_ctx: AppCtx, tmp_path: Path) -> None:
    fs = SchemeFs({"/d/f.txt": b"remote\n"})
    manager = LocalCopyManager(tmp_path / "copies", app_ctx.app.start_job, poll_interval=0.05, settle_time=0.05)
    app_ctx.app.local_copies = manager
    screen = await open_in_editor(app_ctx, fs.path("/d/f.txt"))
    entry = only_entry(manager)
    overwrite(fs, "/d/f.txt", b"server\n")

    await app_ctx.pilot.press("x", "ctrl+s")
    await poll_until(app_ctx.pilot, lambda: isinstance(app_ctx.app.screen, ResponseDialog))
    await screen.action_close_editor()
    await app_ctx.pilot.pause(delay=0.3)
    assert isinstance(app_ctx.app.screen, ResponseDialog)
    assert screen in app_ctx.app.screen_stack

    await app_ctx.pilot.click("#SKIP")
    await back_in_the_list(app_ctx)

    assert screen not in app_ctx.app.screen_stack
    assert entry.status is CopyStatus.CONFLICT
    assert content(fs, "/d/f.txt") == b"server\n"


@pytest.mark.asyncio
@pytest.mark.integration
async def test_opening_a_copy_says_where_saves_go_and_a_local_file_says_nothing(app_ctx: AppCtx, tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    notices = capture_notices(app_ctx, monkeypatch)
    fs = SchemeFs({"/d/f.txt": b"remote\n"})
    install_manager(app_ctx, tmp_path, ScriptedRunner())
    await open_in_editor(app_ctx, fs.path("/d/f.txt"))
    assert notices == ["Local copy of ssh://user@host:2222/d/f.txt. Saves are written back to the source."]
    await app_ctx.pilot.press("ctrl+w")
    await back_in_the_list(app_ctx)

    notices.clear()
    local = tmp_path / "local.txt"
    local.write_text("x")
    await open_in_editor(app_ctx, VPath(local, LocalFilesystem.singleton()))
    assert notices == []


@pytest.mark.asyncio
@pytest.mark.integration
async def test_opening_a_read_only_copy_says_it_is_never_written_back(app_ctx: AppCtx, tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    notices = capture_notices(app_ctx, monkeypatch)
    jar = tmp_path / "x.jar"
    with zipfile.ZipFile(jar, "w") as zf:
        zf.writestr("a.txt", "member\n")
    local = LocalFilesystem.singleton()
    install_manager(app_ctx, tmp_path, ScriptedRunner())

    await open_in_editor(app_ctx, ArchiveFilesystem(local.path(tmp_path), local.path(jar)).path("/a.txt"))

    assert notices == ["a.txt is read-only: it is never written back. Save As writes a local file."]


@pytest.mark.asyncio
@pytest.mark.integration
async def test_a_second_request_while_the_editor_is_opening_pushes_no_second_editor(app_ctx: AppCtx) -> None:
    file = app_ctx.src_dir / "a.txt"
    file.write_text("hello\n")
    source = VPath(file, LocalFilesystem.singleton())

    await asyncio.gather(app_ctx.app.open_editor(source), app_ctx.app.open_editor(source))
    await poll_until(app_ctx.pilot, lambda: isinstance(app_ctx.app.screen, EditorScreen))
    await app_ctx.pilot.pause()

    assert sum(isinstance(s, EditorScreen) for s in app_ctx.app.screen_stack) == 1


# ---------------------------------------------------------------------------
# Error recovery: copy released when open_editor fails
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
@pytest.mark.integration
async def test_copy_is_released_when_push_screen_fails(app_ctx: AppCtx, tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    """If push_screen raises after open_for_editing succeeds, the copy is still released."""
    fs = SchemeFs({"/d/f.txt": b"remote\n"})
    runner = ScriptedRunner()
    manager = install_manager(app_ctx, tmp_path, runner)

    # Mock push_screen to raise an exception after the copy was opened
    def failing_push_screen(_screen: object) -> None:
        raise RuntimeError("push_screen failed")

    monkeypatch.setattr(app_ctx.app, "push_screen", failing_push_screen)

    # Try to open the editor; it should fail but the copy should be released
    with pytest.raises(RuntimeError, match="push_screen failed"):
        await app_ctx.app.open_editor(fs.path("/d/f.txt"))

    # Verify that the copy entry was opened and released (detector should be None after release)
    await manager.wait_idle(max_wait=3)
    entry = only_entry(manager)
    assert entry.detector is None, "Copy should be released even though push_screen failed"
    assert entry.status is CopyStatus.SYNCED
    assert not any(isinstance(s, EditorScreen) for s in app_ctx.app.screen_stack), "No EditorScreen should be in the stack"


@pytest.mark.asyncio
@pytest.mark.integration
async def test_copy_is_released_when_closing_editor_even_if_error_after_pop(app_ctx: AppCtx, tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    """finish_editing is called even if an error occurs after pop_screen during _end_editor_session."""
    fs = SchemeFs({"/d/f.txt": b"remote\n"})
    runner = ScriptedRunner()
    manager = install_manager(app_ctx, tmp_path, runner)

    await open_in_editor(app_ctx, fs.path("/d/f.txt"))
    entry = only_entry(manager)

    # Track whether reload_panels was called by making it fail
    reload_call_count = 0

    def failing_reload() -> None:
        nonlocal reload_call_count
        reload_call_count += 1
        raise RuntimeError("reload_panels failed")

    monkeypatch.setattr(app_ctx.screen, "reload_panels", failing_reload)

    # Close the editor
    await app_ctx.pilot.press("ctrl+w")
    # Wait for reload_panels to raise and for finish_editing to run
    await poll_until(app_ctx.pilot, lambda: entry.detector is None, max_wait=3)

    # Verify that finish_editing was called (detector should be None, meaning the copy was released)
    assert reload_call_count > 0, "reload_panels should have been called"
    assert entry.detector is None, "Copy should be released even though reload_panels raised"
    assert entry.status is CopyStatus.SYNCED
