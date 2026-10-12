"""Pilot tests of the in-place save from `NovaTextArea` (ACT3 design 4 to 6, ADR-3 rev 2); every file lives in `tmp_path`."""

from __future__ import annotations

import errno
import hashlib
import os
import re
import threading
from pathlib import Path

import pytest
from textual.pilot import Pilot

from nova_editor.core import ChangeKind, PreadSource
from nova_editor.core.save import SaveSettings
from nova_editor.document._document import Selection
from nova_editor.document._lazy_config import LazyConfig
from nova_editor.document._lazy_document import DocumentChange, LazyDocument
from nova_editor.widget import NovaTextArea
from tests.nova_editor.core.save_helpers import failing_io, recording_io
from tests.nova_editor.helpers_view import wait_until
from tests.nova_editor.save_widget_helpers import SETTINGS, Gate, SaveHost, leftovers, text_of, wait_saved
from tests.nova_editor.test_shared_history_widget import hex_edit

BODY = "".join(f"row {n} {'x' * (n % 9)}\n" for n in range(60))
LARGE = BODY.encode() * 1700
"""More than `SMALL_FILE_LIMIT`: such a file is opened through a `PreadSource`."""
TERMINATOR = re.compile(rb"\r\n|\n|\r")


@pytest.fixture(autouse=True)
def small_chunks(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(NovaTextArea, "save_settings", SETTINGS)


def _file(tmp_path: Path, data: bytes | None = None, name: str = "doc.txt") -> Path:
    path = tmp_path / name
    path.write_bytes(BODY.encode() if data is None else data)
    return path


def _inode(path: Path) -> int:
    return os.stat(path).st_ino


async def _overwrite(pilot: Pilot[None], area: NovaTextArea, row: int = 0) -> None:
    """Replace the first character of `row` by `x` (the same number of bytes)."""
    area.focus()
    area.move_cursor((row, 0))
    await pilot.press("shift+right", "x")


def _expected(data: bytes, *rows: int) -> bytes:
    """`data` with the first byte of each line in `rows` replaced by `x`."""
    lines = data.split(b"\n")
    for row in rows:
        lines[row] = b"x" + lines[row][1:]
    return b"\n".join(lines)


def _types(host: SaveHost) -> list[type]:
    return [type(message) for _, message in host.saves if not isinstance(message, NovaTextArea.SaveProgress)]


@pytest.mark.asyncio
@pytest.mark.parametrize("data", [BODY.encode(), LARGE], ids=["small", "large"])
async def test_overwrite_saves_in_place_and_clears_modified(tmp_path: Path, monkeypatch: pytest.MonkeyPatch, data: bytes) -> None:
    if len(data) > len(BODY):
        monkeypatch.setattr(NovaTextArea, "save_settings", SaveSettings(chunk=1 << 16, fsync_every=64))
    path = _file(tmp_path, data)
    inode = _inode(path)
    area = NovaTextArea.open(path)
    host = SaveHost(area)
    async with host.run_test() as pilot:
        await pilot.pause()
        changes: list[DocumentChange] = []
        area.document.add_change_listener(changes.append)
        await _overwrite(pilot, area)
        assert area.modified
        before = len(changes)
        assert area.save() is True
        await wait_saved(pilot, area)
        assert path.read_bytes() == _expected(data, 0)
        assert _inode(path) == inode
        assert leftovers(tmp_path) == []
        (message,) = host.terminals()
        assert isinstance(message, NovaTextArea.Saved)
        assert message.path == path
        assert not area.modified
        assert text_of(area) == _expected(data, 0)
        assert [change for change in changes[before:] if change.kind == "save"] == []
        # the reference point: a streaming save announces the rebase
        await pilot.press("q")
        assert area.save() is True
        await wait_saved(pilot, area)
        assert [change.kind for change in changes[before:] if change.kind == "save"] == ["save"]
        assert _inode(path) != inode


@pytest.mark.asyncio
async def test_save_from_a_hex_origin_entry_goes_in_place(tmp_path: Path) -> None:
    path = _file(tmp_path)
    inode = _inode(path)
    area = NovaTextArea.open(path)
    host = SaveHost(area)
    async with host.run_test() as pilot:
        await pilot.pause()
        hex_edit(area.document, 0, 3, b"ROW")
        assert area.modified
        assert area.save() is True
        await wait_saved(pilot, area)
        assert path.read_bytes() == b"ROW" + BODY.encode()[3:]
        assert _inode(path) == inode
        assert not area.modified
        assert [type(message) for message in host.terminals()] == [NovaTextArea.Saved]


@pytest.mark.asyncio
@pytest.mark.parametrize("target", ["own", "copy"])
async def test_save_as_streams(tmp_path: Path, target: str) -> None:
    path = _file(tmp_path)
    inode = _inode(path)
    destination = path if target == "own" else tmp_path / "copy.txt"
    area = NovaTextArea.open(path)
    host = SaveHost(area)
    async with host.run_test() as pilot:
        await pilot.pause()
        await _overwrite(pilot, area)
        assert area.save(destination) is True
        await wait_saved(pilot, area)
        assert destination.read_bytes() == _expected(BODY.encode(), 0)
        if target == "own":
            assert _inode(path) != inode
        else:
            assert _inode(path) == inode
            assert path.read_bytes() == BODY.encode()
        assert [type(message) for message in host.terminals()] == [NovaTextArea.Saved]


@pytest.mark.asyncio
async def test_insert_delete_restoring_length_streams(tmp_path: Path) -> None:
    path = _file(tmp_path)
    inode = _inode(path)
    area = NovaTextArea.open(path)
    host = SaveHost(area)
    async with host.run_test() as pilot:
        await pilot.pause()
        area.focus()
        await pilot.press("x")
        area.move_cursor((5, 0))
        await pilot.press("delete")
        assert area.document.length == len(BODY)
        assert area.save() is True
        await wait_saved(pilot, area)
        assert path.read_bytes() == text_of(area)
        assert _inode(path) != inode


@pytest.mark.asyncio
async def test_save_after_confirmed_external_change_streams(tmp_path: Path) -> None:
    path = _file(tmp_path)
    area = NovaTextArea.open(path)
    host = SaveHost(area)
    async with host.run_test() as pilot:
        await pilot.pause()
        await _overwrite(pilot, area)
        info = path.stat()
        os.utime(path, ns=(info.st_atime_ns, info.st_mtime_ns + 5_000_000_000))
        inode = _inode(path)
        assert area.save() is False
        await pilot.pause(0.1)
        assert [type(message) for message in host.terminals()] == []
        assert area.save(overwrite=True) is True
        await wait_saved(pilot, area)
        assert path.read_bytes() == text_of(area) == _expected(BODY.encode(), 0)
        assert _inode(path) != inode
        assert [type(message) for message in host.terminals()] == [NovaTextArea.Saved]


@pytest.mark.asyncio
@pytest.mark.parametrize("op", ["pwrite", "fsync"])
async def test_edits_undo_redo_paste_refused_while_patching(tmp_path: Path, monkeypatch: pytest.MonkeyPatch, op: str) -> None:
    gate = Gate()
    monkeypatch.setattr(NovaTextArea, "save_io", gate.inplace_io(op))
    path = _file(tmp_path)
    inode = _inode(path)
    area = NovaTextArea.open(path)
    host = SaveHost(area)
    async with host.run_test() as pilot:
        await pilot.pause()
        await _overwrite(pilot, area, 0)
        area.history.checkpoint()
        await _overwrite(pilot, area, 5)
        area.undo()  # the second overwrite goes to the redo stack
        assert len(area.history.redo_stack) == 1
        pilot.app.copy_to_clipboard("zz")
        before = text_of(area)
        undo_depth, redo_depth = len(area.history.undo_stack), len(area.history.redo_stack)
        assert area.save() is True
        assert gate.reached.wait(10)
        for keys in (["q"], ["ctrl+z"], ["ctrl+y"], ["ctrl+v"]):
            await pilot.press(*keys)
        await wait_until(pilot, lambda: len(host.refused) == 4)
        assert host.refused == ["saving"] * 4
        assert text_of(area) == before
        assert (len(area.history.undo_stack), len(area.history.redo_stack)) == (undo_depth, redo_depth)
        area.selection = Selection((0, 0), (0, 3))
        area.action_copy()
        assert area._clipboard_record is None  # a record made now would name the old bytes
        if op == "fsync":
            assert path.read_bytes() == _expected(BODY.encode(), 0)  # the bytes are written, the references are not redirected yet
        else:
            assert path.read_bytes() == BODY.encode()  # nothing is written yet
        gate.release()
        await wait_saved(pilot, area)
        assert path.read_bytes() == before == text_of(area)
        assert _inode(path) == inode
        assert not area.modified
        area.redo()
        assert text_of(area) == _expected(BODY.encode(), 0, 5)
        area.undo()
        area.undo()
        assert text_of(area) == BODY.encode()


@pytest.mark.asyncio
async def test_undo_after_save_restores_old_bytes_and_marks_modified(tmp_path: Path) -> None:
    path = _file(tmp_path)
    inode = _inode(path)
    area = NovaTextArea.open(path)
    host = SaveHost(area)
    async with host.run_test() as pilot:
        await pilot.pause()
        await _overwrite(pilot, area)
        assert area.save() is True
        await wait_saved(pilot, area)
        area.undo()
        assert text_of(area) == BODY.encode()
        assert area.modified
        assert area.save() is True
        await wait_saved(pilot, area)
        assert path.read_bytes() == BODY.encode()
        assert _inode(path) == inode
        assert not area.modified
        assert [type(message) for message in host.terminals()] == [NovaTextArea.Saved, NovaTextArea.Saved]


@pytest.mark.asyncio
async def test_redo_after_save(tmp_path: Path) -> None:
    path = _file(tmp_path)
    inode = _inode(path)
    area = NovaTextArea.open(path)
    host = SaveHost(area)
    async with host.run_test() as pilot:
        await pilot.pause()
        await _overwrite(pilot, area)
        assert area.save() is True
        await wait_saved(pilot, area)
        area.undo()
        assert area.save() is True
        await wait_saved(pilot, area)
        assert path.read_bytes() == BODY.encode()
        area.redo()
        assert text_of(area) == _expected(BODY.encode(), 0)
        assert area.modified
        assert area.save() is True
        await wait_saved(pilot, area)
        assert path.read_bytes() == _expected(BODY.encode(), 0)
        assert _inode(path) == inode
        assert len(host.terminals()) == 3


@pytest.mark.asyncio
async def test_paste_of_a_clipboard_copied_before_the_overwrite_is_redirected(tmp_path: Path) -> None:
    path = _file(tmp_path)
    inode = _inode(path)
    area = NovaTextArea.open(path)
    host = SaveHost(area)
    async with host.run_test() as pilot:
        await pilot.pause()
        area.focus()
        area.selection = Selection((0, 0), (0, 3))
        area.action_copy()
        assert area._clipboard_record is not None
        area.replace("ABC", (0, 0), (0, 3))
        assert text_of(area) == b"ABC" + BODY.encode()[3:]
        assert area.save() is True
        await wait_saved(pilot, area)
        assert path.read_bytes() == b"ABC" + BODY.encode()[3:]
        assert _inode(path) == inode
        record = area._clipboard_record
        assert record is not None
        patched = area.document._patched
        assert patched
        for piece in record.content.pieces:
            assert piece.src != 0 or not any(piece.a < b and a < piece.b for a, b in patched)
        assert not any(a < b2 and a2 < b for a, b in area.history.original_ranges() for a2, b2 in patched)
        area.move_cursor((1, 0))
        await pilot.press("ctrl+v")
        assert text_of(area).split(b"\n")[1].startswith(b"row")
        assert text_of(area).startswith(b"ABC")
        assert area.save() is True
        await wait_saved(pilot, area)
        assert path.read_bytes() == text_of(area)


@pytest.mark.asyncio
async def test_two_saves_in_a_row(tmp_path: Path) -> None:
    path = _file(tmp_path)
    inode = _inode(path)
    area = NovaTextArea.open(path)
    host = SaveHost(area)
    async with host.run_test() as pilot:
        await pilot.pause()
        await _overwrite(pilot, area, 0)
        assert area.save() is True
        await wait_saved(pilot, area)
        await _overwrite(pilot, area, 7)
        assert area.save() is True
        await wait_saved(pilot, area)
        assert path.read_bytes() == text_of(area) == _expected(BODY.encode(), 0, 7)
        assert _inode(path) == inode
        assert [type(message) for message in host.terminals()] == [NovaTextArea.Saved, NovaTextArea.Saved]
        assert host.source_changed == []


@pytest.mark.asyncio
async def test_write_failure_keeps_edits_and_modified(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(NovaTextArea, "save_io", failing_io("pwrite", 2, errno.EIO))
    path = _file(tmp_path)
    area = NovaTextArea.open(path)
    host = SaveHost(area)
    async with host.run_test() as pilot:
        await pilot.pause()
        await _overwrite(pilot, area, 0)
        await _overwrite(pilot, area, 5)
        before = text_of(area)
        assert area.save() is True
        await wait_saved(pilot, area)
        (failed,) = host.terminals()
        assert isinstance(failed, NovaTextArea.SaveFailed)
        assert failed.partial is True
        assert failed.stage == "write"
        assert failed.error.errno == errno.EIO
        assert not failed.committed
        assert area.modified
        assert text_of(area) == before
        area.undo()
        assert text_of(area) != before
        area.redo()
        assert text_of(area) == before


@pytest.mark.asyncio
async def test_save_after_partial_failure_succeeds_and_file_equals_document(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(NovaTextArea, "save_io", failing_io("pwrite", 2, errno.EIO))
    path = _file(tmp_path)
    inode = _inode(path)
    area = NovaTextArea.open(path)
    host = SaveHost(area)
    async with host.run_test() as pilot:
        await pilot.pause()
        await _overwrite(pilot, area, 0)
        await _overwrite(pilot, area, 5)
        assert area.save() is True
        await wait_saved(pilot, area)
        assert area._held_identity is not None
        assert area._held_identity.mtime_ns == os.stat(path).st_mtime_ns  # the partial write changed the file; the identity follows it
        assert area.save() is True  # the retry must not ask "the file changed"
        await wait_saved(pilot, area)
        assert [type(message) for message in host.terminals()] == [NovaTextArea.SaveFailed, NovaTextArea.Saved]
        assert NovaTextArea.SaveNeedsConfirmation not in _types(host)
        assert host.source_changed == []
        assert path.read_bytes() == text_of(area) == _expected(BODY.encode(), 0, 5)
        assert _inode(path) == inode
        assert not area.modified


@pytest.mark.asyncio
async def test_changed_file_before_save_asks_the_existing_question_and_writes_nothing(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    events: list[str] = []
    monkeypatch.setattr(NovaTextArea, "save_io", recording_io(events))
    path = _file(tmp_path)
    area = NovaTextArea.open(path)
    host = SaveHost(area)
    async with host.run_test() as pilot:
        await pilot.pause()
        await _overwrite(pilot, area)
        with path.open("ab") as handle:
            handle.write(b"appended\n")
        assert area.save() is False
        await pilot.pause(0.1)
        (message,) = host.saves
        assert isinstance(message[1], NovaTextArea.SaveNeedsConfirmation)
        assert message[1].kind is ChangeKind.MODIFIED
        assert path.read_bytes() == BODY.encode() + b"appended\n"
        assert events == []


@pytest.mark.asyncio
async def test_change_between_check_and_open_becomes_source_changed_flow(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    path = _file(tmp_path)
    io = recording_io([])
    real_open = io.open_write

    def racing_open(name: str) -> int:
        with path.open("ab") as handle:  # after the pre-checks of the job, before the descriptor is verified
            handle.write(b"appended\n")
        return real_open(name)

    io.open_write = racing_open
    monkeypatch.setattr(NovaTextArea, "save_io", io)
    area = NovaTextArea.open(path)
    host = SaveHost(area)
    async with host.run_test() as pilot:
        await pilot.pause()
        await _overwrite(pilot, area)
        assert area.save() is True
        await wait_saved(pilot, area)
        (failed,) = host.terminals()
        assert isinstance(failed, NovaTextArea.SaveFailed)
        assert failed.partial is False
        assert failed.stage == "prepare"
        assert host.source_changed, "the change flow of the widget must start"
        assert path.read_bytes() == BODY.encode() + b"appended\n"
        assert area.modified


@pytest.mark.asyncio
async def test_read_only_file_posts_the_existing_message(tmp_path: Path) -> None:
    if os.geteuid() == 0:
        pytest.skip("root ignores file permissions")
    path = _file(tmp_path)
    path.chmod(0o444)
    area = NovaTextArea.open(path)
    host = SaveHost(area)
    async with host.run_test() as pilot:
        await pilot.pause()
        await _overwrite(pilot, area)
        assert area.save() is True
        await wait_saved(pilot, area)
        (failed,) = host.terminals()
        assert isinstance(failed, NovaTextArea.SaveFailed)
        assert failed.stage == "prepare"
        assert failed.error.strerror == "read-only file" or "read-only file" in str(failed.error)
        assert failed.partial is False
        assert path.read_bytes() == BODY.encode()
        assert area.modified


@pytest.mark.asyncio
async def test_cancel_before_first_write_posts_cancelled(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    gate = Gate()
    monkeypatch.setattr(NovaTextArea, "save_io", gate.inplace_io("open_write"))
    path = _file(tmp_path)
    area = NovaTextArea.open(path)
    host = SaveHost(area)
    async with host.run_test() as pilot:
        await pilot.pause()
        await _overwrite(pilot, area)
        assert area.save() is True
        assert gate.reached.wait(10)
        area.cancel_save()
        gate.release()
        await wait_saved(pilot, area)
        assert [type(message) for message in host.terminals()] == [NovaTextArea.SaveCancelled]
        assert path.read_bytes() == BODY.encode()
        assert area.modified
        assert not area.saving


@pytest.mark.asyncio
async def test_cancel_before_the_save_thread_built_its_job_is_not_lost(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    held = threading.Event()
    entered = threading.Event()
    real_plan = LazyDocument.plan_in_place

    def slow_plan(self: LazyDocument) -> object:
        entered.set()
        assert held.wait(10)
        return real_plan(self)

    monkeypatch.setattr(LazyDocument, "plan_in_place", slow_plan)
    path = _file(tmp_path)
    area = NovaTextArea.open(path)
    host = SaveHost(area)
    async with host.run_test() as pilot:
        await pilot.pause()
        await _overwrite(pilot, area)
        assert area.save() is True
        assert entered.wait(10)
        area.cancel_save()  # the job does not exist yet
        held.set()
        await wait_saved(pilot, area)
        assert [type(message) for message in host.terminals()] == [NovaTextArea.SaveCancelled]
        assert path.read_bytes() == BODY.encode()


@pytest.mark.asyncio
async def test_cancel_after_the_first_write_is_ignored(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    gate = Gate()
    monkeypatch.setattr(NovaTextArea, "save_io", gate.inplace_io("pwrite", at=2))
    path = _file(tmp_path)
    area = NovaTextArea.open(path)
    host = SaveHost(area)
    async with host.run_test() as pilot:
        await pilot.pause()
        await _overwrite(pilot, area, 0)
        await _overwrite(pilot, area, 5)
        assert area.save() is True
        assert gate.reached.wait(10)  # the first range is written
        area.cancel_save()
        gate.release()
        await wait_saved(pilot, area)
        assert [type(message) for message in host.terminals()] == [NovaTextArea.Saved]
        assert path.read_bytes() == text_of(area) == _expected(BODY.encode(), 0, 5)


@pytest.mark.asyncio
async def test_close_after_first_write_posts_partial(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    gate = Gate()
    monkeypatch.setattr(NovaTextArea, "save_io", gate.inplace_io("pwrite", at=2))
    path = _file(tmp_path)
    area = NovaTextArea.open(path)
    host = SaveHost(area)
    async with host.run_test() as pilot:
        await pilot.pause()
        await _overwrite(pilot, area, 0)
        await _overwrite(pilot, area, 5)
        document = area.document
        assert area.save() is True
        assert gate.reached.wait(10)
        area.close()
        assert not document.wait_closed(0.2), "the source must stay open while the writer runs"
        gate.release()
        assert document.wait_closed(10)
        await pilot.pause(0.1)
        (failed,) = host.terminals()
        assert isinstance(failed, NovaTextArea.SaveFailed)
        assert failed.partial is True
        assert "patched" in str(failed.error)
        assert not [thread for thread in threading.enumerate() if thread.name == "nova-save" and thread.is_alive()]
    assert path.read_bytes() == _expected(BODY.encode(), 0, 5)  # the writer ran to its end


class GatedPread(PreadSource):
    """A `PreadSource` whose line scan reads wait for a permit, so that a test fixes how far the scan got when the save runs."""

    def __init__(self, path: Path) -> None:
        super().__init__(path)
        self.permits = threading.Semaphore(0)
        self.gated = True

    def read(self, offset: int, size: int, *, cache: bool = True) -> bytes:
        if self.gated and not cache and threading.current_thread().name == "line-index-scan":
            self.permits.acquire()
        return super().read(offset, size, cache=cache)


BLOCK = 64
LF = b"".join(b"row %03d\n" % n for n in range(200))
CRLF = b"".join(b"row %03d\r\n" % n for n in range(200))
CASES: dict[str, tuple[bytes, list[tuple[int, bytes]]]] = {
    "newline_to_x": (LF, [(31, b"x"), (8 * 150 + 7, b"x")]),
    "byte_to_newline": (LF, [(8 * 4 + 1, b"\n"), (8 * 150 + 2, b"\n")]),
    "crlf_straddle": (CRLF, [(9 * 3 + 8, b"x"), (9 * 140 + 6, b"\r")]),
}


@pytest.mark.asyncio
@pytest.mark.parametrize("scan", ["complete", "running", "unstarted"])
@pytest.mark.parametrize("case", list(CASES))
async def test_line_index_stays_valid_after_a_break_changing_patch(tmp_path: Path, case: str, scan: str) -> None:
    data, patches = CASES[case]
    path = _file(tmp_path, data)
    inode = _inode(path)
    source = GatedPread(path)
    document = LazyDocument(source, LazyConfig(stride=2, scan_block=BLOCK), autostart=False)
    area = NovaTextArea(text="")
    host = SaveHost(area)
    try:
        async with host.run_test() as pilot:
            await pilot.pause()
            area._replace_document(document)
            area.file_path = path
            area._held_identity = source.identity()
            if scan == "complete":
                source.gated = False
                document.start_scan()
                assert document.wait_indexed(30)
            elif scan == "running":
                document.start_scan()
                source.permits.release(4)
                await wait_until(pilot, lambda: document.snapshot().scanned_bytes >= 3 * BLOCK)
                assert not document.snapshot().complete
            expected = data
            for offset, new in patches:
                hex_edit(document, offset, offset + len(new), new)
                expected = expected[:offset] + new + expected[offset + len(new) :]
            assert area.save() is True
            await wait_saved(pilot, area)
            assert [type(message) for message in host.terminals()] == [NovaTextArea.Saved]
            assert path.read_bytes() == expected
            assert _inode(path) == inode
            source.gated = False
            source.permits.release(10_000)
            document.start_scan()
            assert document.wait_indexed(30)
            rows = [row.decode("utf-8", "surrogateescape") for row in TERMINATOR.split(expected)]
            assert document.line_count == len(rows)
            assert [document.get_line(row) for row in range(len(rows))] == rows
            assert document.read_bytes(0, document.length) == expected
    finally:
        source.gated = False
        source.permits.release(10_000)


@pytest.mark.asyncio
async def test_streaming_save_after_an_in_place_save_leaves_the_right_file(tmp_path: Path) -> None:
    path = _file(tmp_path)
    inode = _inode(path)
    area = NovaTextArea.open(path)
    host = SaveHost(area)
    async with host.run_test() as pilot:
        await pilot.pause()
        await _overwrite(pilot, area, 0)
        assert area.save() is True
        await wait_saved(pilot, area)
        assert _inode(path) == inode
        area.move_cursor((3, 0))
        await pilot.press("q", "q")
        assert area.save() is True
        await wait_saved(pilot, area)
        assert _inode(path) != inode
        assert path.read_bytes() == text_of(area)
        assert text_of(area).startswith(b"xow 0")
        await _overwrite(pilot, area, 9)  # and in place again, on the new file
        newer = _inode(path)
        assert area.save() is True
        await wait_saved(pilot, area)
        assert _inode(path) == newer
        assert path.read_bytes() == text_of(area)
        assert [type(message) for message in host.terminals()] == [NovaTextArea.Saved] * 3


@pytest.mark.asyncio
async def test_in_place_save_into_a_local_copy_shaped_path_keeps_the_inode_and_changes_the_digest(tmp_path: Path) -> None:
    directory = tmp_path / "local"
    directory.mkdir()
    path = _file(directory)
    inode = _inode(path)
    before = hashlib.sha256(path.read_bytes()).hexdigest()
    area = NovaTextArea.open(path)
    host = SaveHost(area)
    async with host.run_test() as pilot:
        await pilot.pause()
        await _overwrite(pilot, area)
        assert area.save() is True
        await wait_saved(pilot, area)
        assert hashlib.sha256(path.read_bytes()).hexdigest() != before
        assert _inode(path) == inode
        assert os.listdir(directory) == [path.name]
