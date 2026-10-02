"""Pilot tests of saving from `NovaTextArea` (ACT5 task 14): the API, the one-terminal-message invariant, the edit lock and closing mid-save."""

from __future__ import annotations

import threading
from pathlib import Path

import pytest

from nova_editor.core.save import SaveIo, SaveSettings
from nova_editor.widget import NovaTextArea
from tests.nova_editor.helpers_view import wait_until
from tests.nova_editor.save_widget_helpers import SETTINGS, Gate, SaveHost, leftovers, text_of, wait_saved

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


@pytest.mark.asyncio
async def test_progress_is_monotonic_and_at_most_ten_per_second(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
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
        times = [when for when, message in host.saves if isinstance(message, NovaTextArea.SaveProgress)]
        done = [message.done for message in host.progress()]
        assert done == sorted(done)
        assert all(times[index + 10] - times[index] >= 0.9 for index in range(len(times) - 10))
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
