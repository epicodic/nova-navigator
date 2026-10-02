"""Pilot tests of saving from `NovaTextArea` (ACT5 task 14): the API, the one-terminal-message invariant, the edit lock and closing mid-save."""

from __future__ import annotations

import threading
from collections.abc import AsyncIterator, Callable
from contextlib import asynccontextmanager
from dataclasses import dataclass
from itertools import pairwise
from pathlib import Path

import pytest
from textual.message import Message
from textual.pilot import Pilot

from nova_editor.core import LineIndex, PreadSource
from nova_editor.core.rebase import RebasePlan
from nova_editor.core.save import FileIdentity, SaveIo, SaveSettings
from nova_editor.core.save import SaveProgress as CoreSaveProgress
from nova_editor.document._lazy_document import LazyDocument
from nova_editor.widget import NovaTextArea
from nova_editor.widget._text_area import _SaveRun
from tests.nova_editor.helpers_view import wait_until
from tests.nova_editor.save_widget_helpers import SETTINGS, Gate, SaveHost, leftovers, open_files, text_of, wait_saved

BODY = "".join(f"row {n} {'x' * (n % 9)}\n" for n in range(60))


def _file(tmp_path: Path, name: str = "doc.txt") -> Path:
    path = tmp_path / name
    path.write_text(BODY)
    return path


@pytest.fixture(autouse=True)
def small_chunks(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(NovaTextArea, "save_settings", SETTINGS)


def test_a_widget_without_a_path_has_nothing_to_save() -> None:
    area = NovaTextArea(text="abc")
    assert area.file_path is None
    assert not area.modified
    assert not area.saving


@pytest.mark.asyncio
async def test_save_without_any_path_returns_false_and_posts_nothing() -> None:
    area = NovaTextArea(text="abc")
    host = SaveHost(area)
    async with host.run_test() as pilot:
        await pilot.pause()
        area.focus()
        await pilot.press("x")
        assert area.modified
        assert area.save() is False
        await pilot.pause(0.1)
        assert host.saves == []


@pytest.mark.asyncio
async def test_unmodified_plain_save_posts_nothing(tmp_path: Path) -> None:
    path = _file(tmp_path)
    area = NovaTextArea.open(path)
    host = SaveHost(area)
    async with host.run_test() as pilot:
        await pilot.pause()
        assert area.file_path == path
        assert area.save() is False
        await pilot.pause(0.1)
        assert host.saves == []


@pytest.mark.asyncio
async def test_save_writes_the_edit_and_ends_unmodified(tmp_path: Path) -> None:
    path = _file(tmp_path)
    area = NovaTextArea.open(path)
    host = SaveHost(area)
    async with host.run_test() as pilot:
        await pilot.pause()
        area.focus()
        await pilot.press("x", "y")
        assert area.modified
        before = text_of(area)
        assert area.save() is True
        assert area.saving
        await wait_saved(pilot, area)
        assert path.read_bytes() == before == text_of(area)
        assert not area.modified
        assert area.file_path == path
        (done,) = host.terminals()
        assert isinstance(done, NovaTextArea.Saved)
        assert done.path == path
        assert done.length == len(before)
        await pilot.press("z")  # the edit lock is lifted
        assert len(text_of(area)) == len(before) + 1
        assert host.refused == []
    assert leftovers(tmp_path) == []


@pytest.mark.asyncio
async def test_save_as_writes_a_copy_and_leaves_the_original(tmp_path: Path) -> None:
    path = _file(tmp_path)
    copy = tmp_path / "copy.txt"
    area = NovaTextArea.open(path)
    host = SaveHost(area)
    async with host.run_test() as pilot:
        await pilot.pause()
        assert area.save(copy) is True  # unmodified: save-as still writes
        await wait_saved(pilot, area)
        assert copy.read_text() == BODY
        assert path.read_text() == BODY
        assert area.file_path == copy
        assert len(host.terminals()) == 1


@pytest.mark.asyncio
async def test_save_while_saving_returns_false(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    gate = Gate()
    monkeypatch.setattr(NovaTextArea, "save_io", gate.io())
    path = _file(tmp_path)
    area = NovaTextArea.open(path)
    host = SaveHost(area)
    async with host.run_test() as pilot:
        await pilot.pause()
        area.focus()
        await pilot.press("x")
        assert area.save() is True
        assert gate.reached.wait(10)
        assert area.save() is False
        gate.release()
        await wait_saved(pilot, area)
        assert len(host.terminals()) == 1


@pytest.mark.asyncio
async def test_failure_posts_one_save_failed_and_unlocks(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    def broken(_fd: int, _data: bytes | memoryview) -> int:
        raise OSError(28, "No space left on device")

    monkeypatch.setattr(NovaTextArea, "save_io", SaveIo(write=broken))
    path = _file(tmp_path)
    area = NovaTextArea.open(path)
    host = SaveHost(area)
    async with host.run_test() as pilot:
        await pilot.pause()
        area.focus()
        await pilot.press("x")
        before = text_of(area)
        assert area.save() is True
        await wait_saved(pilot, area)
        (failed,) = host.terminals()
        assert isinstance(failed, NovaTextArea.SaveFailed)
        assert failed.stage == "write"
        assert failed.path == path
        assert failed.error.errno == 28
        assert area.modified
        assert text_of(area) == before
        assert path.read_text() == BODY
        await pilot.press("y")  # unlocked
        assert host.refused == []
        assert len(text_of(area)) == len(before) + 1
    assert leftovers(tmp_path) == []


@pytest.mark.asyncio
async def test_cancel_posts_one_save_cancelled(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    gate = Gate()
    monkeypatch.setattr(NovaTextArea, "save_io", gate.io())
    path = _file(tmp_path)
    area = NovaTextArea.open(path)
    host = SaveHost(area)
    async with host.run_test() as pilot:
        await pilot.pause()
        area.focus()
        await pilot.press("x")
        assert area.save() is True
        assert gate.reached.wait(10)
        area.cancel_save()
        gate.release()
        await wait_saved(pilot, area)
        (done,) = host.terminals()
        assert isinstance(done, NovaTextArea.SaveCancelled)
        assert area.modified
        assert path.read_text() == BODY
        await pilot.press("y")
        assert host.refused == []
    assert leftovers(tmp_path) == []


@pytest.mark.asyncio
async def test_closing_mid_save_cancels_and_joins_before_the_source_closes(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    gate = Gate()
    monkeypatch.setattr(NovaTextArea, "save_io", gate.io())
    path = _file(tmp_path)
    area = NovaTextArea.open(path)
    host = SaveHost(area)
    async with host.run_test() as pilot:
        await pilot.pause()
        area.focus()
        await pilot.press("x")
        document = area.document
        assert area.save() is True
        assert gate.reached.wait(10)
        area.close()
        assert not document.wait_closed(0.2), "the source must stay open while the writer runs"
        gate.release()
        assert document.wait_closed(10)
        assert not [thread for thread in threading.enumerate() if thread.name == "nova-save" and thread.is_alive()]
        await pilot.pause(0.1)
        assert len(host.terminals()) == 1
        assert not area.saving
        assert path.read_text() == BODY
    assert leftovers(tmp_path) == []


@pytest.mark.asyncio
async def test_edits_are_refused_during_a_save_but_the_cursor_moves(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    gate = Gate()
    monkeypatch.setattr(NovaTextArea, "save_io", gate.io())
    path = _file(tmp_path)
    area = NovaTextArea.open(path)
    host = SaveHost(area)
    async with host.run_test() as pilot:
        await pilot.pause()
        area.focus()
        await pilot.press("x")
        before = text_of(area)
        assert area.save() is True
        assert gate.reached.wait(10)
        await pilot.press("q")
        await wait_until(pilot, lambda: host.refused == ["saving"])
        await pilot.press("down")
        assert area.cursor_location[0] == 1
        assert text_of(area) == before
        gate.release()
        await wait_saved(pilot, area)
        assert text_of(area) == before
        assert not area.modified


class FakeClock:
    """A clock that moves `step` seconds at every reading, so the throttle of the progress messages is a function of the number of readings."""

    def __init__(self, step: float) -> None:
        self.now = 1000.0
        self.step = step

    def __call__(self) -> float:
        self.now += self.step
        return self.now


@pytest.mark.asyncio
async def test_progress_messages_are_throttled_to_ten_per_second_by_the_clock(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    path = _file(tmp_path)
    area = NovaTextArea.open(path)
    clock = FakeClock(0.03)
    emitted: list[tuple[float, int]] = []
    host = SaveHost(area)
    async with host.run_test() as pilot:
        await pilot.pause()
        monkeypatch.setattr(area, "save_clock", clock)
        real_post = area.post_message

        def recording(message: Message) -> bool:
            if isinstance(message, NovaTextArea.SaveProgress):
                emitted.append((clock.now, message.done))
            return real_post(message)

        monkeypatch.setattr(area, "post_message", recording)
        run = _SaveRun(path, [])
        area._save_run = run
        try:
            for step in range(40):  # 40 announcements spread over 1.2 seconds of the fake clock
                run.progress = CoreSaveProgress("writing", step * 10, 400)
                area._announce_save_progress(run)
        finally:
            area._save_run = None
        times = [when for when, _ in emitted]
        done = [value for _, value in emitted]
        assert 9 <= len(emitted) <= 11, emitted  # a vacuous pass is impossible: 40 readings need about 10 messages
        assert all(later - earlier >= 0.1 - 1e-9 for earlier, later in pairwise(times)), times
        assert done == sorted(done)


@pytest.mark.asyncio
async def test_progress_of_a_real_save_never_decreases_and_ends_once(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(NovaTextArea, "save_settings", SaveSettings(chunk=64, fsync_every=64))
    path = tmp_path / "big.txt"
    path.write_text(BODY * 80)
    area = NovaTextArea.open(path)
    host = SaveHost(area)
    async with host.run_test() as pilot:
        await pilot.pause()
        area.focus()
        await pilot.press("x")
        assert area.save() is True
        await wait_saved(pilot, area)
        done = [message.done for message in host.progress()]
        assert done == sorted(done)
        assert len(host.terminals()) == 1


@pytest.mark.asyncio
async def test_load_text_is_refused_while_saving(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    gate = Gate()
    monkeypatch.setattr(NovaTextArea, "save_io", gate.io())
    path = _file(tmp_path)
    area = NovaTextArea.open(path)
    host = SaveHost(area)
    async with host.run_test() as pilot:
        await pilot.pause()
        area.focus()
        await pilot.press("x")
        assert area.save() is True
        assert gate.reached.wait(10)
        with pytest.raises(RuntimeError, match="save"):
            area.load_text("other")
        gate.release()
        await wait_saved(pilot, area)
        area.load_text("other")
        assert text_of(area) == b"other"


@pytest.mark.asyncio
async def test_a_changed_file_needs_confirmation_and_overwrite_writes(tmp_path: Path) -> None:
    path = _file(tmp_path)
    area = NovaTextArea.open(path)
    host = SaveHost(area)
    async with host.run_test() as pilot:
        await pilot.pause()
        area.focus()
        await pilot.press("x")
        path.write_text(BODY + "appended\n")
        assert area.save() is False
        await pilot.pause(0.1)
        (message,) = host.saves
        assert isinstance(message[1], NovaTextArea.SaveNeedsConfirmation)
        assert message[1].kind.value == "modified"
        assert not area.saving
        assert area.save(overwrite=True) is True
        await wait_saved(pilot, area)
        assert path.read_bytes() == text_of(area)
        assert len(host.terminals()) == 1


@pytest.mark.asyncio
async def test_save_as_onto_an_existing_file_needs_confirmation(tmp_path: Path) -> None:
    path = _file(tmp_path)
    other = _file(tmp_path, "other.txt")
    area = NovaTextArea.open(path)
    host = SaveHost(area)
    async with host.run_test() as pilot:
        await pilot.pause()
        assert area.save(other) is False
        await pilot.pause(0.1)
        (message,) = host.saves
        assert isinstance(message[1], NovaTextArea.SaveNeedsConfirmation)
        assert message[1].kind.value == "exists"
        assert area.save(other, overwrite=True) is True
        await wait_saved(pilot, area)


async def _failing_finish(tmp_path: Path, monkeypatch: pytest.MonkeyPatch, owner: type, name: str, error: Exception, *, modified: bool = True) -> tuple[NovaTextArea.SaveFailed, bytes, bool]:
    """Save an edited file while `owner.name` raises `error`; return the failure, the text before the save and whether the area took more edits."""

    def broken(*_args: object, **_kwargs: object) -> None:
        raise error

    monkeypatch.setattr(owner, name, broken)
    path = _file(tmp_path)
    area = NovaTextArea.open(path)
    host = SaveHost(area)
    async with host.run_test() as pilot:
        await pilot.pause()
        area.focus()
        await pilot.press("x")
        before = text_of(area)
        assert area.save() is True
        await wait_saved(pilot, area)
        (failed,) = host.terminals()
        assert isinstance(failed, NovaTextArea.SaveFailed)
        assert text_of(area) == before
        assert area.modified is modified
        monkeypatch.undo()
        await pilot.press("y")
        assert host.refused == []
        return failed, before, len(text_of(area)) == len(before) + 1


@pytest.mark.asyncio
@pytest.mark.parametrize("error", [OSError(5, "boom"), ValueError("bad"), RuntimeError("defect")])
async def test_any_exception_in_apply_rebase_ends_in_one_save_failed(tmp_path: Path, monkeypatch: pytest.MonkeyPatch, error: Exception) -> None:
    # the real apply_rebase fails after it built the new table (in `_install`, before the swap): it must close the saved file itself and leave the old table in place
    failed, _, editable = await _failing_finish(tmp_path, monkeypatch, LazyDocument, "_rebase_long", error)
    assert failed.stage == "internal"
    assert failed.committed is True
    assert editable
    assert leftovers(tmp_path) == []
    assert open_files(tmp_path) == []


@pytest.mark.asyncio
async def test_a_failure_while_swapping_leaves_the_document_on_the_old_table(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    original = LazyDocument._rebase_long

    def broken(*_args: object) -> object:
        raise OSError(5, "mid-swap")

    monkeypatch.setattr(LazyDocument, "_rebase_long", broken)
    path = _file(tmp_path)
    area = NovaTextArea.open(path)
    host = SaveHost(area)
    async with host.run_test() as pilot:
        await pilot.pause()
        area.focus()
        await pilot.press("x")
        before = text_of(area)
        table = area.document._table
        assert area.save() is True
        await wait_saved(pilot, area)
        (failed,) = host.terminals()
        assert isinstance(failed, NovaTextArea.SaveFailed)
        assert failed.committed is True
        assert failed.stage == "internal"
        assert area.document._table is table
        assert text_of(area) == before
        monkeypatch.setattr(LazyDocument, "_rebase_long", original)
        await pilot.press("y")
        assert host.refused == []


@pytest.mark.asyncio
async def test_a_failure_after_the_swap_clears_the_history_and_still_ends_once(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    failed, _, editable = await _failing_finish(tmp_path, monkeypatch, NovaTextArea, "_apply_translated", RuntimeError("defect"), modified=False)
    assert failed.stage == "internal"
    assert failed.committed is True
    assert editable


@pytest.mark.asyncio
async def test_a_failure_before_the_commit_is_not_committed(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    def broken(_fd: int, _data: bytes | memoryview) -> int:
        raise OSError(28, "No space left on device")

    monkeypatch.setattr(NovaTextArea, "save_io", SaveIo(write=broken))
    path = _file(tmp_path)
    area = NovaTextArea.open(path)
    host = SaveHost(area)
    async with host.run_test() as pilot:
        await pilot.pause()
        area.focus()
        await pilot.press("x")
        assert area.save() is True
        await wait_saved(pilot, area)
        (failed,) = host.terminals()
        assert isinstance(failed, NovaTextArea.SaveFailed)
        assert failed.committed is False


@pytest.mark.asyncio
async def test_a_failed_translation_on_the_save_thread_is_committed(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    failed, _, editable = await _failing_finish(tmp_path, monkeypatch, LazyDocument, "prepare_rebase", OSError(5, "translate"))
    assert failed.stage == "internal"
    assert failed.committed is True
    assert editable


@pytest.mark.asyncio
async def test_closing_after_the_replace_does_not_report_a_cancel(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    reached, opened = threading.Event(), threading.Event()
    real = SaveIo()

    def held_dir_sync(directory: str) -> None:
        reached.set()  # the replace has happened: the commit point is behind the job
        assert opened.wait(10)
        real.fsync_dir(directory)

    monkeypatch.setattr(NovaTextArea, "save_io", SaveIo(fsync_dir=held_dir_sync))
    path = _file(tmp_path)
    area = NovaTextArea.open(path)
    host = SaveHost(area)
    async with host.run_test() as pilot:
        await pilot.pause()
        area.focus()
        await pilot.press("x")
        document = area.document
        assert area.save() is True
        assert reached.wait(10)
        area.close()
        opened.set()
        assert document.wait_closed(10)
        await pilot.pause(0.1)
        (failed,) = host.terminals()
        assert isinstance(failed, NovaTextArea.SaveFailed)
        assert failed.committed is True
        assert not area.saving
        assert path.read_bytes().startswith(b"x")
    assert leftovers(tmp_path) == []


@pytest.mark.asyncio
async def test_closing_survives_a_refused_post(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    gate = Gate()
    monkeypatch.setattr(NovaTextArea, "save_io", gate.io())
    path = _file(tmp_path)
    area = NovaTextArea.open(path)
    host = SaveHost(area)
    async with host.run_test() as pilot:
        await pilot.pause()
        area.focus()
        await pilot.press("x")
        document = area.document
        assert area.save() is True
        assert gate.reached.wait(10)
        real_post = area.post_message
        refused: list[object] = []

        def post(message: Message) -> bool:
            if isinstance(message, NovaTextArea.SaveCancelled):
                refused.append(message)
                return False
            return real_post(message)

        monkeypatch.setattr(area, "post_message", post)
        area.close()
        assert len(refused) == 1
        gate.release()
        assert document.wait_closed(10)
        assert not area.saving


@dataclass
class _Outcome:
    area: NovaTextArea
    host: SaveHost
    pilot: Pilot[None]
    target: Path
    before: bytes
    """The text before the edit that was saved."""


@asynccontextmanager
async def _failed_save_as(tmp_path: Path, monkeypatch: pytest.MonkeyPatch, patch: Callable[[pytest.MonkeyPatch], None]) -> AsyncIterator[_Outcome]:
    """Edit, apply `patch`, save as another file, undo the patch and wait for the end of the save; check what every outcome shares."""
    path = _file(tmp_path)
    target = tmp_path / "saved.txt"
    area = NovaTextArea.open(path)
    host = SaveHost(area)
    async with host.run_test() as pilot:
        await pilot.pause()
        area.focus()
        before = text_of(area)
        await pilot.press("x")
        patch(monkeypatch)
        assert area.save(target) is True
        await wait_saved(pilot, area)
        monkeypatch.undo()
        assert not area.saving
        assert len(host.terminals()) == 1  # one terminal message
        await pilot.press("y")
        assert host.refused == []  # the lock is lifted
        await pilot.press("ctrl+z")
        yield _Outcome(area, host, pilot, target, before)
    assert area.document.wait_closed(10.0)
    assert open_files(tmp_path) == []  # no leaked descriptor


@pytest.mark.asyncio
async def test_a_failure_after_the_install_adopts_the_file_and_clears_the_history(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    def patch(patcher: pytest.MonkeyPatch) -> None:
        real = LazyDocument.apply_rebase

        def failing(self: LazyDocument, plan: RebasePlan) -> None:
            real(self, plan)
            msg = "after the swap"
            raise RuntimeError(msg)

        patcher.setattr(LazyDocument, "apply_rebase", failing)

    async with _failed_save_as(tmp_path, monkeypatch, patch) as out:
        (failed,) = out.host.terminals()
        assert isinstance(failed, NovaTextArea.SaveFailed)
        assert failed.committed is True
        assert out.area.file_path == out.target
        source = out.area.document._source
        assert isinstance(source, PreadSource)
        assert out.area._held_identity == source.identity()
        await out.pilot.press("ctrl+z")  # the history is gone: nothing may undo into the old pieces
        assert text_of(out.area) == out.target.read_bytes()
        assert not out.area.history.undo_stack


@pytest.mark.asyncio
async def test_an_identity_that_raises_after_a_failed_rebase_still_clears_the_history(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    def patch(patcher: pytest.MonkeyPatch) -> None:
        real = LazyDocument.apply_rebase
        swapped: list[bool] = []

        def failing(self: LazyDocument, plan: RebasePlan) -> None:
            real(self, plan)
            swapped.append(True)
            msg = "after the swap"
            raise RuntimeError(msg)

        real_identity = PreadSource.identity

        def identity(self: PreadSource) -> FileIdentity:
            if swapped:
                msg = "identity"
                raise OSError(msg)
            return real_identity(self)

        patcher.setattr(LazyDocument, "apply_rebase", failing)
        patcher.setattr(PreadSource, "identity", identity)

    async with _failed_save_as(tmp_path, monkeypatch, patch) as out:
        (failed,) = out.host.terminals()
        assert isinstance(failed, NovaTextArea.SaveFailed)
        assert out.area.file_path == out.target
        assert not out.area.history.undo_stack
        assert text_of(out.area) == out.target.read_bytes()


@pytest.mark.asyncio
async def test_a_failure_before_the_install_keeps_the_path_and_the_history(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    def patch(patcher: pytest.MonkeyPatch) -> None:
        def broken(*_args: object, **_kwargs: object) -> None:
            raise OSError(5, "before the swap")

        patcher.setattr(LazyDocument, "_build_table", broken)

    async with _failed_save_as(tmp_path, monkeypatch, patch) as out:
        (failed,) = out.host.terminals()
        assert isinstance(failed, NovaTextArea.SaveFailed)
        assert out.area.file_path == tmp_path / "doc.txt"
        await out.pilot.press("ctrl+z")
        assert text_of(out.area) == out.before  # the old table and the history still work


@pytest.mark.asyncio
@pytest.mark.parametrize("step", ["line_subscribe", "closer_start"])
async def test_a_failing_tail_step_still_ends_in_saved_with_a_working_undo(tmp_path: Path, monkeypatch: pytest.MonkeyPatch, step: str) -> None:
    real_start = threading.Thread.start

    def patch(patcher: pytest.MonkeyPatch) -> None:
        def broken(*_args: object, **_kwargs: object) -> None:
            raise RuntimeError(step)

        def start(self: threading.Thread) -> None:
            if self.name == "lazy-rebase-closer":
                raise RuntimeError(step)
            real_start(self)

        if step == "line_subscribe":
            patcher.setattr(LineIndex, "subscribe", broken)
        else:
            patcher.setattr(threading.Thread, "start", start)

    async with _failed_save_as(tmp_path, monkeypatch, patch) as out:
        (done,) = out.host.terminals()
        assert isinstance(done, NovaTextArea.Saved)
        assert out.area.file_path == out.target
        assert out.area.document.rebase_problems
        assert text_of(out.area) == out.target.read_bytes()  # the undo of "y"
        await out.pilot.press("ctrl+z")
        assert text_of(out.area) == out.before  # and of "x": the translated history still reads the right bytes
