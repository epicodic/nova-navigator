"""Pilot tests of the nova_edit save, reload and external-change UI (popups, dialogs, keys, poll, quit)."""

from __future__ import annotations

import os
import re
import threading
import time
from pathlib import Path

import pytest
from textual import events

from nova_editor.app import NovaEditApp, main
from nova_editor.core.byte_source import ChangeKind
from nova_editor.core.save import SaveIo
from nova_editor.decisions import CHANGE_TEXT
from nova_editor.status_line import StatusLine
from nova_editor.widget import ExternalCheck, NovaTextArea
from tests.nova_editor.dialog_helpers import (
    answer,
    box_message,
    box_title,
    has_dialog,
    open_box,
    open_file_dialog,
)
from tests.nova_editor.helpers_view import wait_until
from tests.nova_editor.save_widget_helpers import SETTINGS, Gate, wait_saved

SECOND = 2
APP_SOURCE = Path(__file__).parents[2] / "src" / "nova_editor" / "app.py"


@pytest.fixture(autouse=True)
def small_chunks(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(NovaTextArea, "save_settings", SETTINGS)


def make_file(tmp_path: Path, text: str = "one\n") -> Path:
    path = tmp_path / "f.txt"
    path.write_text(text)
    return path


def status_of(app: NovaEditApp) -> StatusLine:
    return app.query_one(StatusLine)


@pytest.mark.asyncio
async def test_ctrl_s_saves_an_edited_file(tmp_path: Path) -> None:
    path = make_file(tmp_path)
    app = NovaEditApp(file_path=path)
    async with app.run_test() as pilot:
        await pilot.pause()
        await pilot.press("x", "ctrl+s")
        assert app.editor is not None
        await wait_until(pilot, lambda: path.read_text() == "xone\n")
        await wait_saved(pilot, app.editor)
        status = status_of(app)
        await wait_until(pilot, lambda: status.note is not None and status.note.startswith("Saved"))
        assert not app.editor.modified


@pytest.mark.asyncio
async def test_f5_reloads_an_unmodified_document(tmp_path: Path) -> None:
    path = make_file(tmp_path)
    app = NovaEditApp(file_path=path)
    async with app.run_test() as pilot:
        await pilot.pause()
        path.write_text("changed on disk\n")
        await pilot.press("f5")
        assert app.editor is not None
        await wait_until(pilot, lambda: app.editor is not None and app.editor.text == "changed on disk\n")


@pytest.mark.asyncio
async def test_escape_cancels_a_running_save(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    path = make_file(tmp_path, "a" * 100 + "\n")
    gate = Gate()
    monkeypatch.setattr(NovaTextArea, "save_io", gate.io())
    app = NovaEditApp(file_path=path)
    try:
        async with app.run_test() as pilot:
            await pilot.pause()
            await pilot.press("x", "ctrl+s")
            await wait_until(pilot, gate.reached.is_set)
            assert app.editor is not None
            assert app.editor.saving
            await pilot.press("escape")
            gate.release()
            await wait_saved(pilot, app.editor)
            status = status_of(app)
            await wait_until(pilot, lambda: status.note == "Save cancelled")
            assert path.read_text() == "a" * 100 + "\n"
    finally:
        gate.release()


@pytest.mark.asyncio
async def test_escape_keeps_its_pending_jump_binding(tmp_path: Path) -> None:
    app = NovaEditApp(file_path=make_file(tmp_path))
    async with app.run_test() as pilot:
        await pilot.pause()
        assert app.editor is not None
        assert app.screen.check_action("cancel_save", ()) is False  # no save runs: Esc is left to the widget
        assert any(getattr(b, "key", None) == "escape" and b.action == "cancel_pending" for b in NovaTextArea.BINDINGS)


@pytest.mark.asyncio
async def test_the_note_shows_the_progress_while_the_save_runs(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    path = make_file(tmp_path, "a" * 1000 + "\n")
    gate = Gate()
    real = SaveIo()
    writes = [0]

    def second_write_waits(fd: int, data: bytes | memoryview) -> int:
        writes[0] += 1  # the first write is reported at once, then the writer blocks
        if writes[0] == SECOND:
            gate.reached.set()
            assert gate.opened.wait(10.0)
        return real.write(fd, data)

    monkeypatch.setattr(NovaTextArea, "save_io", SaveIo(write=second_write_waits))
    app = NovaEditApp(file_path=path)
    try:
        async with app.run_test() as pilot:
            await pilot.pause()
            await pilot.press("x", "ctrl+s")
            await wait_until(pilot, gate.reached.is_set)
            status = status_of(app)
            await wait_until(pilot, lambda: status.note is not None and status.note.startswith("Saving"))
            assert re.fullmatch(r"Saving {2}\S+ / \S+ (B|KiB|MiB|GiB) {2}\d+ % {2}Esc cancels", status.note or ""), status.note
            gate.release()
            await wait_saved(pilot, app.editor)
    finally:
        gate.release()


@pytest.mark.asyncio
async def test_the_note_follows_the_save_phases(tmp_path: Path) -> None:
    app = NovaEditApp(file_path=make_file(tmp_path))
    async with app.run_test() as pilot:
        await pilot.pause()
        editor = app.editor
        assert editor is not None
        status = status_of(app)
        gib = 1024**3
        editor.post_message(NovaTextArea.SaveProgress("writing", int(1.2 * gib), 5 * gib, editor))
        await wait_until(pilot, lambda: status.note == "Saving  1.2 / 5.0 GiB  24 %  Esc cancels")
        editor.post_message(NovaTextArea.SaveProgress("flushing", 5, 5, editor))
        await wait_until(pilot, lambda: status.note == "Flushing")
        editor.post_message(NovaTextArea.SaveProgress("history", 5, 5, editor))
        await wait_until(pilot, lambda: status.note == "Preserving undo history")


@pytest.mark.asyncio
async def test_the_saved_note_clears_itself(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(StatusLine, "NOTE_SECONDS", 0.5)  # long enough for the polling wait below to see the note
    app = NovaEditApp(file_path=make_file(tmp_path))
    async with app.run_test() as pilot:
        await pilot.pause()
        status = status_of(app)
        await pilot.press("x", "ctrl+s")
        await wait_until(pilot, lambda: status.note is not None and status.note.startswith("Saved"))
        await wait_until(pilot, lambda: status.note is None)


def test_interim_code_is_gone() -> None:
    source = APP_SOURCE.read_text()
    for needle in ("Interim", "8 MiB", "TEXT_LIMIT"):
        assert needle not in source, needle


@pytest.mark.asyncio
async def test_unreadable_file_refuses_plain_save(tmp_path: Path) -> None:
    path = make_file(tmp_path, "precious")
    path.chmod(0)
    if os.access(path, os.R_OK):
        path.chmod(0o644)
        pytest.skip("permissions are not enforced for this user")
    app = NovaEditApp(file_path=path)
    try:
        async with app.run_test() as pilot:
            await pilot.pause()
            await pilot.press("x", "ctrl+s")
            await wait_until(pilot, lambda: open_file_dialog(app) is not None)  # only a save as is offered
            assert any("Not saved" in n.message for n in app._notifications)
            await pilot.press("escape")
            await wait_until(pilot, lambda: not has_dialog(app))
    finally:
        path.chmod(0o644)
    assert path.read_text() == "precious"


@pytest.mark.asyncio
async def test_fifo_is_refused_without_blocking(tmp_path: Path) -> None:
    fifo = tmp_path / "pipe"
    os.mkfifo(fifo)
    app = NovaEditApp(file_path=fifo)
    async with app.run_test() as pilot:
        await pilot.pause()
        assert app.editor is not None
        assert app.editor.text == ""
        assert any("not a regular file" in n.message for n in app._notifications)


def test_main_refuses_a_fifo(tmp_path: Path, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]) -> None:
    fifo = tmp_path / "pipe"
    os.mkfifo(fifo)
    monkeypatch.setattr("sys.argv", ["nova_edit", str(fifo)])
    with pytest.raises(SystemExit) as info:
        main()
    assert info.value.code != 0
    assert "not a regular file" in capsys.readouterr().err


@pytest.mark.asyncio
async def test_ctrl_q_during_a_save_cancels_then_asks_and_quit_anyway_exits(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    from tests.nova_editor.dialog_helpers import answer, open_box
    from tests.nova_editor.test_app_quit import count_exits

    path = make_file(tmp_path, "a" * 100 + "\n")
    gate = Gate()
    exits = count_exits(monkeypatch)
    monkeypatch.setattr(NovaTextArea, "save_io", gate.io())
    app = NovaEditApp(file_path=path)
    try:
        async with app.run_test() as pilot:
            await pilot.pause()
            await pilot.press("x", "ctrl+s")
            await wait_until(pilot, gate.reached.is_set)
            await pilot.press("ctrl+q")
            await wait_until(pilot, lambda: open_box(app) is not None, limit=5.0)  # after the bounded wait for the cancel
            assert exits == []
            await answer(pilot, app, "Quit Anyway")
            await wait_until(pilot, lambda: exits == [1], limit=5.0)
    finally:
        gate.release()
    assert path.read_text() == "a" * 100 + "\n"


@pytest.mark.asyncio
async def test_poll_runs_in_a_thread_and_does_not_delay_keys(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    app = NovaEditApp(file_path=make_file(tmp_path))
    app.POLL_SECONDS = 0.05
    entered = threading.Event()
    release = threading.Event()
    threads: list[int] = []

    def slow_check(_check: ExternalCheck) -> ChangeKind:
        threads.append(threading.get_ident())
        entered.set()
        release.wait(1.0)  # a stat that blocks
        return ChangeKind.UNCHANGED

    async with app.run_test() as pilot:
        await pilot.pause()
        assert app.editor is not None
        baseline = 0.0
        for _ in range(3):  # what a key press costs through the pilot while no poll blocks
            started = time.monotonic()
            await pilot.press("left")
            baseline = max(baseline, time.monotonic() - started)
        monkeypatch.setattr(ExternalCheck, "run", slow_check)
        await wait_until(pilot, entered.is_set)
        started = time.monotonic()
        await pilot.press("x")
        elapsed = time.monotonic() - started
        release.set()
        assert app.editor.text == "xone\n"
        assert elapsed - baseline < 0.05, (elapsed, baseline)
    assert threads
    assert threads[0] != threading.get_ident()


@pytest.mark.asyncio
async def test_poll_applies_its_result_on_the_ui_thread(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    path = make_file(tmp_path)
    app = NovaEditApp(file_path=path)
    app.POLL_SECONDS = 0.05
    threads: list[int] = []
    real = NovaTextArea._fail_source

    def recording(self: NovaTextArea, reason: str, kind: ChangeKind = ChangeKind.MODIFIED) -> None:
        threads.append(threading.get_ident())
        real(self, reason, kind)

    monkeypatch.setattr(NovaTextArea, "_fail_source", recording)
    async with app.run_test() as pilot:
        await pilot.pause()
        path.write_text("changed on disk, longer\n")
        await wait_until(pilot, lambda: open_box(app) is not None)
        box = open_box(app)
        assert box is not None
        assert box_message(box).startswith(CHANGE_TEXT[ChangeKind.MODIFIED])
    assert threads == [threading.get_ident()]


@pytest.mark.asyncio
async def test_app_focus_runs_the_check(tmp_path: Path) -> None:
    path = make_file(tmp_path)
    app = NovaEditApp(file_path=path)
    app.POLL_SECONDS = 1000.0
    async with app.run_test() as pilot:
        await pilot.pause()
        path.write_text("changed on disk, longer\n")
        await pilot.pause(0.1)
        assert open_box(app) is None
        app.post_message(events.AppFocus())
        await wait_until(pilot, lambda: open_box(app) is not None)


@pytest.mark.asyncio
@pytest.mark.parametrize("how", ["cancel", "fail"])
async def test_a_change_seen_during_a_save_is_announced_when_the_save_does_not_commit(tmp_path: Path, monkeypatch: pytest.MonkeyPatch, how: str) -> None:
    gate = Gate()
    real = gate.io()

    def refuse(_source: str, _target: str) -> None:
        raise PermissionError(13, "no way")

    monkeypatch.setattr(NovaTextArea, "save_io", real if how == "cancel" else SaveIo(write=real.write, replace=refuse))
    path = make_file(tmp_path)
    app = NovaEditApp(file_path=path)
    app.POLL_SECONDS = 1000.0
    async with app.run_test() as pilot:
        await pilot.pause()
        editor = app.editor
        assert editor is not None
        await pilot.press("x", "ctrl+s")
        assert gate.reached.wait(10.0)
        editor._fail_source("the file changed on disk (modified)", ChangeKind.MODIFIED)
        await pilot.pause(0.1)
        assert open_box(app) is None, "the question waits for the end of the save"
        if how == "cancel":
            editor.cancel_save()
        gate.release()
        await wait_saved(pilot, editor)
        if how == "fail":
            await wait_until(pilot, lambda: open_box(app) is not None)
            failure = open_box(app)
            assert failure is not None
            assert box_title(failure) == "Save failed"
            await answer(pilot, app, "OK")
        await wait_until(pilot, lambda: open_box(app) is not None)
        box = open_box(app)
        assert box is not None
        assert box_message(box).startswith(CHANGE_TEXT[ChangeKind.MODIFIED])


@pytest.mark.asyncio
async def test_a_change_seen_during_a_save_is_dropped_when_the_save_commits(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    gate = Gate()
    monkeypatch.setattr(NovaTextArea, "save_io", gate.io())
    path = make_file(tmp_path)
    app = NovaEditApp(file_path=path)
    app.POLL_SECONDS = 1000.0
    async with app.run_test() as pilot:
        await pilot.pause()
        editor = app.editor
        assert editor is not None
        await pilot.press("x", "ctrl+s")
        assert gate.reached.wait(10.0)
        editor._fail_source("the file changed on disk (modified)", ChangeKind.MODIFIED)
        gate.release()
        await wait_saved(pilot, editor)
        await pilot.pause(0.1)
        assert open_box(app) is None
        assert editor.check_external_change() is ChangeKind.UNCHANGED


@pytest.mark.asyncio
async def test_a_source_changed_message_that_arrives_after_the_save_is_ignored(tmp_path: Path) -> None:
    path = make_file(tmp_path)
    app = NovaEditApp(file_path=path)
    app.POLL_SECONDS = 1000.0
    async with app.run_test() as pilot:
        await pilot.pause()
        editor = app.editor
        assert editor is not None
        await pilot.press("x", "ctrl+s")
        await wait_saved(pilot, editor)
        editor.post_message(NovaTextArea.SourceChanged("the file changed on disk (modified)", editor, ChangeKind.MODIFIED).set_sender(editor))
        await pilot.pause(0.1)
        assert open_box(app) is None
