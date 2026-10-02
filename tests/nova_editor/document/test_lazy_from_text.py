"""`LazyDocument.from_text` / `from_bytes` and the synchronous scan limit (ACT4 Task 5)."""

from __future__ import annotations

import re

import pytest

from nova_editor.core import BytesSource
from nova_editor.core.text_width import SURROGATE_ESCAPE
from nova_editor.document._lazy_config import LazyConfig
from nova_editor.document._lazy_document import LazyDocument

_BREAK = re.compile(rb"\r\n|\r|\n")


def _rows(data: bytes) -> list[bytes]:
    """Reference rows: the data split at every terminator (terminators removed)."""
    return _BREAK.split(data)


@pytest.mark.parametrize(
    "data",
    [b"", b"a", b"a\n", b"a\nb", b"a\r\nb\r\n", b"a\rb\rc", b"a\r\nb\nc\rd", b"\n", b"\r\n", b"\n\n\n", b"\r", b"x\r"],
    ids=repr,
)
def test_from_bytes_has_exact_counts_immediately(data: bytes) -> None:
    doc = LazyDocument.from_bytes(data)
    try:
        snap = doc.snapshot()
        assert snap.complete
        assert snap.count == len(_BREAK.findall(data)) + 1
        assert doc.line_count == snap.count
        assert doc.length == len(data)
        assert not doc.is_growing()
        for row, expected in enumerate(_rows(data)):
            assert doc.get_line(row) == expected.decode("utf-8", SURROGATE_ESCAPE)
        assert doc.read_all(10) == data.decode("utf-8", SURROGATE_ESCAPE)
    finally:
        doc.close()
        assert doc.wait_closed(10)


def test_from_text_round_trips_and_reports_ranges() -> None:
    text = "héllo\r\nwörld\nlast"
    doc = LazyDocument.from_text(text)
    try:
        assert doc.line_count == 3
        assert doc.get_line(0) == "héllo"
        assert doc.get_line(1) == "wörld"
        assert doc.get_line(2) == "last"
        assert doc.newline == "\r\n"
        found = doc.row_at_offset(8)
        assert found is not None
        assert found[0] == 1
        assert doc.length == len(text.encode())
    finally:
        doc.close()


def test_empty_text_is_one_empty_row() -> None:
    doc = LazyDocument.from_text("")
    try:
        assert doc.line_count == 1
        assert doc.get_line(0) == ""
        assert doc.length == 0
    finally:
        doc.close()


def test_bom_is_content() -> None:
    doc = LazyDocument.from_text("﻿abc\nd")
    try:
        assert doc.get_line(0) == "﻿abc"
        assert doc.length == 3 + 3 + 1 + 1
    finally:
        doc.close()


def test_invalid_bytes_decode_with_surrogate_escape_and_keep_their_length() -> None:
    data = b"ok\xff\xfe\nbad\xe2\x82\nz"
    doc = LazyDocument.from_bytes(data)
    try:
        assert doc.line_count == 3
        assert doc.length == len(data)
        assert doc.get_line(0) == "ok\udcff\udcfe"
        assert doc.get_line(1) == "bad\udce2\udc82"
        assert doc.read_all(100).encode("utf-8", SURROGATE_ESCAPE) == data
    finally:
        doc.close()


def test_from_text_with_lone_surrogate_round_trips_bytes() -> None:
    doc = LazyDocument.from_text("a\udcffb")
    try:
        assert doc.length == 3
        assert doc.get_line(0) == "a\udcffb"
    finally:
        doc.close()


def test_sources_up_to_the_limit_are_scanned_on_construction() -> None:
    data = b"row\n" * 50
    doc = LazyDocument(BytesSource(data), LazyConfig(sync_scan_limit=len(data)))
    try:
        assert doc.snapshot().complete
        assert doc.line_count == 51
    finally:
        doc.close()


def test_sources_above_the_limit_use_the_background_scan() -> None:
    data = b"row\n" * 50
    doc = LazyDocument(BytesSource(data), LazyConfig(sync_scan_limit=len(data) - 1))
    try:
        assert doc.wait_indexed(10)
        assert doc.line_count == 51
    finally:
        doc.close()


def test_sync_scan_does_not_run_without_autostart_and_start_scan_still_works() -> None:
    doc = LazyDocument(BytesSource(b"a\nb\n"), LazyConfig(), autostart=False)
    try:
        assert not doc.snapshot().complete
        doc.start_scan()
        assert doc.wait_indexed(10)
        assert doc.line_count == 3
    finally:
        doc.close()


def test_default_sync_scan_limit_is_one_mebibyte() -> None:
    assert LazyConfig().sync_scan_limit == 1_048_576


def test_close_after_a_synchronous_scan_keeps_snapshot_and_closes_the_source() -> None:
    doc = LazyDocument.from_bytes(b"a\r")
    doc.close()
    assert doc.wait_closed(10)
    assert doc.snapshot().complete
    assert doc.line_count == 2
