"""`LazyDocument` edit lock, save registration and `plan` (ACT5 Task 10, design 7.1 and 7.2)."""

from __future__ import annotations

import os
import threading
from pathlib import Path

import pytest

from nova_editor.core import BytesSource, SourceChanged
from nova_editor.core.save import PlanPart
from nova_editor.document._lazy_config import LazyConfig
from nova_editor.document._lazy_document import EditsLocked, LazyDocument, RowUnavailable
from tests.nova_editor.core.reference import ALPHABET, Rng

SMALL = LazyConfig(stride=4, index_long_line_threshold=64, long_row_threshold=512, word_wrap_limit=128, checkpoint_chars=64)
FUZZ_SEEDS = range(12)
FUZZ_EDITS = 25
FUZZ_PLANS = 12
JOIN_SECONDS = 10.0


def _closed(doc: LazyDocument) -> None:
    doc.close()
    assert doc.wait_closed(JOIN_SECONDS)


def test_locked_edits_raise_with_the_reason() -> None:
    doc = LazyDocument.from_text("abc\ndef", SMALL)
    try:
        content = doc.selection_content((0, 0), (0, 2))
        doc.lock_edits("saving")
        assert issubclass(EditsLocked, RowUnavailable)
        for attempt in (
            lambda: doc.replace_range((0, 0), (0, 1), "x"),
            lambda: doc.splice((0, 0), (0, 1), content),
            lambda: doc.splice_bytes(0, 1, content),
        ):
            with pytest.raises(EditsLocked, match="saving"):
                attempt()
        assert doc.read_bytes(0, 7) == b"abc\ndef"
        doc.unlock_edits()
        doc.replace_range((0, 0), (0, 1), "x")
        assert doc.read_bytes(0, 7) == b"xbc\ndef"
    finally:
        _closed(doc)


def test_the_reason_is_the_one_given() -> None:
    doc = LazyDocument.from_text("abc", SMALL)
    try:
        doc.lock_edits("file changed on disk")
        with pytest.raises(EditsLocked, match="file changed on disk"):
            doc.replace_range((0, 0), (0, 0), "x")
    finally:
        _closed(doc)


def test_begin_and_end_save_set_and_clear_the_flag() -> None:
    doc = LazyDocument.from_text("abc", SMALL)
    try:
        assert not doc.saving
        doc.begin_save()
        assert doc.saving
        with pytest.raises(RuntimeError, match="save"):
            doc.require_not_saving("load_text")
        doc.end_save()
        assert not doc.saving
        doc.require_not_saving("load_text")
    finally:
        _closed(doc)


def _edited(seed: int, config: LazyConfig = SMALL) -> LazyDocument:
    rng = Rng(seed)
    text = "".join(rng.choice([item.decode("utf-8", "surrogateescape") for item in ALPHABET]) for _ in range(rng.randint(0, 80)))
    doc = LazyDocument.from_text(text, config)
    for _ in range(rng.randint(0, FUZZ_EDITS)):
        row = rng.randint(0, doc.line_count - 1)
        column = rng.randint(0, len(doc.get_line(row)))
        row2 = rng.randint(row, doc.line_count - 1)
        column2 = rng.randint(0, len(doc.get_line(row2)))
        if row == row2:
            column2 = max(column, column2)
        inserted = "".join(rng.choice([item.decode("utf-8", "surrogateescape") for item in ALPHABET]) for _ in range(rng.randint(0, 6)))
        try:
            doc.replace_range((row, column), (row2, column2), inserted)
        except RowUnavailable:
            continue
    return doc


def _joined(parts: list[PlanPart]) -> bytes:
    return b"".join(part.source.read(part.a, part.b - part.a) for part in parts)


@pytest.mark.parametrize("seed", FUZZ_SEEDS)
def test_plan_parts_join_to_the_document_bytes(seed: int) -> None:
    doc = _edited(seed)
    try:
        rng = Rng(seed + 1000)
        length = doc.length
        for offset, limit in [(0, length), (0, 0), (length, 5), *((rng.randint(0, length), rng.randint(0, length + 3)) for _ in range(FUZZ_PLANS))]:
            parts = doc.plan(offset, limit, unverified=False)
            assert _joined(parts) == doc.read_bytes(offset, limit), (offset, limit)
            assert all(part.b >= part.a for part in parts)
            assert all(part.source is doc._source for part in parts if part.src == 0)
    finally:
        _closed(doc)


def test_plan_of_an_unedited_document_is_the_original() -> None:
    doc = LazyDocument.from_text("abc\ndef", SMALL)
    try:
        parts = doc.plan(2, 4, unverified=False)
        assert [(part.src, part.a, part.b) for part in parts] == [(0, 2, 6)]
        assert parts[0].source is doc._source
    finally:
        _closed(doc)


def test_unverified_plan_reads_through_a_changed_file(tmp_path: Path) -> None:
    path = tmp_path / "f.txt"
    path.write_bytes(b"abcdef\nghi")
    doc = LazyDocument.from_path(path, SMALL)
    try:
        path.write_bytes(b"ABCDEF\nGHI")
        stat = path.stat()
        os.utime(path, ns=(stat.st_atime_ns, stat.st_mtime_ns + 5_000_000_000))
        verified = doc.plan(0, 10, unverified=False)[0]
        with pytest.raises(SourceChanged):
            verified.source.read(verified.a, verified.b - verified.a, cache=False)
        unverified = doc.plan(0, 10, unverified=True)[0]
        assert unverified.source is not doc._source
        assert unverified.source.read(unverified.a, unverified.b - unverified.a, cache=False) == b"ABCDEF\nGHI"
    finally:
        _closed(doc)


def test_unverified_plan_keeps_a_source_without_a_reader() -> None:
    doc = LazyDocument.from_text("abc", SMALL)
    try:
        assert doc.plan(0, 3, unverified=True)[0].source is doc._source
    finally:
        _closed(doc)


class SlowSource:
    """`BytesSource` whose reads can be made to block after the document has been opened."""

    def __init__(self, data: bytes) -> None:
        self._inner = BytesSource(data)
        self.armed = False
        self.entered = threading.Event()
        self.release = threading.Event()

    def length(self) -> int:
        return self._inner.length()

    def read(self, offset: int, size: int, *, cache: bool = True) -> bytes:
        if self.armed:
            self.entered.set()
            assert self.release.wait(JOIN_SECONDS)
        return self._inner.read(offset, size, cache=cache)

    def close(self) -> None:
        self._inner.close()


def test_plan_does_not_hold_the_lock_during_a_read() -> None:
    source = SlowSource(b"abc\ndef\nghi")
    doc = LazyDocument(source, SMALL)
    results: list[bytes] = []

    def save_thread() -> None:
        parts = doc.plan(0, 11, unverified=False)
        results.append(_joined(parts))

    try:
        source.armed = True
        thread = threading.Thread(target=save_thread, name="plan-reader", daemon=True)
        thread.start()
        assert source.entered.wait(JOIN_SECONDS)
        acquired = doc._lock.acquire(timeout=JOIN_SECONDS)
        assert acquired
        doc._lock.release()
        source.release.set()
        thread.join(JOIN_SECONDS)
        assert not thread.is_alive()
        assert results == [b"abc\ndef\nghi"]
    finally:
        source.release.set()
        _closed(doc)


def test_lock_reasons_are_kept_apart() -> None:
    doc = LazyDocument.from_text("abc", SMALL)
    try:
        doc.lock_edits("file changed on disk")
        doc.lock_edits("saving")
        with pytest.raises(EditsLocked, match="saving"):
            doc.replace_range((0, 0), (0, 0), "x")
        doc.unlock_edits("saving")
        with pytest.raises(EditsLocked, match="file changed on disk"):
            doc.replace_range((0, 0), (0, 0), "x")
        doc.unlock_edits("saving")  # a reason that is not held changes nothing
        with pytest.raises(EditsLocked, match="file changed on disk"):
            doc.replace_range((0, 0), (0, 0), "x")
        doc.unlock_edits("file changed on disk")
        doc.replace_range((0, 0), (0, 0), "x")
        assert doc.read_bytes(0, 4) == b"xabc"
    finally:
        _closed(doc)


def test_unlock_without_a_reason_lifts_every_lock() -> None:
    doc = LazyDocument.from_text("abc", SMALL)
    try:
        doc.lock_edits("file changed on disk")
        doc.lock_edits("saving")
        doc.unlock_edits()
        doc.replace_range((0, 0), (0, 0), "x")
        assert doc.read_bytes(0, 4) == b"xabc"
    finally:
        _closed(doc)
