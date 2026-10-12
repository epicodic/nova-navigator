"""The in-place save of a sparse 8 GiB file: no copy of the file, no scan, no temp file (ACT3 design 12 slice 7). Skipped when the file system has no sparse files."""

from __future__ import annotations

import os
import time
import tracemalloc
from pathlib import Path

import pytest

from nova_editor.core import PreadSource, Redirect
from nova_editor.document import LazyDocument
from tests.nova_editor.core.save_helpers import recording_io
from tests.nova_editor.document.helpers_save import content_of, run_in_place

SIZE = 8 * 1024**3
AT = 5 * 1024**3
MIB = 1024 * 1024


def make_sparse(tmp_path: Path) -> Path:
    path = tmp_path / "sparse.bin"
    with open(path, "wb") as handle:
        handle.truncate(SIZE)
    if os.stat(path).st_blocks * 512 > 16 * MIB:
        path.unlink()
        pytest.skip("the file system does not support sparse files")
    return path


def test_one_byte_in_an_8_gib_sparse_file(tmp_path: Path) -> None:
    path = make_sparse(tmp_path)
    before = os.stat(path)
    doc = LazyDocument.from_path(path, autostart=False)
    try:
        removed = doc.splice_bytes(AT, AT + 1, content_of(doc, b"X")).removed
        assert removed is not None
        events: list[str] = []
        tracemalloc.start()
        started = time.monotonic()
        try:
            result = run_in_place(doc, path, io=recording_io(events))
        finally:
            elapsed = time.monotonic() - started
            peak = tracemalloc.get_traced_memory()[1]
            tracemalloc.stop()
        after = os.stat(path)
        assert elapsed < 1.0
        assert peak < 8 * MIB
        assert after.st_blocks * 512 < 16 * MIB
        assert after.st_ino == before.st_ino
        assert after.st_size == SIZE
        assert "mkstemp" not in events
        assert "replace" not in events
        assert result.written == 1
        with open(path, "rb") as handle:
            handle.seek(AT - 1)
            assert handle.read(3) == b"\0X\0"
        assert doc._table.has_unknown  # nothing was scanned or filled
    finally:
        doc.close()


def test_save_undo_save_again(tmp_path: Path) -> None:
    path = make_sparse(tmp_path)
    doc = LazyDocument.from_path(path, autostart=False)
    try:
        removed = doc.splice_bytes(AT, AT + 1, content_of(doc, b"X")).removed
        assert removed is not None
        result = run_in_place(doc, path)
        source = doc._source
        assert isinstance(source, PreadSource)
        redirected = Redirect(result.copies, source, doc._add_store).apply(removed)
        doc.splice_bytes(AT, AT + 1, redirected)  # undo
        again = run_in_place(doc, path)
        assert again.written == 1
        with open(path, "rb") as handle:
            handle.seek(AT)
            assert handle.read(1) == b"\0"
        assert os.stat(path).st_blocks * 512 < 16 * MIB
    finally:
        doc.close()
