"""Pilot tests of the nova_edit save, reload and external-change UI (bars, keys, poll, quit)."""

from __future__ import annotations

import os
import re
import threading
import time
from pathlib import Path

import pytest

from nova_editor.app import ConfirmBar, NovaEditApp, PathBar, SaveBar, main
from nova_editor.core.byte_source import ChangeKind
from nova_editor.core.save import SaveIo
from nova_editor.widget import NovaTextArea
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


def bars(app: NovaEditApp) -> tuple[SaveBar, ConfirmBar, PathBar]:
    return app.query_one(SaveBar), app.query_one(ConfirmBar), app.query_one(PathBar)


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
        save_bar, _, _ = bars(app)
        assert save_bar.display
        assert save_bar.line.startswith("Saved")
        assert not app.editor.modified


@pytest.mark.asyncio
async def test_ctrl_s_without_a_file_opens_the_path_bar() -> None:
    app = NovaEditApp(file_path=None)
    async with app.run_test() as pilot:
        await pilot.pause()
        await pilot.press("a", "ctrl+s")
        await pilot.pause()
        _, _, path_bar = bars(app)
        assert path_bar.display
        assert app.focused is path_bar


@pytest.mark.asyncio
async def test_f2_opens_the_prefilled_path_bar_and_enter_saves_as(tmp_path: Path) -> None:
    path = make_file(tmp_path)
    other = tmp_path / "other.txt"
    app = NovaEditApp(file_path=path)
    async with app.run_test() as pilot:
        await pilot.pause()
        await pilot.press("f2")
        await pilot.pause()
        _, _, path_bar = bars(app)
        assert path_bar.display
        assert path_bar.value == str(path)
        path_bar.value = str(other)
        await pilot.press("enter")
        assert app.editor is not None
        await wait_until(pilot, other.exists)
        await wait_saved(pilot, app.editor)
        assert other.read_text() == "one\n"
        assert not path_bar.display
        assert app.editor.file_path == other


@pytest.mark.asyncio
async def test_f2_then_escape_closes_the_path_bar(tmp_path: Path) -> None:
    app = NovaEditApp(file_path=make_file(tmp_path))
    async with app.run_test() as pilot:
        await pilot.pause()
        await pilot.press("f2")
        await pilot.pause()
        await pilot.press("escape")
        await pilot.pause()
        _, _, path_bar = bars(app)
        assert not path_bar.display
        assert app.focused is app.editor


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
async def test_f5_of_a_modified_document_asks_first(tmp_path: Path) -> None:
    path = make_file(tmp_path)
    app = NovaEditApp(file_path=path)
    async with app.run_test() as pilot:
        await pilot.pause()
        await pilot.press("x")
        path.write_text("changed on disk\n")
        await pilot.press("f5")
        await pilot.pause()
        _, confirm, _ = bars(app)
        assert confirm.display
        assert app.editor is not None
        assert app.editor.text == "xone\n"
        await pilot.press("escape")
        await pilot.pause()
        assert not confirm.display
        assert app.editor.text == "xone\n"
        await pilot.press("f5")
        await pilot.pause()
        await pilot.press("r")
        await wait_until(pilot, lambda: app.editor is not None and app.editor.text == "changed on disk\n")
        assert not confirm.display


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
            save_bar, _, _ = bars(app)
            assert "cancel" in save_bar.line.lower()
            assert path.read_text() == "a" * 100 + "\n"
    finally:
        gate.release()


@pytest.mark.asyncio
async def test_escape_keeps_its_pending_jump_binding(tmp_path: Path) -> None:
    app = NovaEditApp(file_path=make_file(tmp_path))
    async with app.run_test() as pilot:
        await pilot.pause()
        assert app.editor is not None
        assert app.check_action("cancel_save", ()) is False  # no save runs: Esc is left to the widget
        assert any(getattr(b, "key", None) == "escape" and b.action == "cancel_pending" for b in NovaTextArea.BINDINGS)


@pytest.mark.asyncio
async def test_save_bar_shows_progress_while_the_save_runs(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
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
            save_bar, _, _ = bars(app)
            await wait_until(pilot, lambda: save_bar.line.startswith("Saving"))
            assert re.fullmatch(r"Saving {2}\S+ / \S+ (B|KiB|MiB|GiB) {2}\d+ % {2}Esc cancels", save_bar.line), save_bar.line
            gate.release()
            assert app.editor is not None
            await wait_saved(pilot, app.editor)
    finally:
        gate.release()


@pytest.mark.asyncio
async def test_save_bar_phases_and_units(tmp_path: Path) -> None:
    app = NovaEditApp(file_path=make_file(tmp_path))
    async with app.run_test() as pilot:
        await pilot.pause()
        editor = app.editor
        assert editor is not None
        save_bar, _, _ = bars(app)
        gib = 1024**3
        editor.post_message(NovaTextArea.SaveProgress("writing", int(1.2 * gib), 5 * gib, editor))
        await wait_until(pilot, lambda: save_bar.line.startswith("Saving"))
        assert save_bar.line == "Saving  1.2 / 5.0 GiB  24 %  Esc cancels"
        editor.post_message(NovaTextArea.SaveProgress("flushing", 5, 5, editor))
        await wait_until(pilot, lambda: save_bar.line == "Flushing")
        editor.post_message(NovaTextArea.SaveProgress("history", 5, 5, editor))
        await wait_until(pilot, lambda: save_bar.line == "Preserving undo history")


@pytest.mark.asyncio
async def test_result_line_disappears_after_four_seconds(tmp_path: Path) -> None:
    path = make_file(tmp_path)
    app = NovaEditApp(file_path=path)
    async with app.run_test() as pilot:
        await pilot.pause()
        now = [1000.0]
        save_bar, _, _ = bars(app)
        save_bar.clock = lambda: now[0]
        await pilot.press("x", "ctrl+s")
        await wait_until(pilot, lambda: save_bar.line.startswith("Saved"))
        now[0] += 3.9
        await pilot.pause(0.3)
        assert save_bar.display
        now[0] += 0.2
        await wait_until(pilot, lambda: not save_bar.display)


@pytest.mark.asyncio
async def test_failure_stays_until_a_key_is_pressed(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    path = make_file(tmp_path)

    def refuse(_source: str, _target: str) -> None:
        raise PermissionError(13, "no way")

    monkeypatch.setattr(NovaTextArea, "save_io", SaveIo(replace=refuse))
    app = NovaEditApp(file_path=path)
    async with app.run_test() as pilot:
        await pilot.pause()
        now = [1000.0]
        save_bar, _, _ = bars(app)
        save_bar.clock = lambda: now[0]
        await pilot.press("x", "ctrl+s")
        await wait_until(pilot, lambda: "failed" in save_bar.line.lower())
        assert "no way" in save_bar.line
        assert "replace" in save_bar.line
        now[0] += 60
        await pilot.pause(0.3)
        assert save_bar.display
        await pilot.press("y")
        await pilot.pause()
        assert not save_bar.display
    assert path.read_text() == "one\n"


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
            await pilot.pause(0.2)
            _, _, path_bar = bars(app)
            assert path_bar.display  # only a save as is offered
            assert any("Not saved" in n.message for n in app._notifications)
    finally:
        path.chmod(0o644)
    assert path.read_text() == "precious"


@pytest.mark.asyncio
async def test_new_file_that_exists_at_save_time_asks_for_confirmation(tmp_path: Path) -> None:
    path = tmp_path / "new.txt"
    app = NovaEditApp(file_path=path)
    async with app.run_test() as pilot:
        await pilot.pause()
        path.write_text("from elsewhere")
        await pilot.press("h", "i", "ctrl+s")
        await pilot.pause(0.2)
        _, confirm, _ = bars(app)
        assert confirm.display
        assert confirm.kind is ChangeKind.CREATED
        assert path.read_text() == "from elsewhere"
        await pilot.press("o")
        assert app.editor is not None
        await wait_until(pilot, lambda: path.read_text() == "hi")
        await wait_saved(pilot, app.editor)
        assert not confirm.display


@pytest.mark.asyncio
async def test_confirm_bar_keys_for_an_external_change(tmp_path: Path) -> None:
    path = make_file(tmp_path)
    other = tmp_path / "other.txt"
    app = NovaEditApp(file_path=path)
    async with app.run_test() as pilot:
        await pilot.pause()
        assert app.editor is not None
        await pilot.press("x")
        path.write_text("changed on disk, longer\n")
        await pilot.press("ctrl+s")
        await pilot.pause(0.2)
        _, confirm, path_bar = bars(app)
        assert confirm.display
        assert path.read_text() == "changed on disk, longer\n"
        # Esc keeps
        await pilot.press("escape")
        await pilot.pause()
        assert not confirm.display
        assert path.read_text() == "changed on disk, longer\n"
        # A: save as
        await pilot.press("ctrl+s")
        await pilot.pause(0.2)
        assert confirm.display
        await pilot.press("a")
        await pilot.pause()
        assert path_bar.display
        assert not confirm.display
        path_bar.value = str(other)
        await pilot.press("enter")
        await wait_until(pilot, other.exists)
        await wait_saved(pilot, app.editor)
        assert other.read_text() == "xone\n"


@pytest.mark.asyncio
async def test_confirm_bar_overwrite_key(tmp_path: Path) -> None:
    path = make_file(tmp_path)
    app = NovaEditApp(file_path=path)
    async with app.run_test() as pilot:
        await pilot.pause()
        assert app.editor is not None
        await pilot.press("x")
        path.write_text("changed on disk, longer\n")
        await pilot.press("ctrl+s")
        await pilot.pause(0.2)
        _, confirm, _ = bars(app)
        assert confirm.display
        await pilot.press("o")
        await wait_until(pilot, lambda: path.read_text() == "xone\n")
        await wait_saved(pilot, app.editor)
        assert not confirm.display


@pytest.mark.asyncio
async def test_confirm_bar_reload_key(tmp_path: Path) -> None:
    path = make_file(tmp_path)
    app = NovaEditApp(file_path=path)
    async with app.run_test() as pilot:
        await pilot.pause()
        assert app.editor is not None
        await pilot.press("x")
        path.write_text("changed on disk, longer\n")
        await pilot.press("ctrl+s")
        await pilot.pause(0.2)
        _, confirm, _ = bars(app)
        assert confirm.display
        await pilot.press("r")
        await wait_until(pilot, lambda: app.editor is not None and app.editor.text == "changed on disk, longer\n")
        assert not confirm.display


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
async def test_ctrl_q_during_a_save_cancels_and_exits(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    path = make_file(tmp_path, "a" * 100 + "\n")
    gate = Gate()
    monkeypatch.setattr(NovaTextArea, "save_io", gate.io())
    app = NovaEditApp(file_path=path)
    try:
        async with app.run_test() as pilot:
            await pilot.pause()
            await pilot.press("x", "ctrl+s")
            await wait_until(pilot, gate.reached.is_set)
            started = time.monotonic()
            await pilot.press("ctrl+q")
            await wait_until(pilot, lambda: app._exit, limit=5.0)
            assert time.monotonic() - started < 3.0
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

    def slow_check() -> ChangeKind:
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
        monkeypatch.setattr(app.editor, "check_external_change", slow_check)
        await wait_until(pilot, entered.is_set)
        started = time.monotonic()
        await pilot.press("x")
        elapsed = time.monotonic() - started
        release.set()
        assert app.editor.text == "xone\n"
        assert elapsed - baseline < 0.05, (elapsed, baseline)
    assert threads
    assert threads[0] != threading.get_ident()
