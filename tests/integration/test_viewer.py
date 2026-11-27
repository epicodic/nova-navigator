"""F3 shows a file read-only in the editor screen: nothing the user presses writes to the source."""

from __future__ import annotations

import asyncio
import zipfile
from pathlib import Path

import pytest
from textual import events
from textual.worker import WorkerCancelled

from nova_editor.screen import EditorScreen
from nova_navigator.local_copies import LocalCopyManager
from nova_navigator.local_copies.viewer_session import ViewTarget, open_for_viewing
from nova_navigator.nova_navigator import MainScreen
from nova_navigator.terminal import Terminal
from nova_navigator.vfs.filesystems import ArchiveFilesystem, LocalFilesystem
from nova_navigator.vfs.local_copy import LocalCopy
from nova_navigator.vfs.vpath import VPath
from nova_widgets.keymap.key_sequence import KeySequence
from nova_widgets.menu import Menu
from nova_widgets.menu._menu_bar import MenuBarItem
from nova_widgets.message_box import MessageBox
from tests._utils.local_copy_helpers import SchemeFs, ScriptedRunner
from tests.integration.conftest import AppCtx, poll_until, set_panels
from tests.integration.test_editor_integration import back_in_the_list, capture_notices, editor_of, install_manager

TEXT = "needle one\nsecond line\nneedle two\n"
TEXT_KEYS = ("x", "enter", "tab", "backspace", "delete", "ctrl+v", "ctrl+x", "ctrl+z", "ctrl+y", "ctrl+s", "ctrl+shift+s", "ctrl+o")
HEX_KEYS = ("4", "1", "delete", "backspace", "ctrl+v", "ctrl+x", "ctrl+z", "ctrl+y", "ctrl+s")
WRITE_ACTIONS = {"editor.open", "editor.save", "editor.save_as", "editor.undo", "editor.redo", "editor.cut", "editor.paste"}


def snapshot(path: Path) -> tuple[bytes, int]:
    return path.read_bytes(), path.stat().st_mtime_ns


def spy_write_back(monkeypatch: pytest.MonkeyPatch) -> list[str]:
    """Replace every route that writes a local copy back to its source with a recorder."""
    calls: list[str] = []

    def write_back(*_args: object, **_kwargs: object) -> None:
        calls.append("write_back")

    async def sync(*_args: object, **_kwargs: object) -> None:
        calls.append("sync")

    monkeypatch.setattr(LocalCopy, "write_back", write_back)
    monkeypatch.setattr("nova_navigator.local_copies.manager.sync_copy_task", sync)
    return calls


@pytest.fixture
def no_write_back(monkeypatch: pytest.MonkeyPatch) -> list[str]:
    return spy_write_back(monkeypatch)


async def press_f3(ctx: AppCtx, key: str = "f3") -> EditorScreen:
    await ctx.pilot.press(key)
    await poll_until(ctx.pilot, lambda: isinstance(ctx.app.screen, EditorScreen))
    await ctx.pilot.pause()
    return editor_of(ctx)


async def view_with_f3(ctx: AppCtx) -> EditorScreen:
    await set_panels(ctx)
    return await press_f3(ctx)


async def session_ended(ctx: AppCtx) -> None:
    await back_in_the_list(ctx)
    await poll_until(ctx.pilot, lambda: not ctx.app._editor_busy)
    assert not ctx.app._editor_busy


async def close_viewer(ctx: AppCtx) -> None:
    await ctx.pilot.press("escape")
    await session_ended(ctx)


async def press_all(ctx: AppCtx, screen: EditorScreen, keys: tuple[str, ...]) -> None:
    ctx.app.copy_to_clipboard("41 42")
    for key in keys:
        await ctx.pilot.press(key)
        await ctx.pilot.pause()
        assert ctx.app.screen is screen, key
    screen.document.shown.post_message(events.Paste("41 42"))
    await ctx.pilot.pause()


def zip_with_member(ctx: AppCtx) -> tuple[Path, ArchiveFilesystem]:
    zip_path = ctx.src_dir.parent / "x.zip"
    with zipfile.ZipFile(zip_path, "w") as zf:
        zf.writestr("a.txt", "member\n")
    local = LocalFilesystem.singleton()
    return zip_path, ArchiveFilesystem(local.path(zip_path.parent), local.path(zip_path))


async def point_at_member(ctx: AppCtx, archive: ArchiveFilesystem) -> None:
    panel = ctx.screen._left_panel
    panel.set_path(archive.path("/"))
    panel.focus()
    await poll_until(ctx.pilot, lambda: any(item.name == "a.txt" for item in panel.items))
    await ctx.pilot.press("down")
    await ctx.pilot.pause()
    assert panel.path_item_under_cursor.name == "a.txt"


def view_directories(manager_root: Path) -> list[Path]:
    views = manager_root / "view"
    return list(views.iterdir()) if views.exists() else []


@pytest.mark.asyncio
@pytest.mark.integration
async def test_f3_opens_the_file_view_only(app_ctx: AppCtx) -> None:
    (app_ctx.src_dir / "a.txt").write_text(TEXT)

    screen = await view_with_f3(app_ctx)

    assert screen.view_only
    assert screen.document.editor.read_only
    assert screen.document.editor.text == TEXT
    assert screen.document.file_path == app_ctx.src_dir / "a.txt"


@pytest.mark.asyncio
@pytest.mark.integration
async def test_f3_binary_file_opens_in_hex_view(app_ctx: AppCtx) -> None:
    (app_ctx.src_dir / "a.bin").write_bytes(b"\x00\x01\x02\x00binary")

    screen = await view_with_f3(app_ctx)

    assert screen.view_only
    assert screen.document.hex_mode


@pytest.mark.asyncio
@pytest.mark.integration
async def test_viewer_text_keys_and_menu_never_change_or_save(app_ctx: AppCtx, no_write_back: list[str]) -> None:
    file = app_ctx.src_dir / "a.txt"
    file.write_text(TEXT)
    before = snapshot(file)
    screen = await view_with_f3(app_ctx)

    await press_all(app_ctx, screen, TEXT_KEYS)

    menus = [menu for menu in screen.menu_bar.actions if isinstance(menu, Menu) and menu.text in {"File", "Edit"}]
    assert len(menus) == 2
    assert not WRITE_ACTIONS & {action.id for menu in menus for action in menu.actions}
    assert screen.document.editor.text == TEXT
    assert not screen.document.editor.modified
    await close_viewer(app_ctx)
    assert snapshot(file) == before
    assert not app_ctx.app.local_copies.entries
    assert no_write_back == []


@pytest.mark.asyncio
@pytest.mark.integration
async def test_viewer_hex_keys_never_change_or_save(app_ctx: AppCtx, no_write_back: list[str]) -> None:
    file = app_ctx.src_dir / "a.txt"
    file.write_text(TEXT)
    before = snapshot(file)
    screen = await view_with_f3(app_ctx)
    await app_ctx.pilot.press("f4")
    await app_ctx.pilot.pause()
    assert screen.document.hex_mode

    await press_all(app_ctx, screen, HEX_KEYS)

    assert screen.document.editor.document.length == len(TEXT)
    assert not screen.document.editor.modified
    await app_ctx.pilot.press("f4")
    await app_ctx.pilot.pause()
    assert screen.document.editor.text == TEXT
    await close_viewer(app_ctx)
    assert snapshot(file) == before
    assert not app_ctx.app.local_copies.entries
    assert no_write_back == []


@pytest.mark.asyncio
@pytest.mark.integration
async def test_f3_local_file_is_never_written(app_ctx: AppCtx, no_write_back: list[str]) -> None:
    file = app_ctx.src_dir / "a.txt"
    file.write_text(TEXT)
    before = snapshot(file)
    screen = await view_with_f3(app_ctx)

    await press_all(app_ctx, screen, ("x", "delete", "ctrl+s"))
    await close_viewer(app_ctx)

    assert snapshot(file) == before
    assert not app_ctx.app.local_copies.entries
    assert no_write_back == []


@pytest.mark.asyncio
@pytest.mark.integration
async def test_f3_zip_member_is_never_written_back(app_ctx: AppCtx, tmp_path: Path, no_write_back: list[str], monkeypatch: pytest.MonkeyPatch) -> None:
    notices = capture_notices(app_ctx, monkeypatch)
    install_manager(app_ctx, tmp_path, ScriptedRunner())
    zip_path, archive = zip_with_member(app_ctx)
    before = snapshot(zip_path)
    await point_at_member(app_ctx, archive)
    screen = await press_f3(app_ctx)
    assert screen.document.editor.text == "member\n"

    await press_all(app_ctx, screen, ("x", "delete", "ctrl+s"))
    await close_viewer(app_ctx)

    assert snapshot(zip_path) == before
    assert not app_ctx.app.local_copies.entries
    assert no_write_back == []
    assert any("read-only" in notice for notice in notices)


@pytest.mark.asyncio
@pytest.mark.integration
async def test_view_copy_is_removed_on_close_and_not_listed(app_ctx: AppCtx, tmp_path: Path, no_write_back: list[str], monkeypatch: pytest.MonkeyPatch) -> None:
    manager = install_manager(app_ctx, tmp_path, ScriptedRunner())
    zip_path, archive = zip_with_member(app_ctx)
    before = snapshot(zip_path)
    await point_at_member(app_ctx, archive)
    reloads: list[None] = []
    monkeypatch.setattr(app_ctx.screen, "reload_panels", lambda: reloads.append(None))

    screen = await press_f3(app_ctx)

    path = screen.document.file_path
    assert path is not None
    assert tmp_path / "copies" / "view" in path.parents
    assert path.read_bytes() == b"member\n"
    assert not manager.entries
    await close_viewer(app_ctx)
    assert not path.exists()
    assert view_directories(tmp_path / "copies") == []
    assert reloads == []
    assert snapshot(zip_path) == before
    assert not manager.entries
    assert no_write_back == []


@pytest.mark.asyncio
@pytest.mark.integration
async def test_f3_on_directory_and_updir_opens_nothing(app_ctx: AppCtx) -> None:
    (app_ctx.src_dir / "sub").mkdir()
    (app_ctx.src_dir / "tolink").symlink_to(app_ctx.src_dir / "sub")
    await set_panels(app_ctx)
    panel = app_ctx.screen.active_panel()
    await app_ctx.pilot.press("up")

    seen: list[str] = []
    for _ in range(3):
        await app_ctx.pilot.pause()
        seen.append(panel.path_item_under_cursor.name)
        await app_ctx.pilot.press("f3")
        await app_ctx.pilot.pause(delay=0.2)
        assert app_ctx.app.screen is app_ctx.screen
        assert not app_ctx.app._editor_busy
        await app_ctx.pilot.press("down")

    assert seen == ["..", "sub", "tolink"]


@pytest.mark.asyncio
@pytest.mark.integration
async def test_f3_is_in_the_hint_bar_and_file_menu(app_ctx: AppCtx) -> None:
    (app_ctx.src_dir / "a.txt").write_text(TEXT)
    (app_ctx.src_dir / "sub").mkdir()
    screen = app_ctx.screen
    action = screen._act("browser.view_file")

    assert action.show_in_bar
    assert action.shortcut_label == "F3"
    file_menu = next(menu for menu in screen._menu_bar.actions if isinstance(menu, Menu) and menu.text == "File")
    ids = [item.id for item in file_menu.actions]
    assert ids.index("browser.view_file") == ids.index("browser.open_editor") - 1
    screen._update_actions(app_ctx.fs.path(app_ctx.src_dir / "a.txt"))
    assert action.enabled
    screen._update_actions(app_ctx.fs.path(app_ctx.src_dir / "sub"))
    assert not action.enabled


@pytest.mark.asyncio
@pytest.mark.integration
async def test_view_from_the_file_menu_opens_the_viewer(app_ctx: AppCtx) -> None:
    (app_ctx.src_dir / "a.txt").write_text(TEXT)
    await set_panels(app_ctx)
    menu_bar = app_ctx.screen._menu_bar
    await app_ctx.pilot.click(list(menu_bar.query(MenuBarItem))[1])
    await app_ctx.pilot.pause()
    menu = app_ctx.screen.query_one(Menu)
    row = next(i for i, action in enumerate(menu.actions) if action.id == "browser.view_file") + 1

    await app_ctx.pilot.hover(menu, offset=(2, row))
    await app_ctx.pilot.click(menu, offset=(2, row))
    await poll_until(app_ctx.pilot, lambda: isinstance(app_ctx.app.screen, EditorScreen))

    assert editor_of(app_ctx).view_only
    assert editor_of(app_ctx).document.editor.text == TEXT


@pytest.mark.asyncio
@pytest.mark.integration
async def test_f3_can_be_rebound(app_ctx: AppCtx) -> None:
    (app_ctx.src_dir / "a.txt").write_text(TEXT)
    app_ctx.screen.keymap_config.save({"browser.view_file": KeySequence.parse("f9")})
    app_ctx.screen._reload_keymap()
    await set_panels(app_ctx)

    await app_ctx.pilot.press("f3")
    await app_ctx.pilot.pause(delay=0.2)
    assert app_ctx.app.screen is app_ctx.screen

    screen = await press_f3(app_ctx, "f9")
    assert screen.view_only


@pytest.mark.asyncio
@pytest.mark.integration
async def test_f3_with_terminal_focused_goes_to_the_shell(app_ctx: AppCtx, monkeypatch: pytest.MonkeyPatch) -> None:
    (app_ctx.src_dir / "a.txt").write_text(TEXT)
    await set_panels(app_ctx)
    terminal = app_ctx.screen.query(Terminal).first()
    terminal.focus()
    await poll_until(app_ctx.pilot, lambda: terminal.send_queue is not None)
    sent: list[object] = []
    assert terminal.send_queue is not None
    monkeypatch.setattr(terminal.send_queue, "put_nowait", sent.append)

    await app_ctx.pilot.press("f3")
    await app_ctx.pilot.pause(delay=0.2)

    assert ["stdin", "\x1bOR"] in sent
    assert app_ctx.app.screen is app_ctx.screen


@pytest.mark.asyncio
@pytest.mark.integration
async def test_viewer_closes_on_escape_f10_and_quit_key(app_ctx: AppCtx) -> None:
    (app_ctx.src_dir / "a.txt").write_text(TEXT)
    await set_panels(app_ctx)

    for key in ("escape", "f10", "ctrl+q", "ctrl+w"):
        await press_f3(app_ctx)
        await app_ctx.pilot.press(key)
        await session_ended(app_ctx)
        assert app_ctx.app.is_running, key

    app_ctx.screen.keymap_config.save({"editor.quit": KeySequence.parse("f9")})
    await press_f3(app_ctx)
    await app_ctx.pilot.press("f9")
    await session_ended(app_ctx)
    assert app_ctx.app.is_running


@pytest.mark.asyncio
@pytest.mark.integration
async def test_escape_closes_the_popup_before_the_viewer(app_ctx: AppCtx) -> None:
    (app_ctx.src_dir / "a.txt").write_text(TEXT)
    await set_panels(app_ctx)

    for open_key, popup_name in (("ctrl+f", "find_popup"), ("ctrl+g", "goto_popup")):
        screen = await press_f3(app_ctx)
        await app_ctx.pilot.press(open_key)
        await app_ctx.pilot.pause()
        popup = getattr(screen, popup_name)
        assert popup.display
        await app_ctx.pilot.press("escape")
        await app_ctx.pilot.pause()
        assert not popup.display
        assert app_ctx.app.screen is screen
        await close_viewer(app_ctx)

    screen = await press_f3(app_ctx)
    await app_ctx.pilot.click(screen.menu_bar.query(MenuBarItem).first())
    await app_ctx.pilot.pause()
    assert len(screen.query(Menu)) == 1
    await app_ctx.pilot.press("escape")
    await app_ctx.pilot.pause()
    assert not screen.query(Menu)
    assert app_ctx.app.screen is screen
    await close_viewer(app_ctx)


@pytest.mark.asyncio
@pytest.mark.integration
async def test_f3_and_shift_f3_search_inside_the_viewer(app_ctx: AppCtx) -> None:
    (app_ctx.src_dir / "a.txt").write_text(TEXT)
    screen = await view_with_f3(app_ctx)
    editor = screen.document.editor

    await app_ctx.pilot.press("ctrl+f", *"needle", "enter")
    await poll_until(app_ctx.pilot, lambda: editor.selected_text == "needle")
    await app_ctx.pilot.press("escape")
    await app_ctx.pilot.pause()
    assert editor.cursor_location[0] == 0

    await app_ctx.pilot.press("f3")
    await poll_until(app_ctx.pilot, lambda: editor.cursor_location[0] == 2)
    assert editor.cursor_location[0] == 2
    await app_ctx.pilot.press("shift+f3")
    await poll_until(app_ctx.pilot, lambda: editor.cursor_location[0] == 0)
    assert editor.cursor_location[0] == 0
    assert app_ctx.app.screen is screen


@pytest.mark.asyncio
@pytest.mark.integration
async def test_failed_view_download_leaves_nothing_open(app_ctx: AppCtx, tmp_path: Path, no_write_back: list[str]) -> None:
    manager = install_manager(app_ctx, tmp_path, ScriptedRunner())
    fs = SchemeFs({"/d/f.txt": b"remote"}, read_errors={"/d/f.txt": OSError("connection reset")})
    good = app_ctx.src_dir / "a.txt"
    good.write_text(TEXT)

    await app_ctx.app.open_viewer(fs.path("/d/f.txt"))
    await app_ctx.pilot.pause()

    assert app_ctx.app.screen is app_ctx.screen
    assert view_directories(tmp_path / "copies") == []
    assert not manager.entries
    assert no_write_back == []
    screen = await view_with_f3(app_ctx)
    assert screen.document.editor.text == TEXT


@pytest.mark.asyncio
@pytest.mark.integration
async def test_failed_viewer_start_removes_the_view_copy(app_ctx: AppCtx, tmp_path: Path, no_write_back: list[str], monkeypatch: pytest.MonkeyPatch) -> None:
    manager = install_manager(app_ctx, tmp_path, ScriptedRunner())
    fs = SchemeFs({"/d/f.txt": b"remote"})

    def broken_screen(**_options: object) -> EditorScreen:
        raise RuntimeError("no screen")

    monkeypatch.setattr("nova_navigator.nova_navigator.EditorScreen", broken_screen)

    with pytest.raises(RuntimeError, match="no screen"):
        await app_ctx.app.open_viewer(fs.path("/d/f.txt"))

    assert view_directories(tmp_path / "copies") == []
    assert not app_ctx.app._editor_busy
    assert app_ctx.app._editor_session is None
    assert not manager.entries
    assert no_write_back == []
    assert isinstance(app_ctx.app.screen, MainScreen)


@pytest.mark.asyncio
@pytest.mark.integration
async def test_viewer_start_cancelled_after_the_download_removes_the_view_copy(app_ctx: AppCtx, tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    manager = install_manager(app_ctx, tmp_path, ScriptedRunner())
    fs = SchemeFs({"/d/f.txt": b"remote"})
    produced: list[ViewTarget] = []
    opening: list[asyncio.Task[None]] = []

    async def cancel_when_ready(copies: LocalCopyManager, source: VPath) -> ViewTarget | None:
        target = await open_for_viewing(copies, source)
        assert target is not None
        assert target.path.exists()
        produced.append(target)
        # runs after this worker has finished and before `open_viewer` resumes
        asyncio.get_running_loop().call_soon(opening[0].cancel)
        return target

    monkeypatch.setattr("nova_navigator.nova_navigator.open_for_viewing", cancel_when_ready)
    opening.append(asyncio.create_task(app_ctx.app.open_viewer(fs.path("/d/f.txt"))))

    with pytest.raises((asyncio.CancelledError, WorkerCancelled)):
        await opening[0]

    assert len(produced) == 1
    assert not produced[0].path.exists()
    assert view_directories(tmp_path / "copies") == []
    assert not app_ctx.app._editor_busy
    assert app_ctx.app._editor_session is None
    assert not manager.entries
    assert isinstance(app_ctx.app.screen, MainScreen)


@pytest.mark.asyncio
@pytest.mark.integration
@pytest.mark.parametrize("case", ["broken_symlink", "unreadable", "vanished"])
async def test_f3_on_a_file_that_cannot_be_read_shows_the_error_dialog_and_opens_nothing(app_ctx: AppCtx, case: str) -> None:
    file = app_ctx.src_dir / "a.txt"
    if case == "broken_symlink":
        file.symlink_to(app_ctx.src_dir / "nowhere")
    else:
        file.write_text(TEXT)
    if case == "unreadable":
        file.chmod(0)
    await set_panels(app_ctx)
    if case == "vanished":
        file.unlink()

    await app_ctx.pilot.press("f3")
    await poll_until(app_ctx.pilot, lambda: isinstance(app_ctx.app.screen, MessageBox))

    assert isinstance(app_ctx.app.screen, MessageBox)
    assert not any(isinstance(s, EditorScreen) for s in app_ctx.app.screen_stack)
    assert not app_ctx.app._editor_busy
    await app_ctx.pilot.press("escape")
    await back_in_the_list(app_ctx)
    assert app_ctx.app.is_running
