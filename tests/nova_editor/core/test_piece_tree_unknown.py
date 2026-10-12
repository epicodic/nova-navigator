"""Reference fuzz of the tree with unknown pieces beyond a random scan frontier, fanout 4 (ACT1 design 11.1, 11.3)."""

from __future__ import annotations

import bisect

import pytest

from nova_editor.core.piece_tree import _UNKNOWN, PieceTree, _Leaf
from nova_editor.core.pieces import Content, Piece, RowNotIndexed, make_piece, make_unknown_piece

from .reference import ALPHABET, FakeSource, Rng, break_ends, inside_crlf

ORIGINALS: dict[str, bytes] = {
    "lf": b"alpha\nbeta\n\ngamma\ndelta\nepsilon\nzeta\n",
    "crlf": b"alpha\r\nbeta\r\n\r\ngamma\r\ndelta\r\nepsilon\r\n",
    "cr": b"alpha\rbeta\r\rgamma\rdelta\repsilon\r",
    "mixed": b"a\r\nb\nc\rd\r\r\n\n\re\xff\xe2\x82\xac\r\nf\t\n",
    "no-trailing": b"alpha\nbeta\r\ngamma\rlast row without newline",
    "empty": b"",
}


class Frontier(FakeSource):
    """The original as the row oracle sees it mid-scan: nothing at or beyond `frontier` can be asked."""

    def __init__(self, data: bytes) -> None:
        super().__init__(data)
        self.frontier = 0
        self.row_calls = 0

    def row_of(self, offset: int) -> int:
        self.row_calls += 1
        if offset >= self.frontier:
            raise RowNotIndexed(offset)
        return super().row_of(offset)

    def row_start(self, row: int) -> int | None:
        found = super().row_start(row)
        return None if found is None or found > self.frontier else found

    def breaks_between(self, a: int, b: int) -> int:
        return self.row_of(b) - self.row_of(a)


class UnknownHarness:
    """A tree over an original with a scan frontier, mirrored by a plain bytes model."""

    def __init__(self, original: bytes, seed: int, fanout: int = 4) -> None:
        self.rng = Rng(seed)
        self.original = original
        self.truth = FakeSource(original)
        self.frontier_source = Frontier(original)
        self.adds: list[FakeSource] = []
        self.starts = [0, *break_ends(original)]
        length = len(original)
        frontier = self.rng.choice(self.starts)
        pieces: list[Piece] = []
        if frontier > 0:
            pieces.append(make_piece(self.truth, 0, 0, frontier))
        if frontier < length:
            pieces.append(make_unknown_piece(self.frontier_source, 0, frontier, length))
        self.frontier_source.frontier = frontier
        self.tree = PieceTree.from_pieces(self.source_of, pieces, fanout=fanout)
        self.ref = original
        self.saved: list[tuple[Content, bytes]] = []

    def source_of(self, src: int) -> FakeSource:
        return self.frontier_source if src == 0 else self.adds[src - 1]

    @property
    def frontier(self) -> int:
        return self.frontier_source.frontier

    def _boundary(self) -> int:
        while True:
            offset = self.rng.randint(0, len(self.ref))
            if not inside_crlf(self.ref, offset):
                return offset

    def _range(self) -> tuple[int, int]:
        a, b = self._boundary(), self._boundary()
        return (a, b) if a <= b else (b, a)

    def _add_content(self) -> tuple[Content, bytes]:
        data = b"".join(self.rng.choice(ALPHABET) for _ in range(self.rng.randint(1, 3)))
        self.adds.append(FakeSource(data))
        src = len(self.adds)
        return Content.from_pieces([make_piece(self.adds[-1], src, 0, len(data))], 0), data

    def _splice(self, start: int, end: int, content: Content, data: bytes) -> None:
        removed = self.tree.splice(start, end, content)
        if end > start:
            self.saved.append((removed, self.ref[start:end]))
        self.ref = self.ref[:start] + data + self.ref[end:]

    def insert(self) -> None:
        offset = self._boundary()
        content, data = self._add_content()
        self._splice(offset, offset, content, data)

    def replace(self) -> None:
        start, end = self._range()
        if self.rng.randint(0, 1):
            content, data = self._add_content()
        else:
            content, data = Content.from_pieces([], 0), b""
        self._splice(start, end, content, data)

    def original_range(self) -> None:
        if not self.original:
            return
        while True:
            x, y = sorted((self.rng.choice([*self.starts, len(self.original)]), self.rng.randint(0, len(self.original))))
            if x < y and not inside_crlf(self.original, y):
                break
        if y <= self.frontier:
            piece = make_piece(self.truth, 0, x, y)
        else:
            piece = make_unknown_piece(self.frontier_source, 0, x, y)
        start, end = self._range()
        self._splice(start, end, Content.from_pieces([piece], 0), self.original[x:y])

    def resplice(self) -> None:
        if not self.saved:
            return
        content, data = self.saved[self.rng.randint(0, len(self.saved) - 1)]
        start, end = self._range()
        self._splice(start, end, content, data)

    def advance(self) -> None:
        later = [s for s in self.starts if s > self.frontier]
        if not later:
            return
        self.frontier_source.frontier = later[self.rng.randint(0, min(2, len(later) - 1))]
        self.fill()

    def fill(self) -> None:
        tree = self.tree
        while (found := tree.first_unknown()) is not None:
            index, piece, start = found
            if piece.src != 0 or piece.a >= self.frontier:
                break
            if piece.b > self.frontier:
                tree.split_at(start + self.frontier - piece.a)
                found = tree.first_unknown()
                assert found is not None
                index, piece, start = found
            known = make_piece(self.truth, 0, piece.a, piece.b)
            tree.replace_piece(index, known)
            # Splicing the piece onto itself merges it with contiguous known neighbours.
            tree.splice(start, start + known.length, Content.from_pieces([known], 0))

    def random_step(self) -> None:
        step = self.rng.randint(0, 9)
        if step <= 1:
            self.insert()
        elif step <= 3:
            self.replace()
        elif step <= 5:
            self.original_range()
        elif step <= 6:
            self.resplice()
        else:
            self.advance()
        self.check()

    def check(self) -> None:
        tree, ref = self.tree, self.ref
        tree.check_invariants(deep=True)
        assert tree.length == len(ref)
        assert tree.read(0, len(ref)) == ref
        true_ends = break_ends(ref)
        assert tree.breaks <= len(true_ends)
        if tree.unknown == 0:
            assert tree.breaks == len(true_ends) == tree.lead_breaks
        first = tree.first_unknown()
        boundary = len(ref) if first is None else first[2]
        assert tree.lead_breaks == len(break_ends(ref[:boundary]))
        for k in range(1, tree.lead_breaks + 1):
            assert tree.find_break(k) == true_ends[k - 1]
        with pytest.raises(IndexError):
            tree.find_break(tree.lead_breaks + 1)
        for offset in range(len(ref) + 1):
            if first is None or offset < boundary:
                assert tree.row_of(offset) == bisect.bisect_right(true_ends, offset)
            else:
                with pytest.raises(RowNotIndexed):
                    tree.row_of(offset)


def _run(name: str, seed: int, fanout: int = 4, steps: int = 150) -> UnknownHarness:
    harness = UnknownHarness(ORIGINALS[name], seed, fanout)
    harness.check()
    for _ in range(steps):
        harness.random_step()
    return harness


@pytest.mark.parametrize("name", list(ORIGINALS))
@pytest.mark.parametrize("seed", range(8))
def test_fuzz_against_the_plain_bytes_model(name: str, seed: int) -> None:
    _run(name, seed)


@pytest.mark.parametrize(("seed", "fanout"), [(100, 5), (101, 32)])
def test_fuzz_with_other_fanouts(seed: int, fanout: int) -> None:
    _run("mixed", seed, fanout)


@pytest.mark.parametrize("seed", range(4))
def test_unknown_pieces_never_ask_the_oracle(seed: int) -> None:
    harness = UnknownHarness(ORIGINALS["mixed"], seed)
    harness.frontier_source.frontier = 0
    tree = PieceTree.from_pieces(harness.source_of, [make_unknown_piece(harness.frontier_source, 0, 0, len(harness.original))], fanout=4)
    harness.frontier_source.row_calls = 0
    tree.split_at(3)
    tree.splice(0, 3, Content.from_pieces([], 0))
    tree.check_invariants()
    assert tree.unknown >= 1
    assert harness.frontier_source.row_calls == 0
    with pytest.raises(RowNotIndexed):
        tree.row_of(0)


def test_deep_check_still_detects_a_wrong_known_count() -> None:
    harness = UnknownHarness(b"a\nb\nc", 0)
    harness.frontier_source.frontier = 5
    wrong = Piece(0, 0, 5, 3, 0, False, False)
    tree = PieceTree.from_pieces(harness.source_of, [wrong], fanout=4)
    with pytest.raises(AssertionError, match="break count"):
        tree.check_invariants(deep=True)


def test_invariants_reject_the_unknown_bit_on_a_piece_with_facts() -> None:
    harness = UnknownHarness(b"a\nb\nc", 0)
    harness.frontier_source.frontier = 5
    tree = PieceTree.from_pieces(harness.source_of, [Piece(0, 0, 5, 2, 0, False, False)], fanout=4)
    leaf = tree._root
    assert isinstance(leaf, _Leaf)
    leaf.flg[0] |= _UNKNOWN
    leaf.refresh()
    with pytest.raises(AssertionError, match="unknown piece with break facts"):
        tree.check_invariants()


def test_deep_check_rejects_an_unknown_piece_outside_the_original() -> None:
    harness = UnknownHarness(b"a\nb\nc", 0)
    harness.adds.append(FakeSource(b"xyz"))
    tree = PieceTree.from_pieces(harness.source_of, [make_unknown_piece(harness.adds[0], 1, 0, 3)], fanout=4)
    tree.check_invariants()
    with pytest.raises(AssertionError, match="outside the original"):
        tree.check_invariants(deep=True)


def test_the_fuzz_exercises_unknown_pieces_and_fills() -> None:
    seen_unknown = seen_fill = False
    for seed in range(8):
        harness = UnknownHarness(ORIGINALS["mixed"], seed)
        for _ in range(150):
            before = harness.tree.unknown
            harness.random_step()
            seen_unknown = seen_unknown or harness.tree.unknown > 0
            seen_fill = seen_fill or harness.tree.unknown < before
    assert seen_unknown
    assert seen_fill
