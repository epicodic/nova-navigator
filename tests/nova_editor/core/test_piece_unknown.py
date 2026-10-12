"""Aggregate laws and piece rules for unknown pieces (ACT1 design 4 and 11.2)."""

from __future__ import annotations

from itertools import pairwise

import pytest

from nova_editor.core.pieces import (
    EMPTY_AGGREGATE,
    Aggregate,
    Content,
    Piece,
    aggregate_of_bytes,
    combine,
    make_unknown_piece,
    merge_pieces,
    tail_chars_of,
)

from .reference import ALPHABET, FakeSource, Rng, break_ends


class SpySource(FakeSource):
    """`FakeSource` that records the size of every read."""

    def __init__(self, data: bytes) -> None:
        super().__init__(data)
        self.sizes: list[int] = []

    def read(self, a: int, b: int) -> bytes:
        self.sizes.append(b - a)
        return super().read(a, b)


def chunk_aggregate(data: bytes, unknown: bool) -> Aggregate:
    if not data:
        return EMPTY_AGGREGATE
    if unknown:
        return Aggregate(len(data), 0, data[:1] == b"\n", data[-1:] == b"\r", 1, 0, True, True)
    return aggregate_of_bytes(data)


def brute_force(chunks: list[tuple[bytes, bool]]) -> Aggregate:
    """Aggregate of the run computed from first principles (no combine)."""
    items = [(data, unk) for data, unk in chunks if data]
    if not items:
        return EMPTY_AGGREGATE
    breaks = sum(len(break_ends(data)) for data, unk in items if not unk)
    for (ld, lu), (rd, ru) in pairwise(items):
        if not lu and not ru and ld[-1:] == b"\r" and rd[:1] == b"\n":
            breaks -= 1
    first_unknown_at = next((i for i, (_, unk) in enumerate(items) if unk), None)
    if first_unknown_at is None:
        lead = breaks
    else:
        lead = brute_force(items[:first_unknown_at]).breaks if first_unknown_at else 0
    data_all = b"".join(d for d, _ in items)
    return Aggregate(
        len(data_all),
        breaks,
        data_all[:1] == b"\n",
        data_all[-1:] == b"\r",
        sum(1 for _, unk in items if unk),
        lead,
        items[0][1],
        items[-1][1],
    )


def random_chunks(rng: Rng, count: int) -> list[tuple[bytes, bool]]:
    chunks = []
    for _ in range(count):
        data = b"".join(rng.choice(ALPHABET) for _ in range(rng.randint(1, 4)))
        chunks.append((data, rng.randint(0, 2) == 0))
    return chunks


@pytest.mark.parametrize("seed", range(200))
def test_combine_is_associative_and_equals_the_brute_force_fold(seed: int) -> None:
    rng = Rng(seed)
    chunks = random_chunks(rng, rng.randint(3, 8))
    aggs = [chunk_aggregate(d, u) for d, u in chunks]
    left = EMPTY_AGGREGATE
    for agg in aggs:
        left = combine(left, agg)
    right = EMPTY_AGGREGATE
    for agg in reversed(aggs):
        right = combine(agg, right)
    assert left == right == brute_force(chunks)
    assert left.lead_breaks <= left.breaks
    if left.unknown == 0:
        assert left.lead_breaks == left.breaks


def test_known_next_to_unknown_counts_the_cr_once_and_the_lf_once() -> None:
    cr = chunk_aggregate(b"a\r", False)
    lf_unknown = chunk_aggregate(b"\nb", True)
    assert combine(cr, lf_unknown).breaks == 1  # the CR counts, the LF does not subtract
    lf_known = chunk_aggregate(b"\nb", False)
    assert combine(cr, lf_known).breaks == 1  # CRLF is one break
    cr_unknown = chunk_aggregate(b"a\r", True)
    assert combine(cr_unknown, lf_known).breaks == 1  # the known LF counts, nothing subtracts
    assert combine(cr_unknown, lf_known).lead_breaks == 0


def test_unknown_piece_has_honest_flags_and_zero_facts() -> None:
    source = FakeSource(b"x\r\ny")
    piece = make_unknown_piece(source, 0, 2, 4)
    assert piece == Piece(0, 2, 4, 0, 0, True, False, True)
    with pytest.raises(ValueError, match="empty"):
        make_unknown_piece(source, 0, 2, 2)


def test_merge_rules() -> None:
    source = FakeSource(b"ab\ncd\nef")
    u1, u2 = make_unknown_piece(source, 0, 0, 3), make_unknown_piece(source, 0, 3, 6)
    merged = merge_pieces(u1, u2)
    assert merged is not None
    assert merged.unknown
    assert (merged.a, merged.b) == (0, 6)
    known = Piece(0, 6, 8, 0, 2, False, False)
    assert merge_pieces(u2, known) is None
    assert merge_pieces(known, u1) is None
    assert merge_pieces(u1, make_unknown_piece(source, 0, 4, 6)) is None  # not contiguous


def test_content_from_pieces_reports_unknown_and_lower_bound() -> None:
    source = FakeSource(b"a\nb\nc")
    content = Content.from_pieces([Piece(0, 0, 2, 1, 0, False, False), make_unknown_piece(source, 0, 2, 5)], 0)
    assert (content.unknown, content.breaks, content.lead_breaks) == (1, 1, 1)
    assert content.aggregate == Aggregate(5, 1, False, False, 1, 1, False, True)


def test_tail_chars_is_capped_only_when_an_unknown_piece_is_present() -> None:
    data = b"x" * (1 << 20)
    source = SpySource(data)
    unknown = make_unknown_piece(source, 0, 0, len(data))
    source.sizes.clear()
    tail_chars_of([unknown], lambda _src: source)
    assert sum(source.sizes) <= 64 * 1024


def test_tail_chars_reads_a_known_piece_whole() -> None:
    data = b"x" * (1 << 20)
    source = SpySource(data)
    known = Piece(0, 0, len(data), 0, 0, False, False)
    assert tail_chars_of([known], lambda _src: source) == len(data)
    assert sum(source.sizes) == len(data)
