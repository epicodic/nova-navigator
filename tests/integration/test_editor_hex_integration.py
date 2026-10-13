"""The hex view in the navigator-hosted editor and on non-local files (REQ-1 navigator host, REQ-24).

These are verification tests: the behaviour comes from the hosts, the screen and the local-copy mechanism.
The non-local tests use the `SchemeFs` mock (`ssh://user@host:2222` URIs) and a scripted job runner; no real host is contacted.
"""

from __future__ import annotations

import os
import zipfile
from pathlib import Path

import pytest
from rich.text import Text

from nova_editor.core.byte_source import ChangeKind
from nova_editor.screen import EditorScreen
from nova_editor.status_line import StatusLine
from nova_navigator.local_copies import CopyStatus
from nova_navigator.vfs.filesystems import ArchiveFilesystem, LocalFilesystem
from nova_widgets import DataTable, MessageBox, Response
from nova_widgets.key_types import KeySequence
from tests._utils.local_copy_helpers import SchemeFs, ScriptedRunner, overwrite
from tests.integration.conftest import AppCtx, poll_until
from tests.integration.test_editor_integration import (
    back_in_the_list,
    capture_notices,
    content,
    editor_of,
    install_manager,
    only_entry,
    open_in_editor,
    open_with_f4,
)
from tests.integration.test_editor_keys_integration import open_dialog
from tests.nova_editor.view_menu import choose_view_item, view_marks

BINARY = b"\x00\x01\x02\x03binary payload\n"
TEXT = b"plain text\n"


def editors_on_the_stack(ctx: AppCtx) -> int:
    return sum(isinstance(s, EditorScreen) for s in ctx.app.screen_stack)


async def wait_hex(ctx: AppCtx, screen: EditorScreen, on: bool) -> None:
    await poll_until(ctx.pilot, lambda: screen.document.hex_mode is on and screen.document.shown.has_focus)
    assert screen.document.hex_mode is on
    assert screen.document.shown.has_focus


async def edit_first_byte(ctx: AppCtx) -> None:
    """Overwrite byte 0 with 0xEE in the focused hex view."""
    await ctx.pilot.press("ctrl+home", "e", "e")


def status_text(screen: EditorScreen) -> str:
    return screen.query_one(StatusLine).text


# ---------------------------------------------------------------------------
# Navigator-hosted editor on a local file
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
@pytest.mark.integration
async def test_f4_in_the_list_opens_a_binary_file_in_hex(app_ctx: AppCtx) -> None:
    (app_ctx.src_dir / "a.bin").write_bytes(BINARY)

    screen = await open_with_f4(app_ctx)

    await wait_hex(app_ctx, screen, True)
    assert screen.document.file_path == app_ctx.src_dir / "a.bin"
    assert app_ctx.app.local_copies.entries == []
    assert await view_marks(app_ctx.pilot, screen) == {"Line Numbers": True, "Wrap Mode": False, "Hex Mode": True}
    await poll_until(app_ctx.pilot, lambda: status_text(screen).endswith("Hex"))


@pytest.mark.asyncio
@pytest.mark.integration
async def test_f4_in_the_list_opens_a_text_file_in_text(app_ctx: AppCtx) -> None:
    """Control: detection does not turn a text file into a hex start."""
    (app_ctx.src_dir / "a.txt").write_bytes(TEXT)

    screen = await open_with_f4(app_ctx)

    assert not screen.document.hex_mode
    assert (await view_marks(app_ctx.pilot, screen))["Hex Mode"] is False


@pytest.mark.asyncio
@pytest.mark.integration
async def test_f4_in_the_editor_toggles_and_opens_no_second_editor(app_ctx: AppCtx) -> None:
    (app_ctx.src_dir / "a.bin").write_bytes(BINARY)
    screen = await open_with_f4(app_ctx)
    await wait_hex(app_ctx, screen, True)

    await app_ctx.pilot.press("f4")
    await wait_hex(app_ctx, screen, False)
    assert editor_of(app_ctx) is screen
    assert editors_on_the_stack(app_ctx) == 1
    await app_ctx.pilot.press("f4")
    await wait_hex(app_ctx, screen, True)

    assert editor_of(app_ctx) is screen
    assert editors_on_the_stack(app_ctx) == 1
    assert app_ctx.app.local_copies.entries == []


@pytest.mark.asyncio
@pytest.mark.integration
async def test_f4_toggles_a_text_file_into_hex_and_back(app_ctx: AppCtx) -> None:
    (app_ctx.src_dir / "a.txt").write_bytes(TEXT)
    screen = await open_with_f4(app_ctx)

    await app_ctx.pilot.press("f4")
    await wait_hex(app_ctx, screen, True)
    await app_ctx.pilot.press("f4")
    await wait_hex(app_ctx, screen, False)

    assert editors_on_the_stack(app_ctx) == 1
    assert screen.document.editor.text == TEXT.decode()


@pytest.mark.asyncio
@pytest.mark.integration
async def test_view_menu_hex_mode_toggles_in_the_navigator_editor(app_ctx: AppCtx) -> None:
    (app_ctx.src_dir / "a.bin").write_bytes(BINARY)
    screen = await open_with_f4(app_ctx)
    await wait_hex(app_ctx, screen, True)
    view_menu = screen.menu_bar.actions[3]
    enabled = {a.text: a.enabled for a in view_menu.actions}
    assert enabled["Line Numbers"] is False
    assert enabled["Wrap Mode"] is False
    assert enabled["Hex Mode"] is True

    await choose_view_item(app_ctx.pilot, screen, "Hex Mode")
    await wait_hex(app_ctx, screen, False)
    assert (await view_marks(app_ctx.pilot, screen))["Hex Mode"] is False
    assert all(a.enabled for a in view_menu.actions)
    await choose_view_item(app_ctx.pilot, screen, "Hex Mode")
    await wait_hex(app_ctx, screen, True)

    assert (await view_marks(app_ctx.pilot, screen))["Hex Mode"] is True
    assert editors_on_the_stack(app_ctx) == 1
    assert editor_of(app_ctx) is screen


@pytest.mark.asyncio
@pytest.mark.integration
async def test_f4_in_the_list_works_again_after_close(app_ctx: AppCtx) -> None:
    (app_ctx.src_dir / "a.bin").write_bytes(BINARY)
    screen = await open_with_f4(app_ctx)
    await wait_hex(app_ctx, screen, True)
    await app_ctx.pilot.press("f4")
    await wait_hex(app_ctx, screen, False)

    await app_ctx.pilot.press("ctrl+w")
    await back_in_the_list(app_ctx)
    await app_ctx.pilot.press("f4")
    await poll_until(app_ctx.pilot, lambda: isinstance(app_ctx.app.screen, EditorScreen))

    again = editor_of(app_ctx)
    assert again is not screen
    await wait_hex(app_ctx, again, True)
    assert editors_on_the_stack(app_ctx) == 1


@pytest.mark.asyncio
@pytest.mark.integration
async def test_rebinding_hex_mode_moves_the_key_in_the_navigator_editor(app_ctx: AppCtx) -> None:
    (app_ctx.src_dir / "a.bin").write_bytes(BINARY)
    app_ctx.screen.keymap_config.save({"editor.hex_mode": KeySequence.parse("ctrl+k")})
    screen = await open_with_f4(app_ctx)
    await wait_hex(app_ctx, screen, True)
    assert {a.id: a.shortcut_label for a in screen.ACTIONS}["editor.hex_mode"] == "Ctrl+K"

    await app_ctx.pilot.press("f4")
    await app_ctx.pilot.pause(delay=0.1)
    assert screen.document.hex_mode
    assert editor_of(app_ctx) is screen
    await app_ctx.pilot.press("ctrl+k")
    await wait_hex(app_ctx, screen, False)
    await app_ctx.pilot.press("ctrl+k")

    await wait_hex(app_ctx, screen, True)
    assert editors_on_the_stack(app_ctx) == 1


@pytest.mark.asyncio
@pytest.mark.integration
async def test_the_navigator_dialog_lists_hex_mode_with_f4(app_ctx: AppCtx) -> None:
    dialog = await open_dialog(app_ctx)

    table = dialog.query(DataTable).first()
    rows: dict[str, str] = {}
    for index in range(table.row_count):
        label, keys = table.get_row_at(index)
        rows[str(label)] = keys.plain.strip() if isinstance(keys, Text) else str(keys)

    assert rows["Editor: Hex Mode"] == "F4"
    await app_ctx.pilot.press("escape")


@pytest.mark.asyncio
@pytest.mark.integration
async def test_a_local_binary_file_is_edited_in_hex_and_saved_in_place(app_ctx: AppCtx) -> None:
    file = app_ctx.src_dir / "a.bin"
    file.write_bytes(BINARY)
    inode = os.stat(file).st_ino
    screen = await open_with_f4(app_ctx)
    await wait_hex(app_ctx, screen, True)

    await edit_first_byte(app_ctx)
    await poll_until(app_ctx.pilot, lambda: screen.document.editor.modified and "Modified" in status_text(screen))
    await app_ctx.pilot.press("ctrl+s")
    await poll_until(app_ctx.pilot, lambda: file.read_bytes() == b"\xee" + BINARY[1:])
    await poll_until(app_ctx.pilot, lambda: not screen.document.editor.modified and "Modified" not in status_text(screen))

    assert os.stat(file).st_ino == inode
    assert screen.document.editor.check_external_change() is ChangeKind.UNCHANGED
    assert isinstance(app_ctx.app.screen, EditorScreen)


# ---------------------------------------------------------------------------
# Non-local files: the hex view works on the local copy and a save is written back (REQ-24)
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
@pytest.mark.integration
async def test_an_ssh_binary_copy_in_hex_is_saved_in_place_and_written_back_once(app_ctx: AppCtx, tmp_path: Path) -> None:
    fs = SchemeFs({"/d/f.bin": BINARY})
    runner = ScriptedRunner()
    manager = install_manager(app_ctx, tmp_path, runner)

    screen = await open_in_editor(app_ctx, fs.path("/d/f.bin"))
    entry = only_entry(manager)
    await wait_hex(app_ctx, screen, True)
    assert screen.document.file_path == entry.copy.path
    inode = os.stat(entry.copy.path).st_ino

    await edit_first_byte(app_ctx)
    await app_ctx.pilot.press("ctrl+s")
    await poll_until(app_ctx.pilot, lambda: content(fs, "/d/f.bin") == b"\xee" + BINARY[1:])
    await poll_until(app_ctx.pilot, lambda: entry.status is CopyStatus.SYNCED)

    assert os.stat(entry.copy.path).st_ino == inode
    assert entry.copy.path.read_bytes() == b"\xee" + BINARY[1:]
    assert not screen.document.editor.modified
    assert screen.document.editor.check_external_change() is ChangeKind.UNCHANGED
    assert isinstance(app_ctx.app.screen, EditorScreen)
    await app_ctx.pilot.press("ctrl+w")
    await back_in_the_list(app_ctx)
    await poll_until(app_ctx.pilot, lambda: entry.detector is None)
    assert len(runner.sync_jobs) == 1
    assert manager.unsynced() == []
    assert content(fs, "/d/f.bin") == b"\xee" + BINARY[1:]


@pytest.mark.asyncio
@pytest.mark.integration
async def test_f4_toggles_inside_a_copy_session_with_one_editor_and_one_entry(app_ctx: AppCtx, tmp_path: Path) -> None:
    fs = SchemeFs({"/d/f.bin": BINARY})
    manager = install_manager(app_ctx, tmp_path, ScriptedRunner())
    screen = await open_in_editor(app_ctx, fs.path("/d/f.bin"))
    await wait_hex(app_ctx, screen, True)

    await app_ctx.pilot.press("f4")
    await wait_hex(app_ctx, screen, False)
    await app_ctx.pilot.press("f4")
    await wait_hex(app_ctx, screen, True)

    assert editors_on_the_stack(app_ctx) == 1
    assert len(manager.entries) == 1
    assert editor_of(app_ctx) is screen


@pytest.mark.asyncio
@pytest.mark.integration
async def test_a_length_changing_hex_edit_is_streamed_and_written_back(app_ctx: AppCtx, tmp_path: Path) -> None:
    fs = SchemeFs({"/d/f.bin": BINARY})
    runner = ScriptedRunner()
    manager = install_manager(app_ctx, tmp_path, runner)
    screen = await open_in_editor(app_ctx, fs.path("/d/f.bin"))
    entry = only_entry(manager)
    await wait_hex(app_ctx, screen, True)
    inode = os.stat(entry.copy.path).st_ino

    await app_ctx.pilot.press("ctrl+home", "insert", "e", "e", "ctrl+s")
    await poll_until(app_ctx.pilot, lambda: content(fs, "/d/f.bin") == b"\xee" + BINARY)
    await poll_until(app_ctx.pilot, lambda: entry.status is CopyStatus.SYNCED)

    assert os.stat(entry.copy.path).st_ino != inode
    assert entry.copy.path.read_bytes() == b"\xee" + BINARY
    await app_ctx.pilot.press("ctrl+w")
    await back_in_the_list(app_ctx)
    await poll_until(app_ctx.pilot, lambda: entry.detector is None)
    assert len(runner.sync_jobs) == 1


@pytest.mark.asyncio
@pytest.mark.integration
async def test_undo_after_an_in_place_save_and_a_second_save_write_back_again(app_ctx: AppCtx, tmp_path: Path) -> None:
    fs = SchemeFs({"/d/f.bin": BINARY})
    runner = ScriptedRunner()
    manager = install_manager(app_ctx, tmp_path, runner)
    screen = await open_in_editor(app_ctx, fs.path("/d/f.bin"))
    entry = only_entry(manager)
    await wait_hex(app_ctx, screen, True)
    await edit_first_byte(app_ctx)
    await app_ctx.pilot.press("ctrl+s")
    await poll_until(app_ctx.pilot, lambda: content(fs, "/d/f.bin") == b"\xee" + BINARY[1:])
    await poll_until(app_ctx.pilot, lambda: len(runner.sync_jobs) == 1 and entry.status is CopyStatus.SYNCED)

    await app_ctx.pilot.press("ctrl+z", "ctrl+s")
    await poll_until(app_ctx.pilot, lambda: content(fs, "/d/f.bin") == BINARY)
    await poll_until(app_ctx.pilot, lambda: len(runner.sync_jobs) == 2 and entry.status is CopyStatus.SYNCED)

    assert entry.copy.path.read_bytes() == BINARY
    assert not screen.document.editor.modified
    await app_ctx.pilot.press("ctrl+w")
    await back_in_the_list(app_ctx)
    await poll_until(app_ctx.pilot, lambda: entry.detector is None)
    assert len(runner.sync_jobs) == 2
    assert content(fs, "/d/f.bin") == BINARY


@pytest.mark.asyncio
@pytest.mark.integration
async def test_a_conflict_after_a_hex_save_is_reported_at_close(app_ctx: AppCtx, tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    notices = capture_notices(app_ctx, monkeypatch)
    fs = SchemeFs({"/d/f.bin": BINARY})
    runner = ScriptedRunner([Response.SKIP])
    manager = install_manager(app_ctx, tmp_path, runner)
    screen = await open_in_editor(app_ctx, fs.path("/d/f.bin"))
    entry = only_entry(manager)
    await wait_hex(app_ctx, screen, True)
    overwrite(fs, "/d/f.bin", b"server\n")

    await edit_first_byte(app_ctx)
    await app_ctx.pilot.press("ctrl+s")
    await poll_until(app_ctx.pilot, lambda: entry.status is CopyStatus.CONFLICT)
    assert isinstance(app_ctx.app.screen, EditorScreen)
    await app_ctx.pilot.press("ctrl+w")
    await back_in_the_list(app_ctx)

    await poll_until(app_ctx.pilot, lambda: any("changed on the source" in n for n in notices))
    assert content(fs, "/d/f.bin") == b"server\n"
    assert entry.copy.path.read_bytes() == b"\xee" + BINARY[1:]
    assert manager.unsynced() == [entry]


@pytest.mark.asyncio
@pytest.mark.integration
async def test_a_binary_jar_member_opens_in_hex_view_only(app_ctx: AppCtx, tmp_path: Path) -> None:
    jar = tmp_path / "x.jar"
    with zipfile.ZipFile(jar, "w") as zf:
        zf.writestr("a.bin", BINARY)
    before = jar.read_bytes()
    local = LocalFilesystem.singleton()
    member = ArchiveFilesystem(local.path(tmp_path), local.path(jar)).path("/a.bin")
    runner = ScriptedRunner()
    manager = install_manager(app_ctx, tmp_path, runner)

    screen = await open_in_editor(app_ctx, member)
    await wait_hex(app_ctx, screen, True)
    assert screen.document.editor.read_only
    await edit_first_byte(app_ctx)
    await app_ctx.pilot.press("ctrl+s")
    await app_ctx.pilot.pause(delay=0.2)

    assert not screen.document.editor.modified
    assert "Modified" not in status_text(screen)
    assert screen.document.editor.document.read_bytes(0, len(BINARY)) == BINARY
    assert not any(isinstance(s, MessageBox) for s in app_ctx.app.screen_stack)
    await app_ctx.pilot.press("ctrl+w")
    await back_in_the_list(app_ctx)
    assert only_entry(manager).status is CopyStatus.READ_ONLY
    assert runner.sync_jobs == []
    assert jar.read_bytes() == before
