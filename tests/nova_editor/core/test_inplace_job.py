"""`InPlaceJob`: the writer of the in-place save (ACT3 design 3, ADR-3 rev 2); every file lives in `tmp_path`."""

from __future__ import annotations

import errno
import os
from collections.abc import Callable, Sequence
from pathlib import Path

import pytest

from nova_editor.core import ChangeKind, Content, FileIdentity, InPlaceJob, InPlacePlan, PreadSource
from nova_editor.core.redirect import Copy
from nova_editor.core.save import PlanPart, SaveCancelled, SaveFailed, SaveIo, SaveProgress
from nova_editor.document import LazyDocument
from tests.nova_editor.core.save_helpers import failing_io, recording_io
from tests.nova_editor.document.helpers_save import content_of, in_place_job, open_pread_document, run_in_place, run_job

DATA = bytes(range(65, 91)) * 4  # 104 bytes, one row
NAME = "doc.bin"


class RecordingHost:
    """Delegates to a document and records the host calls in `events` (and the copies `prepare_patch` returned)."""

    def __init__(self, doc: LazyDocument, events: list[str]) -> None:
        self.doc = doc
        self.events = events
        self.copies: list[Copy] = []

    def plan_in_place(self) -> InPlacePlan | None:
        self.events.append("plan_in_place")
        return self.doc.plan_in_place()

    def plan(self, offset: int, limit: int, unverified: bool) -> list[PlanPart]:
        return self.doc.plan(offset, limit, unverified)

    def prepare_patch(self, ranges: Sequence[tuple[int, int]], chunk: int, check: Callable[[], None]) -> list[Copy]:
        self.events.append("prepare_patch")
        self.copies = self.doc.prepare_patch(ranges, chunk, check)
        return self.copies

    def begin_patch(self, copies: Sequence[Copy], ranges: Sequence[tuple[int, int]]) -> None:
        self.events.append("begin_patch")
        self.doc.begin_patch(copies, ranges)

    def end_patch(self, identity: FileIdentity | None) -> None:
        self.events.append("end_patch" if identity is not None else "end_patch(None)")
        self.doc.end_patch(identity)


def overwrite(doc: LazyDocument, start: int, data: bytes) -> Content:
    """Replace the bytes at `start` by `data` (same length); return the removed original content."""
    removed = doc.splice_bytes(start, start + len(data), content_of(doc, data)).removed
    assert removed is not None
    return removed


def expected(data: bytes, *edits: tuple[int, bytes]) -> bytes:
    for start, new in edits:
        data = data[:start] + new + data[start + len(new) :]
    return data


def open_doc(tmp_path: Path) -> tuple[LazyDocument, Path]:
    doc, _ = open_pread_document(tmp_path, DATA, name=NAME)
    return doc, tmp_path / NAME


def test_one_byte_overwrite_keeps_inode_and_links(tmp_path: Path) -> None:
    doc, data = open_pread_document(tmp_path, DATA)
    try:
        link = tmp_path / "second_link"
        os.link(tmp_path / "doc.bin", link)
        before = os.stat(tmp_path / "doc.bin")
        doc.splice_bytes(10, 11, content_of(doc, b"#"))
        events: list[str] = []
        result = run_in_place(doc, tmp_path / "doc.bin", io=recording_io(events))
        after = os.stat(tmp_path / "doc.bin")
        assert (after.st_ino, after.st_uid, after.st_mode) == (before.st_ino, before.st_uid, before.st_mode)
        assert link.read_bytes() == data[:10] + b"#" + data[11:] == (tmp_path / "doc.bin").read_bytes()
        assert sorted(p.name for p in tmp_path.iterdir()) == ["doc.bin", "second_link"]  # no temp file
        assert "mkstemp" not in events
        assert "replace" not in events
        assert events.index("open_write") < events.index("pwrite") < events.index("fsync")
        assert result.written == 1
        assert result.length == len(DATA)
        assert result.identity == FileIdentity.from_stat(after)
        assert [(copy.a, copy.b) for copy in result.copies] == [(10, 11)]
    finally:
        doc.close()


def test_undone_overwrite_writes_nothing_for_the_range(tmp_path: Path) -> None:
    doc, path = open_doc(tmp_path)
    try:
        removed = overwrite(doc, 10, b"#")
        doc.splice_bytes(10, 11, removed)
        events: list[str] = []
        result = run_in_place(doc, path, io=recording_io(events))
        assert "pwrite" not in events
        assert "fsync" in events
        assert result.written == 0
        assert path.read_bytes() == DATA
    finally:
        doc.close()


def test_insert_and_delete_restoring_length_has_no_plan(tmp_path: Path) -> None:
    doc, path = open_doc(tmp_path)
    try:
        doc.splice_bytes(5, 5, content_of(doc, b"ab"))
        doc.splice_bytes(20, 22, Content.from_pieces((), 0))
        assert doc.length == len(DATA)
        assert doc.plan_in_place() is None
        events: list[str] = []
        with pytest.raises(SaveFailed) as raised:
            run_in_place(doc, path, io=recording_io(events))
        assert raised.value.stage == "internal"
        assert raised.value.partial is False
        assert "pwrite" not in events
        assert path.read_bytes() == DATA
    finally:
        doc.close()


def test_order_open_copies_begin_patch_pwrite_fsync(tmp_path: Path) -> None:
    doc, path = open_doc(tmp_path)
    try:
        overwrite(doc, 10, b"#")
        events: list[str] = []
        host = RecordingHost(doc, events)
        run_in_place(doc, path, io=recording_io(events), host=host)
        order = ["plan_in_place", "open_write", "prepare_patch", "begin_patch", "pwrite", "fsync", "end_patch"]
        assert [name for name in events if name in order] == order
    finally:
        doc.close()


@pytest.mark.skipif(os.geteuid() == 0, reason="root can write read-only files")
def test_unwritable_file_fails_before_any_write_with_read_only_message(tmp_path: Path) -> None:
    doc, path = open_doc(tmp_path)
    try:
        overwrite(doc, 10, b"#")
        path.chmod(0o444)
        events: list[str] = []
        with pytest.raises(SaveFailed) as raised:
            run_in_place(doc, path, io=recording_io(events))
        assert str(raised.value) == "read-only file"
        assert raised.value.stage == "prepare"
        assert raised.value.partial is False
        assert "pwrite" not in events
        assert path.read_bytes() == DATA
        assert doc._patched == []
    finally:
        doc.close()


def test_changed_file_blocks_writing_and_names_the_kind(tmp_path: Path) -> None:
    doc, path = open_doc(tmp_path)
    try:
        overwrite(doc, 10, b"#")
        events: list[str] = []
        io = recording_io(events)
        real = io.open_write

        def modify_then_open(name: str) -> int:
            with open(name, "ab") as handle:  # the file changes between the check and the open
                handle.write(b"tail")
            return real(name)

        io.open_write = modify_then_open
        with pytest.raises(SaveFailed) as raised:
            run_in_place(doc, path, io=io)
        assert raised.value.change is ChangeKind.MODIFIED
        assert raised.value.stage == "prepare"
        assert raised.value.partial is False
        assert "pwrite" not in events
        assert path.read_bytes() == DATA + b"tail"
        assert doc._patched == []
    finally:
        doc.close()


def test_file_changed_before_the_job_names_the_kind_without_opening(tmp_path: Path) -> None:
    doc, path = open_doc(tmp_path)
    try:
        overwrite(doc, 10, b"#")
        path.write_bytes(DATA + b"more")
        events: list[str] = []
        with pytest.raises(SaveFailed) as raised:
            run_in_place(doc, path, io=recording_io(events))
        assert raised.value.change is ChangeKind.MODIFIED
        assert "open_write" not in events
    finally:
        doc.close()


def test_replaced_file_blocks_writing(tmp_path: Path) -> None:
    doc, path = open_doc(tmp_path)
    try:
        overwrite(doc, 10, b"#")
        events: list[str] = []
        io = recording_io(events)
        real = io.open_write

        def replace_then_open(name: str) -> int:
            fresh = tmp_path / "fresh"
            fresh.write_bytes(DATA)
            os.replace(fresh, name)
            return real(name)

        io.open_write = replace_then_open
        with pytest.raises(SaveFailed) as raised:
            run_in_place(doc, path, io=io)
        assert raised.value.change is ChangeKind.REPLACED
        assert "pwrite" not in events
        assert path.read_bytes() == DATA
    finally:
        doc.close()


def two_range_document(tmp_path: Path) -> tuple[LazyDocument, Path, bytes]:
    doc, path = open_doc(tmp_path)
    overwrite(doc, 10, b"#")
    overwrite(doc, 60, b"@")
    return doc, path, expected(DATA, (10, b"#"), (60, b"@"))


def test_write_error_after_first_range_is_partial(tmp_path: Path) -> None:
    doc, path, final = two_range_document(tmp_path)
    try:
        with pytest.raises(SaveFailed) as raised:
            run_in_place(doc, path, io=failing_io("pwrite", 2, errno.EIO))
        error = raised.value
        assert error.partial is True
        assert error.stage == "write"
        assert error.errno == errno.EIO
        assert path.read_bytes() == expected(DATA, (10, b"#"))  # the first range is on disk
        assert doc.read_bytes(0, doc.length) == final  # the edits are kept
        assert doc.plan_in_place() == InPlacePlan(len(DATA), ((10, 11), (60, 61)))  # the document still differs from the file
        assert doc._patched == [(10, 11), (60, 61)]
        source = doc._source
        assert isinstance(source, PreadSource)
        assert source.read(10, 1) == DATA[10:11]  # the overlay still serves the old bytes
        result = run_in_place(doc, path)  # a retry rewrites the write set
        assert result.written == 2
        assert path.read_bytes() == final
    finally:
        doc.close()


def test_first_pwrite_error_is_plain(tmp_path: Path) -> None:
    doc, path, final = two_range_document(tmp_path)
    try:
        plan = doc.plan_in_place()
        with pytest.raises(SaveFailed) as raised:
            run_in_place(doc, path, io=failing_io("pwrite", 1, errno.EIO))
        assert raised.value.partial is False
        assert raised.value.stage == "write"
        assert raised.value.errno == errno.EIO
        assert path.read_bytes() == DATA
        assert doc._patched == [(10, 11), (60, 61)]  # the failed call may have written: the ranges stay patched, so that a retry rewrites them
        assert doc.plan_in_place() == plan
        assert doc.read_bytes(0, doc.length) == final
        source = doc._source
        assert isinstance(source, PreadSource)
        assert source.read(0, len(DATA)) == DATA  # no patch window is left open: the source reads the unchanged file
        run_in_place(doc, path)  # a retry installs the overlay once more without a clash
        assert path.read_bytes() == final
    finally:
        doc.close()


def test_fsync_error_is_partial(tmp_path: Path) -> None:
    doc, path, final = two_range_document(tmp_path)
    try:
        with pytest.raises(SaveFailed) as raised:
            run_in_place(doc, path, io=failing_io("fsync", 1, errno.EIO))
        assert raised.value.partial is True
        assert raised.value.stage == "flush"
        assert path.read_bytes() == final  # the bytes were written, only the flush failed
        assert doc._patched == [(10, 11), (60, 61)]
    finally:
        doc.close()


def test_fsync_called_before_success_even_for_zero_ranges(tmp_path: Path) -> None:
    doc, path = open_doc(tmp_path)
    try:
        events: list[str] = []
        result = run_in_place(doc, path, io=recording_io(events))
        assert result.written == 0
        assert events.count("fsync") == 1
        assert "pwrite" not in events
        with pytest.raises(SaveFailed) as raised:  # nothing was written, so a failing flush is a plain failure
            run_in_place(doc, path, io=failing_io("fsync", 1, errno.EIO))
        assert raised.value.stage == "flush"
        assert raised.value.partial is False
    finally:
        doc.close()


def assert_unchanged(doc: LazyDocument, path: Path, final: bytes, plan: InPlacePlan | None) -> None:
    assert path.read_bytes() == DATA
    assert doc._patched == []
    assert doc.plan_in_place() == plan
    assert doc.read_bytes(0, doc.length) == final
    source = doc._source
    assert isinstance(source, PreadSource)
    assert source.read(0, len(DATA)) == DATA


def test_cancel_before_started_leaves_everything_unchanged(tmp_path: Path) -> None:
    doc, path, final = two_range_document(tmp_path)
    try:
        plan = doc.plan_in_place()
        job = in_place_job(doc, path)
        job.cancel()
        with pytest.raises(SaveCancelled):
            run_job(doc, job)
        assert_unchanged(doc, path, final, plan)
        assert not job.started

        events: list[str] = []
        io = recording_io(events)
        holder: list[InPlaceJob] = []
        real = io.open_write

        def cancel_on_open(name: str) -> int:
            holder[0].cancel()  # honoured at the next check, which is in the copy phase
            return real(name)

        io.open_write = cancel_on_open
        holder.append(in_place_job(doc, path, io=io))
        with pytest.raises(SaveCancelled):
            run_job(doc, holder[0])
        assert "pwrite" not in events
        assert not holder[0].started
        assert_unchanged(doc, path, final, plan)
        run_in_place(doc, path)  # the document is still saveable
        assert path.read_bytes() == final
    finally:
        doc.close()


def test_cancel_after_started_is_ignored(tmp_path: Path) -> None:
    doc, path, final = two_range_document(tmp_path)
    try:
        events: list[str] = []
        io = recording_io(events)
        holder: list[InPlaceJob] = []
        real = io.pwrite

        def cancel_on_write(fd: int, data: bytes | memoryview, offset: int) -> int:
            holder[0].cancel()
            return real(fd, data, offset)

        io.pwrite = cancel_on_write
        holder.append(in_place_job(doc, path, io=io))
        result = run_job(doc, holder[0])
        assert result.written == 2
        assert holder[0].started
        assert events.count("pwrite") == 2
        assert path.read_bytes() == final
    finally:
        doc.close()


def test_progress_phases_and_started_flag(tmp_path: Path) -> None:
    doc, path, _ = two_range_document(tmp_path)
    try:
        reports: list[SaveProgress] = []
        run_in_place(doc, path, progress=reports.append)
        phases = [report.phase for report in reports]
        assert phases[-2:] == ["flushing", "finishing"]
        assert set(phases[:-2]) == {"writing"}
        assert reports[0].started is False
        assert all(report.started for report in reports[-3:])
        writing = [report for report in reports if report.phase == "writing"]
        assert all(report.total == 2 for report in writing)
        assert writing[-1].done == 2
        assert [report.done for report in writing] == sorted(report.done for report in writing)
    finally:
        doc.close()


def test_write_set_reads_overlaid_original_piece(tmp_path: Path) -> None:
    doc, path = open_doc(tmp_path)
    try:
        stray = overwrite(doc, 10, b"#")  # still names the original byte 10
        run_in_place(doc, path)
        assert path.read_bytes() == expected(DATA, (10, b"#"))
        doc.splice_bytes(10, 11, stray)  # no redirect ran: an original piece over a patched range
        assert doc.plan_in_place() == InPlacePlan(len(DATA), ((10, 11),))
        result = run_in_place(doc, path)
        assert result.written == 1
        assert path.read_bytes() == DATA == doc.read_bytes(0, doc.length)
    finally:
        doc.close()


def test_original_part_outside_the_patched_ranges_is_an_internal_error(tmp_path: Path) -> None:
    doc, path = open_doc(tmp_path)
    try:
        overwrite(doc, 10, b"#")

        class Lying(RecordingHost):
            def plan(self, offset: int, limit: int, unverified: bool) -> list[PlanPart]:
                return [PlanPart(0, doc._source, offset, offset + limit)]  # the original where the document holds an add piece

        events: list[str] = []
        with pytest.raises(SaveFailed) as raised:
            run_in_place(doc, path, io=recording_io(events), host=Lying(doc, events))
        assert raised.value.stage == "internal"
        assert raised.value.partial is False
        assert "pwrite" not in events
        assert path.read_bytes() == DATA
        assert doc._patched == []
        run_in_place(doc, path)
        assert path.read_bytes() == expected(DATA, (10, b"#"))
    finally:
        doc.close()


def test_second_save_after_undo_restores_original_bytes(tmp_path: Path) -> None:
    doc, path = open_doc(tmp_path)
    try:
        removed = overwrite(doc, 10, b"#")
        result = run_in_place(doc, path)
        assert path.read_bytes() == expected(DATA, (10, b"#"))
        redirected = doc.redirect_for(result.copies).apply(removed)
        assert [piece.src for piece in redirected.pieces] != [0]  # the undo record names the copy now
        doc.splice_bytes(10, 11, redirected)  # undo
        assert doc.read_bytes(0, doc.length) == DATA
        again = run_in_place(doc, path)
        assert again.written == 1
        assert path.read_bytes() == DATA
    finally:
        doc.close()


def test_prepare_patch_copies_only_uncovered_subranges(tmp_path: Path) -> None:
    doc, path = open_doc(tmp_path)
    try:
        overwrite(doc, 10, b"ab")
        run_in_place(doc, path)
        assert doc._patched == [(10, 12)]
        overwrite(doc, 8, b"123456")
        events: list[str] = []
        host = RecordingHost(doc, events)
        run_in_place(doc, path, host=host)
        assert [(copy.a, copy.b) for copy in host.copies] == [(8, 10), (12, 14)]
        assert doc._patched == [(8, 14)]
        assert path.read_bytes() == expected(DATA, (8, b"123456"))
        assert path.read_bytes() == doc.read_bytes(0, doc.length)
    finally:
        doc.close()


def test_enospc_on_pwrite_after_start_is_partial(tmp_path: Path) -> None:
    doc, path, _ = two_range_document(tmp_path)
    try:
        with pytest.raises(SaveFailed) as raised:
            run_in_place(doc, path, io=failing_io("pwrite", 2, errno.ENOSPC))
        assert raised.value.partial is True
        assert raised.value.errno == errno.ENOSPC
        assert raised.value.stage == "write"
    finally:
        doc.close()


def test_etxtbsy_is_plain_failure_with_errno_text(tmp_path: Path) -> None:
    doc, path = open_doc(tmp_path)
    try:
        overwrite(doc, 10, b"#")
        events: list[str] = []
        io = failing_io("open_write", 1, errno.ETXTBSY)
        recording = recording_io(events, io)
        with pytest.raises(SaveFailed) as raised:
            run_in_place(doc, path, io=recording)
        assert raised.value.stage == "prepare"
        assert raised.value.errno == errno.ETXTBSY
        assert raised.value.partial is False
        assert "pwrite" not in events
        assert doc._patched == []
    finally:
        doc.close()


def test_short_pwrite_returns_are_looped(tmp_path: Path) -> None:
    doc, path = open_doc(tmp_path)
    try:
        overwrite(doc, 10, b"0123456789")
        io = SaveIo()
        real = io.pwrite
        calls: list[int] = []

        def three_at_a_time(fd: int, data: bytes | memoryview, offset: int) -> int:
            calls.append(offset)
            return real(fd, bytes(data[:3]), offset)

        io.pwrite = three_at_a_time
        run_in_place(doc, path, io=io)
        assert calls == [10, 13, 16, 17]  # the chunk is 7 bytes: 10..17 in 3+3+1, then 17..20
        assert path.read_bytes() == expected(DATA, (10, b"0123456789"))
    finally:
        doc.close()


def test_a_write_that_accepts_no_bytes_fails(tmp_path: Path) -> None:
    doc, path = open_doc(tmp_path)
    try:
        overwrite(doc, 10, b"#")
        io = SaveIo()

        def accept_nothing(fd: int, data: bytes | memoryview, offset: int) -> int:
            del fd, data, offset
            return 0

        io.pwrite = accept_nothing
        with pytest.raises(SaveFailed) as raised:
            run_in_place(doc, path, io=io)
        assert raised.value.stage == "write"
        assert raised.value.partial is False  # nothing was transferred
        assert path.read_bytes() == DATA
        assert doc._patched == []
    finally:
        doc.close()


def test_the_file_is_opened_without_truncation_and_creation(tmp_path: Path) -> None:
    path = tmp_path / "plain"
    path.write_bytes(DATA)
    fd = SaveIo().open_write(str(path))
    try:
        assert os.fstat(fd).st_size == len(DATA)
        assert path.read_bytes() == DATA
        with pytest.raises(OSError, match="Bad file descriptor"):
            os.read(fd, 1)  # opened write only
    finally:
        os.close(fd)
    with pytest.raises(FileNotFoundError):
        SaveIo().open_write(str(tmp_path / "missing"))
    assert not (tmp_path / "missing").exists()


def test_the_file_may_not_change_size_during_the_patch(tmp_path: Path) -> None:
    doc, path = open_doc(tmp_path)
    try:
        overwrite(doc, 10, b"#")
        io = SaveIo()
        real = io.pwrite

        def grow(fd: int, data: bytes | memoryview, offset: int) -> int:
            count = real(fd, data, offset)
            os.pwrite(fd, b"extra", len(DATA))  # another writer appends
            return count

        io.pwrite = grow
        with pytest.raises(SaveFailed) as raised:
            run_in_place(doc, path, io=io)
        assert raised.value.partial is True
        assert raised.value.stage == "flush"
    finally:
        doc.close()


def test_partial_failure_carries_the_identity_of_the_file_after_the_partial_write(tmp_path: Path) -> None:
    doc, path, _ = two_range_document(tmp_path)
    try:
        with pytest.raises(SaveFailed) as raised:
            run_in_place(doc, path, io=failing_io("pwrite", 2, errno.EIO))
        assert raised.value.partial is True
        assert raised.value.identity == FileIdentity.from_stat(os.stat(path))  # the write changed the mtime: a retry must not look like an outside change
    finally:
        doc.close()


def test_partial_flush_failure_carries_the_identity(tmp_path: Path) -> None:
    doc, path, _ = two_range_document(tmp_path)
    try:
        with pytest.raises(SaveFailed) as raised:
            run_in_place(doc, path, io=failing_io("fsync", 1, errno.EIO))
        assert raised.value.identity == FileIdentity.from_stat(os.stat(path))
    finally:
        doc.close()


def test_failure_before_the_first_write_carries_no_identity(tmp_path: Path) -> None:
    doc, path, _ = two_range_document(tmp_path)
    try:
        with pytest.raises(SaveFailed) as raised:
            run_in_place(doc, path, io=failing_io("open_write", 1, errno.EIO))  # a first pwrite that raises may have written: it carries the identity
        assert raised.value.partial is False
        assert raised.value.identity is None
    finally:
        doc.close()
