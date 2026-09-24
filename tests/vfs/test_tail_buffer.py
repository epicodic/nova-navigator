from __future__ import annotations

from nova_navigator.vfs.tail_buffer import TailBuffer


def test_tail_buffer_keeps_all_bytes_below_limit() -> None:
    tail = TailBuffer(10)
    tail.append(b"abc")
    tail.append(b"def")
    assert tail.text() == "abcdef"


def test_tail_buffer_keeps_only_newest_bytes() -> None:
    tail = TailBuffer(4)
    tail.append(b"abcdef")
    tail.append(b"gh")
    assert tail.text() == "efgh"


def test_tail_buffer_replaces_invalid_utf8() -> None:
    tail = TailBuffer(10)
    tail.append(b"a\xffb")
    assert tail.text() == "a�b"
