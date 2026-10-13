"""REQ-3: binary detection looks for a NUL byte in the first 64 KiB."""

from __future__ import annotations

from collections.abc import Callable

from nova_editor.core.binary_detect import HEAD_LIMIT, head_is_binary
from nova_editor.core.byte_source import ChangeKind, SourceChanged
from nova_editor.core.memory_source import BytesSource


def reader_of(data: bytes) -> Callable[[int, int], bytes]:
    return lambda offset, size: data[offset : offset + size]


def test_limit_is_64_kib() -> None:
    assert HEAD_LIMIT == 65536


def test_nul_at_offset_65535_is_binary() -> None:
    data = b"a" * 65535 + b"\x00" + b"a" * 10
    assert head_is_binary(reader_of(data)) is True


def test_first_nul_at_65536_is_text() -> None:
    data = b"a" * 65536 + b"\x00"
    assert head_is_binary(reader_of(data)) is False


def test_nul_at_offset_zero_is_binary() -> None:
    assert head_is_binary(reader_of(b"\x00abc")) is True


def test_empty_is_text() -> None:
    assert head_is_binary(reader_of(b"")) is False


def test_short_file_with_a_nul_is_binary() -> None:
    assert head_is_binary(reader_of(b"abc\x00def")) is True


def test_no_nul_is_text() -> None:
    assert head_is_binary(reader_of(b"hello\nworld\n")) is False


def test_high_bytes_without_nul_are_text() -> None:
    assert head_is_binary(reader_of(bytes(range(1, 256)) * 4)) is False


def test_one_read_from_zero_of_the_limit() -> None:
    calls: list[tuple[int, int]] = []

    def read(offset: int, size: int) -> bytes:
        calls.append((offset, size))
        return b"abc"

    head_is_binary(read)
    assert calls == [(0, 65536)]


def test_a_longer_answer_is_cut_to_the_limit() -> None:
    data = b"a" * 65536 + b"\x00"
    assert head_is_binary(lambda _offset, _size: data) is False


def test_source_changed_means_text() -> None:
    def read(_offset: int, _size: int) -> bytes:
        raise SourceChanged("gone", ChangeKind.TRUNCATED)

    assert head_is_binary(read) is False


def test_oserror_means_text() -> None:
    def read(_offset: int, _size: int) -> bytes:
        raise OSError("io")

    assert head_is_binary(read) is False


def test_works_with_a_bytes_source_reader() -> None:
    assert head_is_binary(BytesSource(b"ab\x00").read) is True
    assert head_is_binary(BytesSource(b"abc").read) is False
