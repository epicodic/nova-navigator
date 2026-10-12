"""`LazyDocument.content_of_bytes` and `content_between`: piece references for byte editing and copying (ACT5 commit 1)."""

from __future__ import annotations

import pytest

from nova_editor.core.pieces import Content
from nova_editor.document._lazy_document import LazyDocument, RowUnavailable
from tests.nova_editor.document.test_lazy_edit import SMALL


def test_content_of_bytes_holds_the_bytes() -> None:
    doc = LazyDocument.from_bytes(b"abc", SMALL)
    content = doc.content_of_bytes(b"XYZ")
    assert content.length == 3
    assert content.breaks == 0
    doc.splice_bytes(1, 1, content)
    assert doc.read_bytes(0, doc.length) == b"aXYZbc"
    doc.close()


def test_content_of_bytes_counts_breaks_and_tail() -> None:
    doc = LazyDocument.from_bytes(b"abc", SMALL)
    content = doc.content_of_bytes(b"x\nyz")
    assert content.length == 4
    assert content.breaks == 1
    assert content.tail_chars == 2
    doc.close()


def test_content_of_bytes_empty_is_the_empty_content() -> None:
    doc = LazyDocument.from_bytes(b"abc", SMALL)
    content = doc.content_of_bytes(b"")
    assert isinstance(content, Content)
    assert content.length == 0
    assert content.pieces == ()
    doc.close()


def test_content_of_bytes_on_a_closed_document_is_refused() -> None:
    doc = LazyDocument.from_bytes(b"abc", SMALL)
    doc.close()
    assert doc.wait_closed(5.0)
    with pytest.raises(RowUnavailable):
        doc.content_of_bytes(b"x")


def test_content_between_returns_the_bytes() -> None:
    doc = LazyDocument.from_bytes(b"hello world", SMALL)
    content = doc.content_between(2, 8)
    assert content.length == 6
    assert doc._table.content_bytes(content, 0, content.length) == b"llo wo"
    doc.close()


def test_content_between_is_a_valid_splice_source() -> None:
    doc = LazyDocument.from_bytes(b"hello world", SMALL)
    content = doc.content_between(0, 5)
    doc.splice_bytes(11, 11, content)
    assert doc.read_bytes(0, doc.length) == b"hello worldhello"
    doc.close()


def test_content_between_empty_and_whole() -> None:
    doc = LazyDocument.from_bytes(b"abc\ndef", SMALL)
    assert doc.content_between(3, 3).length == 0
    whole = doc.content_between(0, doc.length)
    assert whole.length == 7
    assert whole.breaks == 1
    assert whole.tail_chars == 3
    doc.close()


def test_content_between_does_not_read_the_range_except_the_tail(monkeypatch: pytest.MonkeyPatch) -> None:
    reads: list[tuple[int, int]] = []
    doc = LazyDocument.from_bytes(b"ab\n" + b"c" * 40 + b"\n" + b"d" * 10, SMALL)
    source = doc._table._source
    original = source.read

    def logged(start: int, end: int, *, cache: bool = True) -> bytes:
        reads.append((start, end))
        return original(start, end, cache=cache)

    monkeypatch.setattr(source, "read", logged)
    content = doc.content_between(0, doc.length)
    assert content.tail_chars == 10
    assert sum(end - start for start, end in reads) <= 10 + 16
    doc.close()


def test_content_between_takes_the_crlf_halves_along() -> None:
    doc = LazyDocument.from_bytes(b"a\r\nb", SMALL)
    inside_start = doc.content_between(2, 4)  # starts between CR and LF
    assert inside_start.length == 3
    inside_end = doc.content_between(0, 2)  # ends between CR and LF
    assert inside_end.length == 3
    assert doc._table.content_bytes(inside_start, 0, 3) == b"\r\nb"
    assert doc._table.content_bytes(inside_end, 0, 3) == b"a\r\n"
    doc.close()


def test_content_between_rejects_bad_ranges() -> None:
    doc = LazyDocument.from_bytes(b"abc", SMALL)
    with pytest.raises(ValueError, match="invalid byte range"):
        doc.content_between(2, 1)
    with pytest.raises(ValueError, match="invalid byte range"):
        doc.content_between(-1, 2)
    with pytest.raises(ValueError, match="beyond the document"):
        doc.content_between(0, 4)
    doc.close()


def test_content_between_works_on_an_unscanned_document() -> None:
    data = b"line\n" * 4000
    doc = LazyDocument.from_bytes(data, SMALL)
    content = doc.content_between(len(data) - 12, len(data) - 2)
    assert content.length == 10
    assert doc._table.content_bytes(content, 0, 10) == data[-12:-2]
    doc.close()


def test_content_between_on_a_closed_document_is_refused() -> None:
    doc = LazyDocument.from_bytes(b"abc", SMALL)
    doc.close()
    assert doc.wait_closed(5.0)
    with pytest.raises(RowUnavailable):
        doc.content_between(0, 1)
