"""Tests for `LineIndex.scan_now`: the inline scan gives the results of the threaded scan."""

from __future__ import annotations

from pathlib import Path

import pytest

from nova_editor.core import BytesSource, LineIndex, SourceChanged
from tests.nova_editor.helpers_view import make_mixed


def _state(index: LineIndex) -> tuple[object, ...]:
    snapshot = index.snapshot()
    ranges = [index.row_range(row) for row in range(snapshot.count)]
    return snapshot, ranges, index.stored_entries(), index.long_row_count, index.long_overflow


@pytest.mark.parametrize("terminator", [b"\n", b"\r\n", b"\r"])
@pytest.mark.parametrize("scan_block", [7, 4096])
@pytest.mark.parametrize("stride", [1, 4, 64])
def test_scan_now_equals_the_threaded_scan(tmp_path: Path, terminator: bytes, scan_block: int, stride: int) -> None:
    data = make_mixed(tmp_path / "f.txt", terminator=terminator, long_chars=500).read_bytes()
    threaded = LineIndex(BytesSource(data), stride=stride, long_line_threshold=64, scan_block=scan_block)
    threaded.start()
    assert threaded.join(10)
    inline = LineIndex(BytesSource(data), stride=stride, long_line_threshold=64, scan_block=scan_block)
    inline.scan_now()
    assert inline.snapshot().complete
    assert _state(inline) == _state(threaded)


def test_scan_now_of_an_empty_source() -> None:
    index = LineIndex(BytesSource(b""))
    index.scan_now()
    assert index.snapshot().complete
    assert index.snapshot().count == 1


def test_scan_now_notifies_and_refuses_a_second_scan() -> None:
    index = LineIndex(BytesSource(b"a\nb\n"))
    calls: list[int] = []
    index.subscribe(lambda: calls.append(1))
    index.scan_now()
    assert calls
    with pytest.raises(RuntimeError, match="already started"):
        index.scan_now()
    with pytest.raises(RuntimeError, match="already started"):
        index.start()


def test_scan_now_after_start_raises() -> None:
    index = LineIndex(BytesSource(b"a\nb\n"))
    index.start()
    index.join(10)
    with pytest.raises(RuntimeError, match="already started"):
        index.scan_now()


def test_scan_now_records_a_source_change() -> None:
    class Empty(BytesSource):
        def read(self, offset: int, size: int, *, cache: bool = True) -> bytes:
            return b""

    index = LineIndex(Empty(b"abc"))
    index.scan_now()
    assert isinstance(index.snapshot().error, SourceChanged)
