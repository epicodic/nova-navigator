"""Pilot tests of the file changing under a displayed widget (ACT5 task 15): detection, the stale-view policy, save after a change and `reload`.

Hermetic: every file is a temp file of about 2 MiB read through a `PreadSource`; no reference file is touched.
"""

from __future__ import annotations

import os
import threading
from collections.abc import Callable
from pathlib import Path

import pytest
from textual.pilot import Pilot

from nova_editor.core import ChangeKind, PreadSource, SourceChanged
from nova_editor.core.save import SaveIo, SaveSettings
from nova_editor.widget import ExternalCheck, NovaTextArea
from nova_editor.widget import _text_area as widget_module
from tests.nova_editor.helpers_view import await_first_layout, row_strip_text, wait_until
from tests.nova_editor.save_widget_helpers import Gate, SaveHost, leftovers, text_of, wait_saved

SETTINGS = SaveSettings(chunk=4093, fsync_every=1 << 20)
"""Chunks of an odd size (they end inside rows and sequences) but large enough for a 2 MiB document to be saved in a few hundred writes."""
ROWS = 140_000
BODY = b"".join(b"row %06d text\n" % n for n in range(ROWS))
FAR_ROW = ROWS - 100
STALE = "file changed on disk"
TRUNCATION = "the file was truncated; the unchanged parts cannot be read"
EXPECTED = b"x" + BODY
"""The document after typing one `x` at the start of the file."""


def _file(tmp_path: Path, name: str = "doc.txt") -> Path:
    path = tmp_path / name
    path.write_bytes(BODY)
    assert path.stat().st_size > 2_000_000
    return path


def _truncate(path: Path) -> None:
    os.truncate(path, 0)


def _shrink(path: Path) -> None:
    os.truncate(path, 1_000_000)


def _append(path: Path) -> None:
    with path.open("ab") as handle:
        handle.write(b"appended\n")


def _touch(path: Path) -> None:
    info = path.stat()
    os.utime(path, ns=(info.st_atime_ns, info.st_mtime_ns + 5_000_000_000))


def _replace(path: Path) -> None:
    other = path.with_name("other.tmp")
    other.write_bytes(b"something else entirely\n")
    os.replace(other, path)


def _delete(path: Path) -> None:
    path.unlink()


CHANGES: list[tuple[str, Callable[[Path], None], ChangeKind]] = [
    ("truncate", _truncate, ChangeKind.TRUNCATED),
    ("shrink", _shrink, ChangeKind.TRUNCATED),
    ("append", _append, ChangeKind.MODIFIED),
    ("touch", _touch, ChangeKind.MODIFIED),
    ("replace", _replace, ChangeKind.REPLACED),
    ("delete", _delete, ChangeKind.DELETED),
]
IN_PLACE = [case for case in CHANGES if case[2] in {ChangeKind.TRUNCATED, ChangeKind.MODIFIED}]
TRUNCATIONS = [case for case in CHANGES if case[2] is ChangeKind.TRUNCATED]
READABLE = [case for case in CHANGES if case[0] in {"append", "touch", "replace", "delete"}]
IDS = [case[0] for case in CHANGES]


class ChangeHost(SaveHost):
    """Host that also records the `SourceChanged` kinds, the `Reloaded` messages and the reload failures."""

    def __init__(self, widget: NovaTextArea) -> None:
        super().__init__(widget)
        self.kinds: list[ChangeKind] = []
        self.reloaded = 0
        self.reload_failures: list[NovaTextArea.ReloadFailed] = []

    def on_nova_text_area_source_changed(self, message: NovaTextArea.SourceChanged) -> None:
        self.kinds.append(message.kind)

    def on_nova_text_area_reloaded(self, message: NovaTextArea.Reloaded) -> None:
        del message
        self.reloaded += 1

    def on_nova_text_area_reload_failed(self, message: NovaTextArea.ReloadFailed) -> None:
        self.reload_failures.append(message)

    def confirmations(self) -> list[tuple[ChangeKind, Path]]:
        return [(message.kind, message.path) for _, message in self.saves if isinstance(message, NovaTextArea.SaveNeedsConfirmation)]


@pytest.fixture(autouse=True)
def small_chunks(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(NovaTextArea, "save_settings", SETTINGS)


async def _opened(pilot: Pilot[None], area: NovaTextArea) -> None:
    await await_first_layout(pilot, area)
    assert area.document.wait_indexed(30.0)
    await pilot.pause(0.05)


@pytest.mark.parametrize(("change", "kind"), [(case[1], case[2]) for case in CHANGES], ids=IDS)
@pytest.mark.asyncio
async def test_check_external_change_detects_each_kind_without_a_read(tmp_path: Path, monkeypatch: pytest.MonkeyPatch, change: Callable[[Path], None], kind: ChangeKind) -> None:
    path = _file(tmp_path)
    area = NovaTextArea.open(path)
    host = ChangeHost(area)
    async with host.run_test() as pilot:
        await _opened(pilot, area)
        assert area.check_external_change() is ChangeKind.UNCHANGED
        reads: list[int] = []
        real = PreadSource.read

        def counting(self: PreadSource, offset: int, size: int, *, cache: bool = True) -> bytes:
            reads.append(offset)
            return real(self, offset, size, cache=cache)

        monkeypatch.setattr(PreadSource, "read", counting)
        change(path)
        assert area.check_external_change() is kind
        assert area.check_external_change() is kind
        await pilot.pause(0.1)
        assert reads == []
        assert host.kinds == [kind]
        assert len(host.source_changed) == 1


@pytest.mark.parametrize(("change", "kind"), [(case[1], case[2]) for case in CHANGES], ids=IDS)
@pytest.mark.asyncio
async def test_a_changed_file_keeps_edits_history_and_cached_rows(tmp_path: Path, change: Callable[[Path], None], kind: ChangeKind) -> None:
    path = _file(tmp_path)
    area = NovaTextArea.open(path)
    host = ChangeHost(area)
    async with host.run_test() as pilot:
        await _opened(pilot, area)
        area.focus()
        await pilot.press("x")
        area.history.checkpoint()
        await pilot.press("y")
        area.undo()
        assert area.modified
        assert len(area.history.undo_stack) == 1
        assert len(area.history.redo_stack) == 1
        assert row_strip_text(area, 1).startswith("row 000001")
        change(path)
        assert area.check_external_change() is kind
        await pilot.pause(0.1)
        assert host.kinds == [kind]
        assert row_strip_text(area, 1).startswith("row 000001")  # a cached row still renders
        area.scroll_to(y=FAR_ROW, animate=False)
        await pilot.pause(0.1)
        assert row_strip_text(area, FAR_ROW).strip() == ""  # an uncached row is blank
        assert area.modified
        before = list(host.refused)
        await pilot.press("z")
        area.undo()
        area.redo()
        await pilot.pause(0.1)
        assert host.refused == [*before, STALE, STALE, STALE]
        assert area.modified
        assert len(area.history.undo_stack) == 1
        assert len(area.history.redo_stack) == 1
        await pilot.press("shift+right", "left", "pagedown", "pageup", "ctrl+end", "ctrl+home")
        area.scroll_to(y=3, animate=False)
        await pilot.pause(0.1)
        assert host.kinds == [kind]


@pytest.mark.parametrize(("change", "kind"), [(case[1], case[2]) for case in IN_PLACE], ids=[case[0] for case in IN_PLACE])
@pytest.mark.asyncio
async def test_a_read_of_uncached_content_detects_an_in_place_change(tmp_path: Path, change: Callable[[Path], None], kind: ChangeKind) -> None:
    path = _file(tmp_path)
    area = NovaTextArea.open(path)
    host = ChangeHost(area)
    async with host.run_test() as pilot:
        await _opened(pilot, area)
        change(path)
        area.scroll_to(y=FAR_ROW, animate=False)
        await wait_until(pilot, lambda: bool(host.kinds))
        await pilot.pause(0.1)
        assert host.kinds == [kind]
        assert len(host.source_changed) == 1
        assert area.check_external_change() is kind
        await pilot.pause(0.05)
        assert host.kinds == [kind]


@pytest.mark.parametrize(("change", "kind"), [(case[1], case[2]) for case in CHANGES], ids=IDS)
@pytest.mark.asyncio
async def test_save_after_a_change_asks_for_confirmation_and_starts_nothing(tmp_path: Path, change: Callable[[Path], None], kind: ChangeKind) -> None:
    path = _file(tmp_path)
    area = NovaTextArea.open(path)
    host = ChangeHost(area)
    async with host.run_test() as pilot:
        await _opened(pilot, area)
        area.focus()
        await pilot.press("x")
        change(path)
        assert area.save() is False
        await pilot.pause(0.1)
        assert not area.saving
        assert host.confirmations() == [(kind, path)]
        assert host.terminals() == []
        assert leftovers(tmp_path) == []
        assert host.kinds == [kind]


@pytest.mark.parametrize("change", [case[1] for case in READABLE], ids=[case[0] for case in READABLE])
@pytest.mark.asyncio
async def test_overwrite_after_a_change_writes_the_document(tmp_path: Path, change: Callable[[Path], None]) -> None:
    path = _file(tmp_path)
    area = NovaTextArea.open(path)
    host = ChangeHost(area)
    async with host.run_test() as pilot:
        await _opened(pilot, area)
        area.focus()
        await pilot.press("x")
        change(path)
        assert area.save(overwrite=True) is True
        await wait_saved(pilot, area)
        assert [type(message) for message in host.terminals()] == [NovaTextArea.Saved]
        assert path.read_bytes() == EXPECTED
        assert text_of(area) == EXPECTED
        assert not area.modified
        assert leftovers(tmp_path) == []
        # the stale state ended with the successful save: edits work again and the rows render
        refused = len(host.refused)
        await pilot.press("y")
        assert len(host.refused) == refused
        assert area.modified
        assert row_strip_text(area, 0).startswith("xyrow 000000")


@pytest.mark.parametrize("change", [case[1] for case in TRUNCATIONS], ids=[case[0] for case in TRUNCATIONS])
@pytest.mark.asyncio
async def test_overwrite_after_a_truncation_that_removed_needed_bytes_fails(tmp_path: Path, change: Callable[[Path], None]) -> None:
    path = _file(tmp_path)
    area = NovaTextArea.open(path)
    host = ChangeHost(area)
    async with host.run_test() as pilot:
        await _opened(pilot, area)
        area.focus()
        await pilot.press("x")
        change(path)
        size = path.stat().st_size
        assert area.save(overwrite=True) is True
        await wait_saved(pilot, area)
        (failure,) = host.terminals()
        assert isinstance(failure, NovaTextArea.SaveFailed)
        assert failure.stage == "write"
        assert str(failure.error) == TRUNCATION
        assert path.stat().st_size == size  # the target is untouched
        assert leftovers(tmp_path) == []
        assert area.modified
        # the stale lock survived the failed save
        before = len(host.refused)
        await pilot.press("y")
        assert host.refused[before:] == [STALE]


@pytest.mark.asyncio
async def test_the_saving_lock_does_not_replace_the_stale_lock(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    path = _file(tmp_path)
    gate = Gate()
    monkeypatch.setattr(NovaTextArea, "save_io", gate.io())
    area = NovaTextArea.open(path)
    host = ChangeHost(area)
    async with host.run_test() as pilot:
        await _opened(pilot, area)
        area.focus()
        await pilot.press("x")
        _append(path)
        assert area.check_external_change() is ChangeKind.MODIFIED
        assert area.save(overwrite=True) is True
        assert gate.reached.wait(10.0)
        await pilot.press("y")
        assert host.refused[-1] == "saving"
        gate.release()
        await wait_saved(pilot, area)
        assert [type(message) for message in host.terminals()] == [NovaTextArea.Saved]
        refused = len(host.refused)
        await pilot.press("y")
        assert len(host.refused) == refused  # the successful save lifted both reasons


@pytest.mark.asyncio
async def test_a_failed_overwrite_save_leaves_only_the_stale_lock(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    path = _file(tmp_path)
    gate = Gate()
    monkeypatch.setattr(NovaTextArea, "save_io", gate.io())
    area = NovaTextArea.open(path)
    host = ChangeHost(area)
    async with host.run_test() as pilot:
        await _opened(pilot, area)
        area.focus()
        await pilot.press("x")
        _shrink(path)
        assert area.save(overwrite=True) is True
        assert gate.reached.wait(10.0)
        await pilot.press("y")
        assert host.refused[-1] == "saving"
        gate.release()
        await wait_saved(pilot, area)
        assert isinstance(host.terminals()[0], NovaTextArea.SaveFailed)
        await pilot.press("y")
        assert host.refused[-1] == STALE


@pytest.mark.parametrize("change", [case[1] for case in READABLE], ids=[case[0] for case in READABLE])
@pytest.mark.asyncio
async def test_save_as_after_a_change_writes_without_confirmation(tmp_path: Path, change: Callable[[Path], None]) -> None:
    path = _file(tmp_path)
    target = tmp_path / "copy.txt"
    area = NovaTextArea.open(path)
    host = ChangeHost(area)
    async with host.run_test() as pilot:
        await _opened(pilot, area)
        area.focus()
        await pilot.press("x")
        change(path)
        assert area.save(target) is True
        await wait_saved(pilot, area)
        assert [type(message) for message in host.terminals()] == [NovaTextArea.Saved]
        assert host.confirmations() == []
        assert target.read_bytes() == EXPECTED
        assert area.file_path == target
        assert not area.modified


@pytest.mark.parametrize("change", [case[1] for case in TRUNCATIONS], ids=[case[0] for case in TRUNCATIONS])
@pytest.mark.asyncio
async def test_save_as_after_a_truncation_fails_without_writing(tmp_path: Path, change: Callable[[Path], None]) -> None:
    path = _file(tmp_path)
    target = tmp_path / "copy.txt"
    area = NovaTextArea.open(path)
    host = ChangeHost(area)
    async with host.run_test() as pilot:
        await _opened(pilot, area)
        area.focus()
        await pilot.press("x")
        change(path)
        assert area.save(target) is True
        await wait_saved(pilot, area)
        (failure,) = host.terminals()
        assert isinstance(failure, NovaTextArea.SaveFailed)
        assert (failure.stage, str(failure.error)) == ("write", TRUNCATION)
        assert not target.exists()
        assert leftovers(tmp_path) == []


@pytest.mark.asyncio
async def test_save_as_onto_an_existing_file_after_a_change_needs_confirmation(tmp_path: Path) -> None:
    path = _file(tmp_path)
    target = tmp_path / "taken.txt"
    target.write_bytes(b"taken")
    area = NovaTextArea.open(path)
    host = ChangeHost(area)
    async with host.run_test() as pilot:
        await _opened(pilot, area)
        area.focus()
        await pilot.press("x")
        _append(path)
        assert area.save(target) is False
        await pilot.pause(0.1)
        assert host.confirmations() == [(ChangeKind.EXISTS, target)]
        assert target.read_bytes() == b"taken"


@pytest.mark.asyncio
async def test_reload_discards_edits_and_clears_the_stale_state(tmp_path: Path) -> None:
    path = _file(tmp_path)
    area = NovaTextArea.open(path)
    host = ChangeHost(area)
    async with host.run_test() as pilot:
        await _opened(pilot, area)
        area.focus()
        await pilot.press("x")
        area.move_cursor((5, 2))
        _append(path)
        assert area.check_external_change() is ChangeKind.MODIFIED
        assert area.reload() is True
        await pilot.pause(0.1)
        await wait_until(pilot, lambda: area.cursor_location == (5, 0))
        assert host.reloaded == 1
        assert not area.modified
        assert not area.history.undo_stack
        assert not area.history.redo_stack
        assert area.document.length == len(BODY) + len(b"appended\n")
        assert area.check_external_change() is ChangeKind.UNCHANGED
        refused = len(host.refused)
        await pilot.press("y")
        assert len(host.refused) == refused
        assert area.modified
        assert row_strip_text(area, 5).startswith("yrow 000005")
        assert host.kinds == [ChangeKind.MODIFIED]


@pytest.mark.asyncio
async def test_reload_moves_the_cursor_to_the_last_row_when_its_row_is_gone(tmp_path: Path) -> None:
    path = _file(tmp_path)
    area = NovaTextArea.open(path)
    host = ChangeHost(area)
    async with host.run_test() as pilot:
        await _opened(pilot, area)
        area.move_cursor((FAR_ROW, 0))
        await pilot.pause(0.1)
        path.write_bytes(b"a\nb\nc")
        assert area.reload() is True
        await pilot.pause(0.1)
        assert area.cursor_location == (2, 0)
        assert host.reloaded == 1


@pytest.mark.asyncio
async def test_reload_is_refused_while_saving(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    path = _file(tmp_path)
    gate = Gate()
    monkeypatch.setattr(NovaTextArea, "save_io", gate.io())
    area = NovaTextArea.open(path)
    host = ChangeHost(area)
    async with host.run_test() as pilot:
        await _opened(pilot, area)
        area.focus()
        await pilot.press("x")
        assert area.save() is True
        assert gate.reached.wait(10.0)
        with pytest.raises(RuntimeError, match="reload"):
            area.reload()
        assert area.saving
        assert area.modified
        gate.release()
        await wait_saved(pilot, area)
        assert [type(message) for message in host.terminals()] == [NovaTextArea.Saved]
        assert host.reloaded == 0
        assert path.read_bytes() == EXPECTED


@pytest.mark.asyncio
async def test_reload_of_a_deleted_file_fails_and_keeps_the_stale_state(tmp_path: Path) -> None:
    path = _file(tmp_path)
    area = NovaTextArea.open(path)
    host = ChangeHost(area)
    async with host.run_test() as pilot:
        await _opened(pilot, area)
        area.focus()
        await pilot.press("x")
        _delete(path)
        assert area.check_external_change() is ChangeKind.DELETED
        assert area.reload() is False
        await pilot.pause(0.1)
        assert host.reloaded == 0
        assert [message.path for message in host.reload_failures] == [path]
        assert isinstance(host.reload_failures[0].error, OSError)
        assert area.modified
        before = len(host.refused)
        await pilot.press("y")
        assert host.refused[before:] == [STALE]
        assert row_strip_text(area, 1).startswith("row 000001")


@pytest.mark.asyncio
async def test_reload_without_a_path_does_nothing() -> None:
    area = NovaTextArea(text="abc")
    host = ChangeHost(area)
    async with host.run_test() as pilot:
        await pilot.pause()
        assert area.reload() is False
        await pilot.pause(0.05)
        assert host.reloaded == 0
        assert host.reload_failures == []


@pytest.mark.asyncio
async def test_load_text_resets_the_modified_state() -> None:
    area = NovaTextArea(text="abc")
    host = ChangeHost(area)
    async with host.run_test() as pilot:
        await pilot.pause()
        area.focus()
        await pilot.press("x")
        assert area.modified
        area.load_text("fresh")
        assert not area.modified


@pytest.mark.asyncio
async def test_truncating_a_displayed_file_to_zero_never_ends_the_process(tmp_path: Path) -> None:
    path = _file(tmp_path)
    area = NovaTextArea.open(path)
    host = ChangeHost(area)
    async with host.run_test() as pilot:
        await _opened(pilot, area)
        area.focus()
        await pilot.press("pagedown", "pagedown", "pagedown")
        _truncate(path)
        keys = ["down", "pagedown", "up", "pageup", "x", "ctrl+z", "right", "end", "home", "ctrl+end"]
        for number in range(50):
            await pilot.press(keys[number % len(keys)])
            if number % 7 == 0:
                area.scroll_to(y=(number * 2113) % ROWS, animate=False)
        await pilot.pause(0.2)
        assert area.check_external_change() is ChangeKind.TRUNCATED
        assert host.kinds == [ChangeKind.TRUNCATED]
        assert len(host.source_changed) == 1


def _track_sources(monkeypatch: pytest.MonkeyPatch) -> list[PreadSource]:
    """Record every source that `reload` opens (the one of `NovaTextArea.open` before this call is not included)."""
    opened: list[PreadSource] = []
    real = widget_module._open_source

    def tracking(path: Path) -> object:
        source = real(path)
        assert isinstance(source, PreadSource)
        opened.append(source)
        return source

    monkeypatch.setattr(widget_module, "_open_source", tracking)
    return opened


def _is_closed(source: PreadSource) -> bool:
    return source._closed


@pytest.mark.asyncio
async def test_reload_closes_the_new_source_when_the_document_cannot_be_built(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    path = _file(tmp_path)
    area = NovaTextArea.open(path)
    host = ChangeHost(area)
    async with host.run_test() as pilot:
        await _opened(pilot, area)
        area.focus()
        await pilot.press("x")
        opened = _track_sources(monkeypatch)

        def refuse(*_args: object, **_kwargs: object) -> None:
            raise ValueError("no document")

        monkeypatch.setattr(widget_module, "LazyDocument", refuse)
        assert area.reload() is False
        await pilot.pause(0.05)
        (source,) = opened
        assert _is_closed(source)
        assert len(host.reload_failures) == 1
        assert area.modified
        assert area.history.undo_stack


@pytest.mark.asyncio
async def test_reload_keeps_the_history_and_closes_the_new_document_when_the_swap_fails(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    path = _file(tmp_path)
    area = NovaTextArea.open(path)
    host = ChangeHost(area)
    async with host.run_test() as pilot:
        await _opened(pilot, area)
        area.focus()
        await pilot.press("x")
        old = area.document
        opened = _track_sources(monkeypatch)

        def refuse(*_args: object, **_kwargs: object) -> None:
            raise RuntimeError("swap failed")

        monkeypatch.setattr(widget_module, "LazyWrappedDocument", refuse)
        with pytest.raises(RuntimeError, match="swap failed"):
            area.reload()
        assert area.document is old
        assert area.modified
        assert area.history.undo_stack
        assert host.reloaded == 0
        (source,) = opened
        await wait_until(pilot, lambda: _is_closed(source))  # the document closes its source on a closer thread


@pytest.mark.asyncio
async def test_reload_closes_the_new_source_when_row_zero_cannot_be_read(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    path = _file(tmp_path)
    area = NovaTextArea.open(path)
    host = ChangeHost(area)
    async with host.run_test() as pilot:
        await _opened(pilot, area)
        opened = _track_sources(monkeypatch)

        def changed(*_args: object, **_kwargs: object) -> None:
            raise SourceChanged("changed while reloading", ChangeKind.MODIFIED)

        monkeypatch.setattr(widget_module.LazyDocument, "_range", changed)
        assert area.reload() is False
        monkeypatch.undo()
        await pilot.pause(0.05)
        (source,) = opened
        await wait_until(pilot, lambda: _is_closed(source))  # the document closes its source on a closer thread
        assert len(host.reload_failures) == 1


def _run_in_thread(check: ExternalCheck) -> ChangeKind:
    """Run the pure part of an external check on a thread of its own, as the poll of the app does."""
    found: list[ChangeKind] = []
    thread = threading.Thread(target=lambda: found.append(check.run()))
    thread.start()
    thread.join(10.0)
    assert found
    return found[0]


@pytest.mark.asyncio
async def test_the_pure_check_changes_no_widget_state_and_the_apply_does(tmp_path: Path) -> None:
    path = _file(tmp_path)
    area = NovaTextArea.open(path)
    host = ChangeHost(area)
    async with host.run_test() as pilot:
        await _opened(pilot, area)
        check = area.begin_external_check()
        assert check is not None
        _append(path)
        assert _run_in_thread(check) is ChangeKind.MODIFIED
        await pilot.pause(0.05)
        assert host.kinds == []
        assert area._stale_kind is None  # the thread did not touch the state
        assert area.apply_external_check(check, ChangeKind.MODIFIED) is ChangeKind.MODIFIED
        await pilot.pause(0.05)
        assert host.kinds == [ChangeKind.MODIFIED]
        assert area.begin_external_check() is None  # stale already: nothing left to find


@pytest.mark.asyncio
async def test_a_check_started_before_a_save_is_ignored_after_it(tmp_path: Path) -> None:
    path = _file(tmp_path)
    area = NovaTextArea.open(path)
    host = ChangeHost(area)
    async with host.run_test() as pilot:
        await _opened(pilot, area)
        area.focus()
        await pilot.press("x")
        check = area.begin_external_check()
        assert check is not None
        assert area.save() is True
        await wait_saved(pilot, area)
        assert path.read_bytes() == EXPECTED
        kind = _run_in_thread(check)  # its identity predates the replace: it sees the app's own file as replaced
        assert kind is ChangeKind.REPLACED
        assert area.apply_external_check(check, kind) is ChangeKind.UNCHANGED
        await pilot.pause(0.05)
        assert host.kinds == []
        assert area._stale_kind is None
        assert area.check_external_change() is ChangeKind.UNCHANGED


@pytest.mark.asyncio
async def test_a_check_between_the_replace_and_the_finish_reports_nothing(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    reached, opened = threading.Event(), threading.Event()
    real = SaveIo()

    def held_dir_sync(directory: str) -> None:
        reached.set()  # the replace happened; the UI thread has not finished the save
        assert opened.wait(10.0)
        real.fsync_dir(directory)

    monkeypatch.setattr(NovaTextArea, "save_io", SaveIo(fsync_dir=held_dir_sync))
    path = _file(tmp_path)
    area = NovaTextArea.open(path)
    host = ChangeHost(area)
    async with host.run_test() as pilot:
        await _opened(pilot, area)
        area.focus()
        await pilot.press("x")
        early = area.begin_external_check()
        assert early is not None
        assert area.save() is True
        assert reached.wait(10.0)
        assert area.begin_external_check() is None
        assert area.check_external_change() is ChangeKind.UNCHANGED
        assert area.apply_external_check(early, ChangeKind.REPLACED) is ChangeKind.UNCHANGED
        opened.set()
        await wait_saved(pilot, area)
        assert host.kinds == []
        assert [type(message) for message in host.terminals()] == [NovaTextArea.Saved]
        assert area.check_external_change() is ChangeKind.UNCHANGED
