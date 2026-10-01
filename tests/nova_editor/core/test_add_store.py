"""Tests for `AddStore`: appends, reads, the sparse row index and thread safety."""

from __future__ import annotations

import queue
import threading

import pytest

from nova_editor.core import AddStore, Piece, make_piece, merge_pieces

from .reference import ALPHABET, BREAK, FakeSource, Rng, break_ends


def _random_data(rng: Rng, max_atoms: int) -> bytes:
    return b"".join(rng.choice(ALPHABET) for _ in range(rng.randint(1, max_atoms)))


def _check_segments(store: AddStore, expected: dict[int, bytearray]) -> None:
    """Every row query of every segment equals the regex reference over the segment's bytes."""
    for segment, content in expected.items():
        data = bytes(content)
        reference = FakeSource(data)
        assert store.length(segment) == len(data)
        assert store.read(segment, 0, len(data) + 5) == data
        view = store.segment(segment)
        for offset in range(len(data) + 1):
            if 0 < offset < len(data) and data[offset - 1 : offset + 1] == b"\r\n":
                continue
            assert view.row_of(offset) == reference.row_of(offset), (data, offset)
        for row in range(len(break_ends(data)) + 3):
            assert view.row_start(row) == reference.row_start(row), (data, row)


@pytest.mark.parametrize("seed", range(12))
@pytest.mark.parametrize(("tail_limit", "stride"), [(8, 2), (8, 1), (13, 3), (64, 2)])
def test_random_appends_and_reads(seed: int, tail_limit: int, stride: int) -> None:
    rng = Rng(seed)
    store = AddStore(tail_limit=tail_limit, stride=stride)
    expected: dict[int, bytearray] = {}
    for _ in range(40):
        data = _random_data(rng, 6)
        segment, a, b = store.append(data)
        assert b - a == len(data)
        content = expected.setdefault(segment, bytearray())
        assert a == len(content)
        content.extend(data)
        assert store.read(segment, a, b) == data
        if rng.randint(0, 9) < 3:
            _check_segments(store, expected)
    _check_segments(store, expected)
    assert store.segment_count == len(expected)


def test_tail_is_continued_until_the_limit_then_sealed() -> None:
    store = AddStore(tail_limit=8, stride=2)
    assert store.append(b"abc") == (1, 0, 3)
    assert store.append(b"de") == (1, 3, 5)
    assert store.append(b"fgh") == (1, 5, 8)
    assert store.append(b"i") == (2, 0, 1)
    assert store.read(1, 0, 100) == b"abcdefgh"


def test_large_append_is_its_own_segment_and_tail_stays_open() -> None:
    store = AddStore(tail_limit=8, stride=2)
    assert store.append(b"ab") == (1, 0, 2)
    assert store.append(b"x" * 8) == (2, 0, 8)
    assert store.append(b"cd") == (1, 2, 4)
    assert store.read(2, 0, 8) == b"x" * 8


def test_continuing_append_lets_the_caller_merge_pieces() -> None:
    store = AddStore(tail_limit=16, stride=2)
    first = store.append(b"a\r")
    second = store.append(b"\nb")
    assert first[0] == second[0]
    left = make_piece(store.segment(first[0]), first[0], first[1], first[2])
    right = Piece(second[0], second[1], second[2], 1, 0, True, False)
    merged = merge_pieces(left, right)
    assert merged is not None
    assert make_piece(store.segment(first[0]), first[0], first[1], second[2]) == merged


@pytest.mark.parametrize("stride", [1, 2, 3])
def test_crlf_across_appends_is_one_break(stride: int) -> None:
    store = AddStore(tail_limit=64, stride=stride)
    data = b""
    for chunk in [b"a\r", b"\n", b"\r", b"\r", b"\n", b"b\r\n", b"\r", b"\n\n"]:
        store.append(chunk)
        data += chunk
        reference = FakeSource(data)
        view = store.segment(1)
        rows = range(len(reference.ends) + 2)
        assert [view.row_start(r) for r in rows] == [reference.row_start(r) for r in rows]
        assert view.row_of(len(data)) == len(reference.ends)


def test_breaks_between_matches_the_piece_rule() -> None:
    store = AddStore(tail_limit=64, stride=2)
    data = b"a\nb\r\nc\rd\n"
    store.append(data)
    reference = FakeSource(data)
    view = store.segment(1)
    for a in range(len(data)):
        for b in range(a + 1, len(data) + 1):
            if any(data[x - 1 : x + 1] == b"\r\n" for x in (a, b) if 0 < x < len(data)):
                continue
            assert view.breaks_between(a, b) == reference.breaks_between(a, b)
            assert view.breaks_between(a, b) == len(BREAK.findall(data[a:b]))


def test_sealed_segment_is_immutable() -> None:
    store = AddStore(tail_limit=4, stride=2)
    store.append(b"abc")
    store.append(b"defg")
    store.append(b"xy")
    assert store.read(1, 0, 10) == b"abc"
    assert store.length(1) == 3
    store.append(b"z")
    assert store.read(1, 0, 10) == b"abc"


def test_errors() -> None:
    store = AddStore()
    with pytest.raises(ValueError, match="empty"):
        store.append(b"")
    with pytest.raises(IndexError):
        store.read(1, 0, 1)
    with pytest.raises(ValueError, match="positive"):
        AddStore(tail_limit=0)
    store.append(b"a")
    assert store.read(1, 5, 9) == b""
    assert store.row_start(1, 1) is None


def test_reader_thread_sees_consistent_bytes_while_main_appends() -> None:
    rng = Rng(7)
    store = AddStore(tail_limit=8, stride=2)
    published: queue.Queue[tuple[int, int, int, bytes] | None] = queue.Queue()
    barrier = threading.Barrier(2)
    failures: list[str] = []

    def reader() -> None:
        seen: list[tuple[int, int, int, bytes]] = []
        barrier.wait(timeout=10)
        while True:
            item = published.get(timeout=10)
            if item is None:
                break
            seen.append(item)
            for segment, a, b, data in seen:  # earlier ranges must stay intact while the tail grows
                if store.read(segment, a, b) != data:
                    failures.append(f"read mismatch in segment {segment} [{a}, {b})")
                if store.breaks_between(segment, a, b) < 0:
                    failures.append("negative break count")

    thread = threading.Thread(target=reader)
    thread.start()
    barrier.wait(timeout=10)
    for _ in range(300):
        data = _random_data(rng, 4)
        segment, a, b = store.append(data)
        published.put((segment, a, b, data))
    published.put(None)
    thread.join(timeout=30)
    assert not thread.is_alive()
    assert failures == []
