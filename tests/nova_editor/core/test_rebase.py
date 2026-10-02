"""`Rebaser.translate` against a brute-force byte map (ACT5 design 8.3 and 8.4)."""

from __future__ import annotations

import pytest

from nova_editor.core import AddStore, BytesSource, Content, LineIndex, OriginalSource, Piece, PieceSource, make_piece
from nova_editor.core.pieces import MAX_GENERATION, aggregate_of_bytes, generation_of, make_src, segment_of
from nova_editor.core.rebase import UNDO_COPY_LIMIT, RebasePlan, Rebaser
from nova_editor.core.save_layout import SaveLayout

from .rebase_helpers import walk_pieces
from .reference import ALPHABET, FakeSource, Rng

SEEDS = range(200)
LEGACY_SEEDS = range(20)


class Poisoned:
    """A piece source that fails every read: nothing may touch the old files after a translation."""

    def row_of(self, offset: int) -> int:
        raise AssertionError("old source used")

    def row_start(self, row: int) -> int | None:
        raise AssertionError("old source used")

    def breaks_between(self, a: int, b: int) -> int:
        raise AssertionError("old source used")

    def read(self, a: int, b: int) -> bytes:
        raise AssertionError("old source used")


class FailingRead(FakeSource):
    """A fake source whose reads fail with an `OSError`."""

    def read(self, a: int, b: int) -> bytes:
        raise OSError(5, "injected")


class World:
    """An old original, two old add segments, a saved layout over random runs (duplicates and orphans) and random contents."""

    def __init__(self, seed: int, *, failing: bool = False) -> None:
        self.rng = Rng(seed)
        self.data = [self._bytes(self.rng.randint(20, 60)) for _ in range(3)]  # src 0 (original), 1 and 2 (add segments)
        self.old: list[PieceSource] = [FakeSource(data) for data in self.data]
        self.layout = SaveLayout()
        out = 0
        written = bytearray()
        for _ in range(self.rng.randint(2, 8)):
            src = self.rng.randint(0, 2)
            a = self.rng.randint(0, len(self.data[src]) - 1)
            b = self.rng.randint(a + 1, min(len(self.data[src]), a + 25))
            self.layout.add(src, a, b, out)
            written += self.data[src][a:b]
            out += b - a
        self.saved = bytes(written)
        self.new_original = FakeSource(self.saved)
        self.contents = [self._content() for _ in range(self.rng.randint(1, 6))]
        if failing:  # armed after the pieces are made
            self.old[1] = FailingRead(self.data[1])

    def _bytes(self, size: int) -> bytes:
        return b"".join(self.rng.choice(ALPHABET) for _ in range(size))

    def _content(self) -> Content:
        pieces: list[Piece] = []
        for _ in range(self.rng.randint(0, 5)):
            src = self.rng.randint(0, 2)
            a = self.rng.randint(0, len(self.data[src]) - 1)
            b = self.rng.randint(a + 1, min(len(self.data[src]), a + 20))
            pieces.append(make_piece(self.old[src], src, a, b))
        return Content.of(pieces, lambda s: self.old[s])

    def old_bytes(self, content: Content) -> bytes:
        return b"".join(self.data[p.src][p.a : p.b] for p in content.pieces)

    def marks(self) -> dict[tuple[int, int], int]:
        """Brute force: the output offset of the first occurrence of every written source byte."""
        found: dict[tuple[int, int], int] = {}
        for src in range(3):
            for a, b, out in self.layout.intervals(src):
                for offset in range(a, b):
                    found.setdefault((src, offset), out + offset - a)
        return found

    def rebaser(
        self,
        store: AddStore,
        *,
        limit: int = UNDO_COPY_LIMIT,
        max_generation: int = MAX_GENERATION,
        legacy: tuple[OriginalSource, AddStore] | None = None,
        generations: int = 0,
    ) -> Rebaser:
        return Rebaser(self.layout, self.old, self.new_original, store, limit=limit, max_generation=max_generation, legacy=legacy, generations=generations)


def new_store() -> AddStore:
    return AddStore(tail_limit=24, stride=2)


def legacy_of(world: World) -> tuple[OriginalSource, AddStore]:
    source = BytesSource(world.data[0])
    index = LineIndex(source, stride=4, long_line_threshold=64)
    index.scan_now()
    return OriginalSource(index, source), AddStore()


def read_back(plan_store: AddStore, world: World, content: Content, *, legacy: dict[int, PieceSource] | None = None) -> bytes:
    """Read `content` through the NEW sources only (plus the legacy generation, when given); the old files are not reachable."""

    def source_of(src: int) -> PieceSource:
        generation = generation_of(src)
        if generation == 0:
            return world.new_original if src == 0 else plan_store.segment(src)
        assert legacy is not None
        return legacy[segment_of(src)]

    return b"".join(source_of(p.src).read(p.a, p.b) for p in walk_pieces(content))


def expected_present(world: World, content: Content) -> list[tuple[int, int, int]]:
    """Brute force: the pieces of the new original `(0, a, b)` that `content` must contain, adjacent contiguous parts merged."""
    marks = world.marks()
    parts: list[list[int]] = []  # [present (1) or orphan (0), first output offset or 0, end]
    for piece in content.pieces:
        current: list[int] | None = None
        for offset in range(piece.a, piece.b):
            out = marks.get((piece.src, offset))
            if out is None:
                if current is not None and current[0] == 0:
                    continue
                current = [0, 0, 0]
                parts.append(current)
            elif current is not None and current[0] == 1 and current[2] == out:
                current[2] = out + 1
            else:
                current = [1, out, out + 1]
                parts.append(current)
        current = None
    merged: list[list[int]] = []
    for part in parts:
        if merged and part[0] == 1 and merged[-1][0] == 1 and merged[-1][2] == part[1]:
            merged[-1][2] = part[2]
        else:
            merged.append(part)
    return [(0, part[1], part[2]) for part in merged if part[0] == 1]


@pytest.mark.parametrize("seed", SEEDS)
def test_translate_copies_orphans_and_keeps_bytes_and_aggregates(seed: int) -> None:
    world = World(seed)
    store = new_store()
    plan = world.rebaser(store).translate(world.contents)
    assert plan.retained is None
    assert not plan.clear_history
    assert len(plan.contents) == len(world.contents)
    marks = world.marks()
    unique: set[tuple[int, int, int]] = set()
    for old in world.contents:
        for piece in old.pieces:
            run_start = None
            for offset in range(piece.a, piece.b + 1):
                absent = offset < piece.b and (piece.src, offset) not in marks
                if absent and run_start is None:
                    run_start = offset
                if not absent and run_start is not None:
                    unique.add((piece.src, run_start, offset))
                    run_start = None
    assert plan.orphan_bytes == sum(b - a for _, a, b in unique)
    # The old files are unreachable now: nothing reads them.
    world.old = [Poisoned() for _ in world.old]
    for old, new in zip(world.contents, plan.contents, strict=True):
        assert read_back(plan.new_add_store, world, new) == world_bytes(world, old)
        assert new.aggregate == old.aggregate
        assert new.aggregate == aggregate_of_bytes(world_bytes(world, old))
        assert (new.length, new.breaks, new.tail_chars) == (old.length, old.breaks, old.tail_chars)
        for piece in walk_pieces(new):
            assert generation_of(piece.src) == 0
            if piece.src == 0:
                assert piece == make_piece(world.new_original, 0, piece.a, piece.b)
                assert 0 <= piece.a < piece.b <= len(world.saved)
            else:
                assert piece.src <= plan.new_add_store.segment_count
                assert piece.b <= plan.new_add_store.length(piece.src)
        assert [(p.src, p.a, p.b) for p in new.pieces if p.src == 0] == expected_present(world, old)


def world_bytes(world: World, content: Content) -> bytes:
    return b"".join(world.data[p.src][p.a : p.b] for p in content.pieces)


def test_the_store_holds_only_the_orphans() -> None:
    world = World(3)
    store = new_store()
    plan = world.rebaser(store).translate(world.contents)
    held = sum(store.length(k) for k in range(1, store.segment_count + 1))
    assert held == plan.orphan_bytes


@pytest.mark.parametrize("seed", LEGACY_SEEDS)
def test_orphans_over_the_limit_stay_as_ranges_of_a_retained_generation(seed: int) -> None:
    world = World(seed)
    legacy = legacy_of(world)
    plan = world.rebaser(new_store(), limit=3, legacy=legacy, generations=0).translate(world.contents)
    if plan.orphan_bytes <= 3:
        assert plan.retained is None
        return
    assert plan.retained is legacy
    assert not plan.clear_history
    assert plan.new_add_store.segment_count == 0
    for old, new in zip(world.contents, plan.contents, strict=True):
        assert new.aggregate == old.aggregate
        assert read_back(plan.new_add_store, world, new, legacy=dict(enumerate(world.old))) == world_bytes(world, old)
        for piece in walk_pieces(new):
            if generation_of(piece.src) == 1:
                assert segment_of(piece.src) in {0, 1, 2}
                assert piece.src == make_src(1, segment_of(piece.src))
                assert all(world.marks().get((segment_of(piece.src), x)) is None for x in range(piece.a, piece.b))
            else:
                assert piece.src == 0


def test_the_generation_follows_the_registered_ones_and_old_generations_are_untouched() -> None:
    world = World(7)
    foreign = Piece(make_src(1, 1), 0, 4, 0, 0, False, False)
    held = Content.from_pieces([foreign], 4)
    legacy = legacy_of(world)
    plan = world.rebaser(new_store(), limit=0, legacy=legacy, generations=1).translate([held, *world.contents])
    assert plan.contents[0] is not held
    assert plan.contents[0].pieces == (foreign,)
    assert plan.retained is legacy
    generations = {generation_of(p.src) for c in plan.contents[1:] for p in c.pieces}
    assert generations <= {0, 2}


def test_at_the_last_generation_the_history_is_cleared() -> None:
    world = World(7)
    plan = world.rebaser(new_store(), limit=0, legacy=legacy_of(world), generations=4, max_generation=4).translate(world.contents)
    assert plan.orphan_bytes > 0
    assert plan.clear_history
    assert plan.retained is None
    assert plan.contents == []


def test_the_last_generation_is_still_usable() -> None:
    world = World(7)
    plan = world.rebaser(new_store(), limit=0, legacy=legacy_of(world), generations=3, max_generation=4).translate(world.contents)
    assert not plan.clear_history
    assert plan.retained is not None


def test_the_default_limit_is_eight_mebibytes() -> None:
    assert UNDO_COPY_LIMIT == 8 * 1024 * 1024
    assert MAX_GENERATION == 255


@pytest.mark.parametrize("seed", LEGACY_SEEDS)
def test_an_exception_in_the_translation_keeps_every_reference(seed: int) -> None:
    world = World(seed, failing=True)
    legacy = legacy_of(world)
    store = new_store()
    plan = world.rebaser(store, legacy=legacy).translate(world.contents)
    uses_failing = any(not _all_present(world, piece) for content in world.contents for piece in content.pieces if piece.src == 1)
    if not uses_failing:
        assert plan.retained is None
        return
    assert plan.retained is legacy
    assert plan.orphan_bytes == 0
    for old, new in zip(world.contents, plan.contents, strict=True):
        assert new.aggregate == old.aggregate
        expected = [(p.src if generation_of(p.src) else make_src(1, p.src), p.a, p.b) for p in old.pieces]
        assert [(p.src, p.a, p.b) for p in new.pieces] == expected


def _all_present(world: World, piece: Piece) -> bool:
    marks = world.marks()
    return all((piece.src, x) in marks for x in range(piece.a, piece.b))


def test_an_exception_at_the_last_generation_clears_the_history() -> None:
    world = World(1, failing=True)
    pieces = [make_piece(FakeSource(world.data[1]), 1, 0, 2)]
    # Make the piece an orphan for sure: nothing of source 1 was written.
    world.layout = SaveLayout()
    world.layout.add(0, 0, 4, 0)
    content = Content.from_pieces(pieces, 0)
    plan = world.rebaser(new_store(), generations=2, max_generation=2).translate([content])
    assert plan.clear_history
    assert plan.contents == []


def test_retain_all_is_a_plan_of_its_own() -> None:
    world = World(2)
    legacy = legacy_of(world)
    plan = RebasePlan.retain_all(world.contents, new_store(), legacy, 5)
    assert plan.retained is legacy
    assert all(generation_of(p.src) == 5 for c in plan.contents for p in c.pieces)
    with pytest.raises(ValueError, match="old files"):
        RebasePlan.retain_all(world.contents, new_store(), None, 5)
