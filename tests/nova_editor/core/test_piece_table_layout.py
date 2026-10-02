"""`PieceTable.layout_range` (no reads) and the generation-aware legacy source registry (ACT5 design 8.3 and 8.4)."""

from __future__ import annotations

import pytest

from nova_editor.core import AddStore, BytesSource, Content, LineIndex, OriginalSource, PieceTable, make_piece
from nova_editor.core.pieces import MAX_GENERATION, SEGMENT_MASK, generation_of, make_src, segment_of

from .helpers import CountingSource, reference_rows
from .reference import inside_crlf
from .test_piece_table import ORIGINALS, Harness, StepSource

SEEDS = range(6)
STEPS = 80
LINES = b"alpha\nbeta\r\ngamma\rdelta\nepsilon\n" * 4


class LoggingStore(AddStore):
    """Add store that logs every byte read."""

    def __init__(self) -> None:
        super().__init__(tail_limit=24, stride=2)
        self.calls = 0

    def read(self, segment: int, a: int, b: int) -> bytes:
        self.calls += 1
        return super().read(segment, a, b)


class LoggingStep(StepSource):
    """Step source that counts reads."""

    def __init__(self, data: bytes, scan_block: int) -> None:
        super().__init__(data, scan_block)
        self.calls = 0

    def read(self, offset: int, size: int, *, cache: bool = True) -> bytes:
        self.calls += 1
        return super().read(offset, size, cache=cache)


def _runs_bytes(harness: Harness, runs: list[tuple[int, int, int]], legacy: tuple[bytes, AddStore] | None = None) -> bytes:
    parts: list[bytes] = []
    for src, a, b in runs:
        if generation_of(src) == 1 and legacy is not None:
            parts.append(legacy[0][a:b] if segment_of(src) == 0 else legacy[1].read(segment_of(src), a, b))
        elif src == 0:
            parts.append(harness.original[a:b])
        else:
            parts.append(harness.store.read(src, a, b))
    return b"".join(parts)


def _check_layout(harness: Harness, legacy: tuple[bytes, AddStore] | None = None) -> None:
    table = harness.table
    ref = harness.ref
    runs = table.layout_range(0, table.length)
    assert sum(b - a for _, a, b in runs) == table.length
    assert all(a < b for _, a, b in runs)
    assert _runs_bytes(harness, runs, legacy) == ref
    for _ in range(4):
        start = harness.rng.randint(0, len(ref))
        end = harness.rng.randint(start, len(ref))
        assert _runs_bytes(harness, table.layout_range(start, end), legacy) == ref[start:end]


@pytest.mark.parametrize("seed", SEEDS)
@pytest.mark.parametrize("name", ["lf", "mixed", "no-trailing"])
@pytest.mark.parametrize("scan_block", [None, 8])
def test_layout_range_matches_read(name: str, scan_block: int | None, seed: int) -> None:
    harness = Harness(ORIGINALS[name], scan_block=scan_block, seed=seed)
    try:
        for _ in range(STEPS):
            harness.random_step()
            _check_layout(harness)
        harness.finish_scan()
        _check_layout(harness)
    finally:
        harness.stop()


def test_layout_range_empty_and_invalid() -> None:
    harness = Harness(ORIGINALS["lf"], scan_block=None, seed=1)
    assert harness.table.layout_range(3, 3) == []
    with pytest.raises(ValueError, match="outside"):
        harness.table.layout_range(0, harness.table.length + 1)


def test_layout_range_never_reads_and_shows_open_tail() -> None:
    source = LoggingStep(LINES, 8)
    index = LineIndex(source, stride=2, scan_block=8)
    source.attach(index)
    store = LoggingStore()
    table = PieceTable(source, index, store, 4)
    try:
        source.step()
        segment, a, b = store.append(b"NEW")
        piece = make_piece(store.segment(segment), segment, a, b)
        table.splice(0, 5, Content.from_pieces([piece], 3))
        assert table.has_open_tail
        source.calls = 0
        store.calls = 0
        runs = table.layout_range(0, table.length)
        assert (source.calls, store.calls) == (0, 0)
        assert runs[0] == (segment, a, b)
        assert runs[-1] == (0, 5, len(LINES))
        assert sum(y - x for _, x, y in runs) == table.length
        assert table.layout_range(2, 4) == [(segment, a + 2, a + 3), (0, 5, 6)]
        assert table.layout_range(table.length, table.length) == []
    finally:
        source.stop()


def test_generation_helpers() -> None:
    src = make_src(3, 17)
    assert (generation_of(src), segment_of(src)) == (3, 17)
    assert make_src(0, 5) == 5
    assert segment_of(make_src(MAX_GENERATION, SEGMENT_MASK)) == SEGMENT_MASK
    assert generation_of(make_src(MAX_GENERATION, 0)) == MAX_GENERATION


def _legacy(data: bytes, appended: list[bytes]) -> tuple[OriginalSource, AddStore]:
    source = BytesSource(data)
    index = LineIndex(source, stride=2, scan_block=4096)
    index.scan_now()
    store = AddStore(tail_limit=8, stride=2)
    for chunk in appended:
        store.append(chunk)
    return OriginalSource(index, source), store


def test_legacy_registry_resolves_generations() -> None:
    harness = Harness(ORIGINALS["lf"], scan_block=None, seed=1)
    old_data = b"old\r\noriginal\nrows\rhere\n"
    old_original, old_store = _legacy(old_data, [b"seg one\n", b"two", b"x" * 20])
    generation = harness.table.register_legacy(old_original, old_store)
    assert generation == 1
    # Original of the legacy generation (k = 0).
    src0 = make_src(generation, 0)
    piece = make_piece(old_original, src0, 0, 9)
    harness.splice(2, 4, Content.from_pieces([piece], 0), old_data[:9])
    assert harness.table.read(2, 9) == old_data[:9]
    # Add segments (k >= 1).
    for k, text in ((1, b"seg one\n"), (2, b"two"), (3, b"x" * 20)):
        src = make_src(generation, k)
        piece = make_piece(old_store.segment(k), src, 0, len(text))
        position = harness.rng.randint(0, len(harness.ref))
        if inside_crlf(harness.ref, position):
            position = 0
        harness.splice(position, position, Content.from_pieces([piece], 0), text)
    assert harness.ref == harness.table.read(0, len(harness.ref))
    assert len(reference_rows(harness.ref)) == harness.table.snapshot().count
    _check_layout(harness, (old_data, old_store))
    runs = harness.table.layout_range(0, harness.table.length)
    assert {generation_of(src) for src, _, _ in runs} >= {0, generation}


def test_generation_zero_is_unchanged_and_limit() -> None:
    table = PieceTable(BytesSource(b""), LineIndex(BytesSource(b"")), AddStore())
    counting = CountingSource(BytesSource(b"x\n"))
    old_original = OriginalSource(LineIndex(counting), counting)
    store = AddStore()
    assert [table.register_legacy(old_original, store) for _ in range(MAX_GENERATION)] == list(range(1, MAX_GENERATION + 1))
    with pytest.raises(ValueError, match="generation"):
        table.register_legacy(old_original, store)


def test_source_of_is_public_and_resolves_every_source() -> None:
    store = AddStore()
    source = BytesSource(LINES)
    index = LineIndex(source)
    index.scan_now()
    table = PieceTable(source, index, store)
    content = table.add(b"new")
    (piece,) = content.pieces
    assert isinstance(table.source_of(0), OriginalSource)
    assert table.source_of(piece.src).read(piece.a, piece.b) == b"new"
