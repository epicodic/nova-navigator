"""`InPlaceJob` review findings: close during the write (F2), a same-size change before the first write (F3), a failed first write (F4)."""

from __future__ import annotations

import errno
import os
import threading
from collections.abc import Sequence
from pathlib import Path

import pytest

from nova_editor.core import ChangeKind, FileIdentity, InPlaceJob, InPlaceResult, PreadSource
from nova_editor.core.redirect import Copy
from nova_editor.core.save import SaveFailed, SaveIo, SaveSettings
from nova_editor.document import LazyDocument
from tests.nova_editor.core.test_inplace_job import DATA, RecordingHost, expected, open_doc, overwrite
from tests.nova_editor.document.helpers_save import in_place_job, run_in_place

CHUNKY = SaveSettings(chunk=4, fsync_every=64)
NEW = b"ABCDEFGHIJKLMNOPQRST"  # 20 bytes at 10: five chunks of four


class ClosingHost(RecordingHost):
    """Closes the document right after `begin_patch`."""

    def begin_patch(self, copies: Sequence[Copy], ranges: Sequence[tuple[int, int]]) -> None:
        super().begin_patch(copies, ranges)
        self.doc.close()


def run_in_thread(doc: LazyDocument, job: InPlaceJob) -> tuple[threading.Thread, list[InPlaceResult | SaveFailed]]:
    """Run `job` on a thread that the document's closer joins before it closes the source, as the widget does."""
    outcome: list[InPlaceResult | SaveFailed] = []
    doc.lock_edits("saving")
    doc.begin_save()

    def work() -> None:
        try:
            outcome.append(job.run())
        except SaveFailed as error:
            outcome.append(error)

    thread = threading.Thread(target=work)
    thread.start()
    doc.join_on_close(thread)
    return thread, outcome


def test_a_close_between_begin_patch_and_the_first_pwrite_still_patches_everything(tmp_path: Path) -> None:
    doc, path = open_doc(tmp_path)
    overwrite(doc, 10, NEW)
    job = in_place_job(doc, path, host=ClosingHost(doc, []), settings=CHUNKY)
    thread, outcome = run_in_thread(doc, job)
    thread.join(10)
    assert doc.wait_closed(10)
    assert len(outcome) == 1
    assert isinstance(outcome[0], InPlaceResult), outcome[0]
    assert path.read_bytes() == expected(DATA, (10, NEW))
    assert job.committed


def test_a_close_after_the_first_pwrite_still_patches_everything(tmp_path: Path) -> None:
    doc, path = open_doc(tmp_path)
    overwrite(doc, 10, NEW)
    io = SaveIo()
    real = io.pwrite
    calls = 0

    def closing(fd: int, data: bytes | memoryview, offset: int) -> int:
        nonlocal calls
        calls += 1
        written = real(fd, data, offset)
        if calls == 1:
            doc.close()
        return written

    io.pwrite = closing
    job = in_place_job(doc, path, io=io, settings=CHUNKY)
    thread, outcome = run_in_thread(doc, job)
    thread.join(10)
    assert doc.wait_closed(10)
    assert len(outcome) == 1
    assert isinstance(outcome[0], InPlaceResult), outcome[0]
    assert calls == 5
    assert path.read_bytes() == expected(DATA, (10, NEW))


def test_the_job_is_committed_only_after_begin_patch_and_a_cancel_after_it_is_ignored(tmp_path: Path) -> None:
    doc, path = open_doc(tmp_path)
    try:
        overwrite(doc, 10, NEW)
        seen: list[bool] = []

        class Probe(RecordingHost):
            def begin_patch(self, copies: Sequence[Copy], ranges: Sequence[tuple[int, int]]) -> None:
                seen.append(job.committed)
                super().begin_patch(copies, ranges)

        job = in_place_job(doc, path, host=Probe(doc, []), settings=CHUNKY)
        result = None
        doc.lock_edits("saving")
        doc.begin_save()
        try:
            result = job.run()
        finally:
            doc.end_save()
            doc.unlock_edits()
        assert seen == [False]
        assert job.committed
        job.cancel()
        assert result is not None
    finally:
        doc.close()


def test_a_same_size_change_before_the_first_write_fails_plainly_and_rolls_back(tmp_path: Path) -> None:
    doc, path = open_doc(tmp_path)
    try:
        overwrite(doc, 10, b"#")
        events: list[str] = []

        class Touching(RecordingHost):
            def begin_patch(self, copies: Sequence[Copy], ranges: Sequence[tuple[int, int]]) -> None:
                super().begin_patch(copies, ranges)
                os.utime(path, ns=(1_500_000_000_000_000_000, 1_500_000_000_000_000_000))  # an external touch, same size

        with pytest.raises(SaveFailed) as raised:
            run_in_place(doc, path, host=Touching(doc, events))
        assert raised.value.partial is False
        assert raised.value.change is ChangeKind.MODIFIED
        assert path.read_bytes() == DATA
        assert doc._patched == []
        assert "end_patch(None)" in events
    finally:
        doc.close()


def injected_write_then_raise() -> SaveIo:
    """A `SaveIo` whose first `pwrite` really writes the bytes and then raises."""
    io = SaveIo()
    real = io.pwrite
    calls = 0

    def write_then_raise(fd: int, data: bytes | memoryview, offset: int) -> int:
        nonlocal calls
        calls += 1
        if calls == 1:
            real(fd, data, offset)
            raise OSError(errno.EIO, "injected failure after the write")
        return real(fd, data, offset)

    io.pwrite = write_then_raise
    return io


def test_a_failed_first_pwrite_keeps_the_ranges_patched_so_a_restored_text_is_rewritten(tmp_path: Path) -> None:
    doc, path = open_doc(tmp_path)
    try:
        removed = overwrite(doc, 10, b"#")
        with pytest.raises(SaveFailed) as raised:
            run_in_place(doc, path, io=injected_write_then_raise())
        assert raised.value.partial is False  # the plain message stays
        assert raised.value.stage == "write"
        assert path.read_bytes() == expected(DATA, (10, b"#"))  # the bytes were written although the call raised
        assert doc._patched == [(10, 11)]
        assert isinstance(doc._source, PreadSource)
        assert raised.value.identity == FileIdentity.from_stat(os.stat(path))
        doc.splice_bytes(10, 11, removed)  # the text is the original again
        assert doc.read_bytes(0, doc.length) == DATA
        result = run_in_place(doc, path, held=raised.value.identity)
        assert result.written == 1
        assert path.read_bytes() == DATA
    finally:
        doc.close()
