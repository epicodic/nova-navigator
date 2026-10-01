"""Piece, aggregate and PieceTree against a plain-bytes reference (ACT4 design 4.1 to 4.3)."""

from __future__ import annotations

import tracemalloc

import pytest
from hypothesis import given, settings
from hypothesis import strategies as st
from hypothesis.stateful import RuleBasedStateMachine, initialize, invariant, rule

from nova_editor.core import Content, PieceTree, combine, make_piece
from nova_editor.core.piece_tree import _Inner
from nova_editor.core.pieces import EMPTY_AGGREGATE, Aggregate, Piece, aggregate_of_bytes, merge_pieces, tail_chars_of
from tests.nova_editor.core.reference import ALPHABET, Rng, Sources, break_ends, inside_crlf, tail_chars

chunks = st.lists(st.sampled_from(ALPHABET), max_size=8).map(b"".join)


def fold(parts: list[bytes]) -> Aggregate:
    agg = EMPTY_AGGREGATE
    for part in parts:
        agg = combine(agg, aggregate_of_bytes(part))
    return agg


def test_aggregate_associative_and_equal_to_reference() -> None:
    @given(chunks, chunks, chunks)
    @settings(max_examples=300, deadline=None)
    def check(x: bytes, y: bytes, z: bytes) -> None:
        a, b, c = aggregate_of_bytes(x), aggregate_of_bytes(y), aggregate_of_bytes(z)
        assert combine(combine(a, b), c) == combine(a, combine(b, c))
        assert fold([x, y, z]) == aggregate_of_bytes(x + y + z)
        assert fold([x, y, z]).breaks == len(break_ends(x + y + z))

    check()


def test_junction_cr_lf_counts_one_break() -> None:
    assert combine(aggregate_of_bytes(b"a\r"), aggregate_of_bytes(b"\nb")).breaks == 1
    assert combine(aggregate_of_bytes(b"\r"), aggregate_of_bytes(b"\r")).breaks == 2
    assert combine(aggregate_of_bytes(b"\n"), aggregate_of_bytes(b"\n")).breaks == 2
    assert combine(aggregate_of_bytes(b"\n"), aggregate_of_bytes(b"\r")).breaks == 2
    assert combine(EMPTY_AGGREGATE, aggregate_of_bytes(b"\n")) == aggregate_of_bytes(b"\n")


class Harness:
    """A `PieceTree` and a bytes reference that are driven by the same operations and compared after every step."""

    def __init__(self, initial: bytes, fanout: int = 4) -> None:
        self.sources = Sources()
        self.ref = initial
        self.saved: list[tuple[Content, bytes]] = []
        if initial:
            src = self.sources.add(initial)
            self.tree = PieceTree.from_pieces(self.sources, [make_piece(self.sources(src), src, 0, len(initial))], fanout)
        else:
            self.tree = PieceTree(self.sources, fanout)
        self.check()

    def content_of(self, data: bytes) -> Content:
        if not data:
            return Content.from_pieces((), 0)
        src = self.sources.add(data)
        return Content.of([make_piece(self.sources(src), src, 0, len(data))], self.sources)

    def splice(self, start: int, end: int, content: Content, data: bytes) -> None:
        if inside_crlf(self.ref, start) or inside_crlf(self.ref, end):
            with pytest.raises(ValueError, match="CRLF"):
                self.tree.splice(start, end, content)
        else:
            removed = self.tree.splice(start, end, content)
            expected = self.ref[start:end]
            assert removed.length == len(expected)
            assert removed.breaks == len(break_ends(expected))
            assert removed.tail_chars == tail_chars(expected)
            assert b"".join(self.sources(p.src).read(p.a, p.b) for p in removed.pieces) == expected
            self.saved.append((removed, expected))
            self.ref = self.ref[:start] + data + self.ref[end:]
        self.check()

    def insert(self, pos: int, data: bytes) -> None:
        self.splice(pos, pos, self.content_of(data), data)

    def delete(self, start: int, end: int) -> None:
        self.splice(start, end, Content.from_pieces((), 0), b"")

    def replace(self, start: int, end: int, data: bytes) -> None:
        self.splice(start, end, self.content_of(data), data)

    def reinsert(self, pos: int, which: int, end: int | None = None) -> None:
        if not self.saved:
            return
        content, data = self.saved[which % len(self.saved)]
        if end is None:
            end = pos
        self.splice(pos, end, content, data)

    def extract(self, start: int, end: int) -> None:
        if inside_crlf(self.ref, start) or inside_crlf(self.ref, end):
            with pytest.raises(ValueError, match="CRLF"):
                self.tree.content(start, end)
            return
        content = self.tree.content(start, end)
        expected = self.ref[start:end]
        assert (content.length, content.breaks, content.tail_chars) == (len(expected), len(break_ends(expected)), tail_chars(expected))
        self.saved.append((content, expected))
        self.check()

    def check(self) -> None:
        tree, ref = self.tree, self.ref
        tree.check_invariants(deep=True)
        ends = break_ends(ref)
        assert tree.length == len(ref)
        assert tree.breaks == len(ends)
        assert tree.read(0, len(ref)) == ref
        for k, end in enumerate(ends, start=1):
            assert tree.find_break(k) == end, k
        for offset in range(len(ref) + 1):
            location = tree.find_offset(offset)
            if offset == len(ref):
                assert location.piece is None
            else:
                assert location.piece is not None
                assert location.start + location.inner == offset
                assert 0 <= location.inner < location.piece.length
        for piece in tree.pieces():
            assert piece.a < piece.b


def random_bytes(rng: Rng, limit: int) -> bytes:
    return b"".join(rng.choice(ALPHABET) for _ in range(rng.randint(0, limit)))


def run_random(seed: int, steps: int, fanout: int) -> None:
    rng = Rng(seed)
    harness = Harness(random_bytes(rng, 40), fanout)
    for _ in range(steps):
        n = len(harness.ref)
        a = rng.randint(0, n)
        b = rng.randint(a, min(n, a + rng.choice([1, 3, 10, 40])))
        op = rng.choice(["insert", "delete", "replace", "reinsert", "extract", "replace_saved"])
        if op == "insert":
            harness.insert(a, random_bytes(rng, 6))
        elif op == "delete":
            harness.delete(a, b)
        elif op == "replace":
            harness.replace(a, b, random_bytes(rng, 6))
        elif op == "reinsert":
            harness.reinsert(a, rng.randint(0, 999))
        elif op == "replace_saved":
            harness.reinsert(a, rng.randint(0, 999), b)
        else:
            harness.extract(a, b)


@pytest.mark.parametrize("seed", range(8))
def test_fuzz_fanout_4(seed: int) -> None:
    run_random(seed, 250, 4)


@pytest.mark.parametrize(("seed", "fanout"), [(100, 5), (101, 6), (102, 32)])
def test_fuzz_other_fanouts(seed: int, fanout: int) -> None:
    run_random(seed, 200, fanout)


def test_fuzz_grows_deep_tree_and_shrinks_it_again() -> None:
    harness = Harness(b"", 4)
    rng = Rng(7)
    for i in range(150):
        harness.insert(len(harness.ref), random_bytes(rng, 3) + b"x")
        if i % 25 == 0:
            harness.check()
    assert isinstance(harness.tree._root, _Inner)
    assert harness.tree.piece_count > 30
    while harness.ref:
        n = len(harness.ref)
        a = rng.randint(0, n)
        harness.delete(a, min(n, a + rng.randint(1, 20)))
    assert harness.tree.piece_count == 0
    assert harness.tree.length == 0


class TreeMachine(RuleBasedStateMachine):
    """Hypothesis stateful variant of the fuzz test."""

    def __init__(self) -> None:
        super().__init__()
        self.h = Harness(b"", 4)

    @initialize(data=chunks)
    def start(self, data: bytes) -> None:
        self.h = Harness(data, 4)

    @rule(frac=st.floats(0, 1), data=chunks)
    def insert(self, frac: float, data: bytes) -> None:
        self.h.insert(round(frac * len(self.h.ref)), data)

    @rule(x=st.floats(0, 1), y=st.floats(0, 1))
    def delete(self, x: float, y: float) -> None:
        n = len(self.h.ref)
        a, b = sorted((round(x * n), round(y * n)))
        self.h.delete(a, b)

    @rule(x=st.floats(0, 1), y=st.floats(0, 1), data=chunks)
    def replace(self, x: float, y: float, data: bytes) -> None:
        n = len(self.h.ref)
        a, b = sorted((round(x * n), round(y * n)))
        self.h.replace(a, b, data)

    @rule(x=st.floats(0, 1), which=st.integers(0, 50))
    def reinsert(self, x: float, which: int) -> None:
        self.h.reinsert(round(x * len(self.h.ref)), which)

    @rule(x=st.floats(0, 1), y=st.floats(0, 1))
    def extract(self, x: float, y: float) -> None:
        n = len(self.h.ref)
        a, b = sorted((round(x * n), round(y * n)))
        self.h.extract(a, b)

    @invariant()
    def matches_reference(self) -> None:
        assert self.h.tree.read(0, self.h.tree.length) == self.h.ref


TestTreeMachine = TreeMachine.TestCase
TestTreeMachine.settings = settings(max_examples=40, stateful_step_count=25, deadline=None, derandomize=True)


def test_split_between_cr_and_lf_is_rejected_inside_a_piece() -> None:
    h = Harness(b"a\r\nb")
    with pytest.raises(ValueError, match="CRLF"):
        h.tree.split_at(2)
    with pytest.raises(ValueError, match="CRLF"):
        h.tree.splice(2, 3, h.content_of(b"x"))
    with pytest.raises(ValueError, match="CRLF"):
        h.tree.splice(0, 2, h.content_of(b"x"))
    assert h.tree.read(0, 4) == b"a\r\nb"
    assert h.tree.piece_count == 1
    h.tree.split_at(3)
    h.tree.check_invariants(deep=True)


def test_split_between_cr_and_lf_is_rejected_at_a_junction() -> None:
    h = Harness(b"a\rX\nb")
    h.delete(2, 3)
    assert h.tree.breaks == 1
    with pytest.raises(ValueError, match="CRLF"):
        h.tree.split_at(2)
    h.check()


def test_deleting_between_cr_and_lf_merges_rows() -> None:
    h = Harness(b"a\rxyz\nb")
    assert h.tree.breaks == 2
    h.delete(2, 5)
    assert h.ref == b"a\r\nb"
    assert h.tree.breaks == 1
    assert h.tree.find_break(1) == 3


def test_insert_next_to_cr_and_lf() -> None:
    h = Harness(b"a\nb")
    h.insert(1, b"\r")
    assert h.tree.breaks == 1
    h.insert(0, b"\r")
    assert h.tree.breaks == 2
    h.check()


def test_splice_merges_contiguous_pieces_of_one_source() -> None:
    h = Harness(b"abcdef")
    removed = h.tree.splice(2, 4, Content.from_pieces((), 0))
    assert h.tree.piece_count == 2
    h.tree.splice(2, 2, removed)
    assert h.tree.piece_count == 1
    assert h.tree.read(0, 6) == b"abcdef"
    h.tree.check_invariants(deep=True)


def test_content_extraction_does_not_leave_the_tree_split() -> None:
    h = Harness(b"abcdef")
    content = h.tree.content(2, 4)
    assert content.length == 2
    assert h.tree.piece_count == 1


def test_find_break_over_pieces_from_several_sources() -> None:
    h = Harness(b"x\ny\rz\r\nw")
    h.insert(4, b"\r\n\r")
    h.insert(0, b"\n")
    h.check()
    assert [h.tree.find_break(k) for k in range(1, h.tree.breaks + 1)] == break_ends(h.ref)
    with pytest.raises(IndexError):
        h.tree.find_break(0)
    with pytest.raises(IndexError):
        h.tree.find_break(h.tree.breaks + 1)


def test_iter_range_clips_pieces() -> None:
    h = Harness(b"0123456789")
    h.insert(5, b"AB")
    spans = [(piece.src, lo, hi) for piece, lo, hi in h.tree.iter_range(3, 8)]
    assert spans == [(0, 3, 5), (1, 0, 2), (0, 0, 1)]
    assert list(h.tree.iter_range(4, 4)) == []
    assert h.tree.read(3, 8) == b"34AB5"


def test_out_of_range_is_rejected() -> None:
    h = Harness(b"abc")
    with pytest.raises(ValueError, match="outside"):
        h.tree.splice(2, 9, Content.from_pieces((), 0))
    with pytest.raises(ValueError, match="outside"):
        h.tree.find_offset(4)
    with pytest.raises(ValueError, match="fanout"):
        PieceTree(Sources(), fanout=3)


def test_content_tail_chars_spans_pieces() -> None:
    sources = Sources()
    first, second = sources.add(b"x\n\xe2\x82"), sources.add(b"\xac\xc3\xa9")
    pieces = [make_piece(sources(first), first, 0, 4), make_piece(sources(second), second, 0, 3)]
    content = Content.of(pieces, sources)
    assert content.tail_chars == 2
    assert content.breaks == 1
    assert tail_chars_of([], sources) == 0
    assert merge_pieces(pieces[0], pieces[1]) is None


def test_check_invariants_detects_corruption() -> None:
    h = Harness(b"abc\ndef\n" * 20)
    for i in range(10):
        h.insert(i * 3, b"\n")
    root = h.tree._root
    assert isinstance(root, _Inner)
    h.tree.check_invariants(deep=True)
    original = root.agg
    root.agg = Aggregate(original.length + 1, original.breaks, original.first_is_lf, original.last_is_cr)
    with pytest.raises(AssertionError, match="stale aggregate"):
        h.tree.check_invariants()
    root.agg = original
    root.children[0].count += 1
    with pytest.raises(AssertionError, match="stale count"):
        h.tree.check_invariants()


def test_check_invariants_deep_detects_a_wrong_piece() -> None:
    sources = Sources()
    src = sources.add(b"a\nb")
    wrong = Piece(src, 0, 3, 2, 0, False, False)
    tree = PieceTree.from_pieces(sources, [wrong])
    tree.check_invariants()
    with pytest.raises(AssertionError, match="break count"):
        tree.check_invariants(deep=True)


def test_piece_memory_stays_below_120_bytes_per_piece() -> None:
    sources = Sources()
    src = sources.add(b"x" * 10)
    count = 100_000
    tracemalloc.start()
    try:
        before = tracemalloc.take_snapshot()
        tree = PieceTree.from_pieces(sources, (Piece(src, 2 * i, 2 * i + 1, 0, 0, False, False) for i in range(count)), fanout=32)
        after = tracemalloc.take_snapshot()
    finally:
        tracemalloc.stop()
    used = sum(stat.size_diff for stat in after.compare_to(before, "filename"))
    assert tree.piece_count == count
    assert used / count <= 120, used / count


def test_incrementally_built_tree_memory_stays_below_120_bytes_per_piece() -> None:
    sources = Sources()
    src = sources.add(b"x" * 10)
    count = 8_000
    tree = PieceTree(sources)
    tracemalloc.start()
    try:
        before = tracemalloc.take_snapshot()
        for i in range(count):
            tree.splice(tree.length, tree.length, Content.from_pieces([Piece(src, 2 * i % 8, 2 * i % 8 + 1, 0, 0, False, False)], 1))
        after = tracemalloc.take_snapshot()
    finally:
        tracemalloc.stop()
    used = sum(stat.size_diff for stat in after.compare_to(before, "filename"))
    assert tree.piece_count == count
    assert used / count <= 120, used / count
