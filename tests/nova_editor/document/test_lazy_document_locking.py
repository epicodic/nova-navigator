"""Smaller lock and lifecycle guarantees of `LazyDocument` that the table audit does not see: closed sources, `_newline` and `_line_index`."""

from __future__ import annotations

from pathlib import Path
from typing import Any

from nova_editor.core import BytesSource, ChangeKind, LineIndex
from nova_editor.document._lazy_document import LazyDocument
from tests.nova_editor.document.helpers_lock import TrackedLock

JOIN_SECONDS = 20.0


def test_check_source_of_a_closed_document_is_unchanged(tmp_path: Path) -> None:
    path = tmp_path / "f.txt"
    path.write_bytes(b"a\nb\n" * 100)
    doc = LazyDocument.from_path(path)
    doc.close()
    assert doc.wait_closed(JOIN_SECONDS)
    assert doc.check_source() is ChangeKind.UNCHANGED  # the source is closed: its own check would raise ValueError


class _Watched(LazyDocument):
    """A document that records whether the lock was held at every read of `_line_index` and `_newline`."""

    reads: list[tuple[str, bool]]

    def _held(self) -> bool:
        lock = self.__dict__.get("_lock")
        return isinstance(lock, TrackedLock) and lock.held()

    @property
    def _line_index(self) -> LineIndex:
        self.__dict__.setdefault("reads", []).append(("line_index", self._held()))
        return self.__dict__["_li"]

    @_line_index.setter
    def _line_index(self, value: LineIndex) -> None:
        self.__dict__["_li"] = value

    @property
    def _newline(self) -> Any:
        self.__dict__.setdefault("reads", []).append(("newline", self._held()))
        return self.__dict__.get("_nl")

    @_newline.setter
    def _newline(self, value: Any) -> None:
        self.__dict__["_nl"] = value
        self.__dict__.setdefault("reads", []).append(("newline-write", self._held()))


def _watched() -> _Watched:
    doc = _Watched(BytesSource(b"a\r\nb\r\n"))
    doc.__dict__["_lock"] = TrackedLock()
    doc.__dict__["reads"] = []
    return doc


def test_newline_cache_is_read_and_written_under_the_lock() -> None:
    doc = _watched()
    assert doc.newline == "\r\n"
    assert doc.newline == "\r\n"
    cache = [held for name, held in doc.reads if name.startswith("newline")]
    assert cache
    assert all(cache), doc.reads


def test_subscribe_reads_the_line_index_under_the_lock() -> None:
    doc = _watched()
    doc.reads.clear()
    doc.subscribe(lambda: None)
    index_reads = [held for name, held in doc.reads if name == "line_index"]
    assert index_reads
    assert all(index_reads), doc.reads
