"""`PieceTable` rows, open tail and identity fast path against a plain-bytes reference (ACT4 design 4.5 and 5)."""

from __future__ import annotations

import threading

import pytest

from nova_editor.core import AddStore, BytesSource, Content, LineIndex, PieceTable, RowNotIndexed, RowRange, make_piece
from nova_editor.core.byte_source import ByteSource
from nova_editor.core.line_index import LineSnapshot

from .helpers import CountingSource, reference_row_at_offset, reference_rows
from .reference import ALPHABET, FakeSource, Rng, inside_crlf

ORIGINALS: dict[str, bytes] = {
    "lf": b"alpha\nbeta\n\ngamma\ndelta\nepsilon\nzeta\n",
    "crlf": b"alpha\r\nbeta\r\n\r\ngamma\r\ndelta\r\nepsilon\r\n",
    "cr": b"alpha\rbeta\r\rgamma\rdelta\repsilon\r",
    "mixed": b"a\r\nb\nc\rd\r\r\n\n\re\xff\xe2\x82\xac\r\nf\t\n",
    "no-trailing": b"alpha\nbeta\r\ngamma\rlast row without newline",
    "empty": b"",
}


class StepSource:
    """Byte source whose scan reads wait for `step()`, so a test advances the scan one block at a time without sleeping."""

    def __init__(self, data: bytes, scan_block: int) -> None:
        self._inner = BytesSource(data)
        self._blocks = -(-len(data) // scan_block)
        self._done = 0
        self._permits = threading.Semaphore(0)
        self._progress = threading.Event()
        self.index: LineIndex | None = None

    def length(self) -> int:
        return self._inner.length()

    def read(self, offset: int, size: int, *, cache: bool = True) -> bytes:
        if not cache:
            self._permits.acquire()
        return self._inner.read(offset, size, cache=cache)

    def close(self) -> None:
        self._inner.close()

    def attach(self, index: LineIndex) -> None:
        self.index = index
        index.subscribe(self._progress.set)
        index.start()
        if self._blocks == 0:
            assert index.join(10)

    def stop(self) -> None:
        """Release a scan that is still waiting (a failed test must not leave the thread behind)."""
        if self.index is not None:
            self.index.cancel()
            self._permits.release(self._blocks + 1)
            self.index.join(10)

    @property
    def finished(self) -> bool:
        return self._done >= self._blocks

    def step(self) -> None:
        """Let the scan thread process one more block and wait until it published it."""
        assert self.index is not None
        if self.finished:
            return
        self._progress.clear()
        self._done += 1
        self._permits.release()
        assert self._progress.wait(10)
        if self.finished:
            assert self.index.join(10)


class Harness:
    """A `PieceTable` and a bytes reference driven by the same operations and compared after every step."""

    def __init__(self, original: bytes, *, scan_block: int | None, seed: int, fanout: int = 4, stride: int = 2) -> None:
        self.rng = Rng(seed)
        self.ref = original
        self.original = original
        self.fake = FakeSource(original)
        self.saved: list[tuple[Content, bytes]] = []
        self.store = AddStore(tail_limit=24, stride=2)
        self.stepper: StepSource | None = None
        source: ByteSource
        if scan_block is None:
            source = BytesSource(original)
            self.index = LineIndex(source, stride=stride, scan_block=4096)
            self.index.scan_now()
        else:
            self.stepper = StepSource(original, scan_block)
            source = self.stepper
            self.index = LineIndex(source, stride=stride, scan_block=scan_block)
            self.stepper.attach(self.index)
        self.table = PieceTable(source, self.index, self.store, fanout)
        self.check()

    # ----- content builders ----------------------------------------------------------------------------------------------------------

    def random_bytes(self, limit: int) -> bytes:
        return b"".join(self.rng.choice(ALPHABET) for _ in range(self.rng.randint(0, limit)))

    def from_store(self, data: bytes) -> Content:
        if not data:
            return Content.from_pieces((), 0)
        segment, a, b = self.store.append(data)
        return Content.of([make_piece(self.store.segment(segment), segment, a, b)], self.store.segment)

    def from_original(self, a: int, b: int) -> tuple[Content, bytes]:
        """Content of the original `[a, b)`; like a copy made from the document, it lies in the scanned part."""
        scanned = self.index.snapshot()
        if (not scanned.complete and b >= scanned.scanned_bytes) or a == b or inside_crlf(self.original, a) or inside_crlf(self.original, b):
            return Content.from_pieces((), 0), b""
        piece = make_piece(self.fake, 0, a, b)
        return Content.from_pieces([piece], 0), self.original[a:b]

    # ----- operations ----------------------------------------------------------------------------------------------------------------

    def accepted(self, end: int) -> bool:
        snap = self.table.snapshot()
        return snap.complete or end < snap.scanned_bytes or end <= self.table.tree.length

    def splice(self, start: int, end: int, content: Content, data: bytes) -> None:
        before = self.ref
        if inside_crlf(before, start) or inside_crlf(before, end):
            with pytest.raises(ValueError, match="CRLF"):
                self.table.splice(start, end, content)
        elif not self.accepted(end):
            with pytest.raises(RowNotIndexed):
                self.table.splice(start, end, content)
        else:
            removed = self.table.splice(start, end, content)
            assert removed.length == end - start
            self.saved.append((removed, before[start:end]))
            self.ref = before[:start] + data + before[end:]
        self.check()

    def extract(self, start: int, end: int) -> None:
        if inside_crlf(self.ref, start) or inside_crlf(self.ref, end):
            with pytest.raises(ValueError, match="CRLF"):
                self.table.content(start, end)
        elif not self.accepted(end):
            with pytest.raises(RowNotIndexed):
                self.table.content(start, end)
        else:
            content = self.table.content(start, end)
            assert content.length == end - start
            self.saved.append((content, self.ref[start:end]))
        self.check()

    def random_step(self) -> None:
        rng = self.rng
        n = len(self.ref)
        a = rng.randint(0, n)
        b = rng.randint(a, min(n, a + rng.choice([1, 3, 10, 40])))
        op = rng.choice(["insert", "delete", "replace", "original", "saved", "extract", "step", "step"])
        if op == "insert":
            data = self.random_bytes(6)
            self.splice(a, a, self.from_store(data), data)
        elif op == "delete":
            self.splice(a, b, Content.from_pieces((), 0), b"")
        elif op == "replace":
            data = self.random_bytes(6)
            self.splice(a, b, self.from_store(data), data)
        elif op == "original":
            x = rng.randint(0, len(self.original))
            y = rng.randint(x, min(len(self.original), x + rng.choice([2, 8, 30])))
            content, data = self.from_original(x, y)
            self.splice(a, b, content, data)
        elif op == "saved" and self.saved:
            content, data = self.saved[rng.randint(0, len(self.saved) - 1)]
            self.splice(a, b, content, data)
        elif op == "extract":
            self.extract(a, b)
        elif self.stepper is not None:
            self.stepper.step()
            self.check()

    def stop(self) -> None:
        if self.stepper is not None:
            self.stepper.stop()

    def finish_scan(self) -> None:
        while self.stepper is not None and not self.stepper.finished:
            self.stepper.step()
        self.check()

    # ----- the comparison ------------------------------------------------------------------------------------------------------------

    def check(self) -> None:
        table, ref = self.table, self.ref
        snap = table.snapshot()
        expected = reference_rows(ref)
        assert table.length == len(ref)
        assert table.read(0, len(ref) + 5) == ref
        assert table.read(0, 0) == b""
        for _ in range(4):
            lo = self.rng.randint(0, len(ref))
            size = self.rng.randint(0, 12)
            assert table.read(lo, size) == ref[lo : lo + size]
        chunks = [table.tree._source_of(piece.src).read(piece.a + lo, piece.a + hi) for piece, lo, hi in table.iter_range(0, len(ref))]
        assert b"".join(chunks) == ref
        assert snap.error is None
        assert snap.scanned_bytes <= len(ref)
        if snap.complete:
            assert snap.count == len(expected)
            assert snap.scanned_bytes == len(ref)
        else:
            assert 1 <= snap.count <= len(expected)
        self.check_rows(snap, expected)
        self.check_offsets(snap, ref)
        if not table.is_identity:
            table.tree.check_invariants()

    def check_rows(self, snap: LineSnapshot, expected: list[tuple[int, int, int]]) -> None:
        table = self.table
        resolvable = snap.count if snap.complete else snap.count - 1
        for row, (start, content_end, end) in enumerate(expected):
            found = table.row_range(row)
            if row < resolvable:
                assert found == RowRange(start, content_end, end), row
            else:
                assert found is None, row
        assert table.row_range(len(expected) + 3) is None
        for first in range(0, len(expected) + 1, 3):
            got = table.lines(first, 128)
            assert got == [RowRange(*r) for r in expected[first : min(first + 128, resolvable)]], first
        with pytest.raises(IndexError):
            table.row_range(-1)

    def check_offsets(self, snap: LineSnapshot, ref: bytes) -> None:
        table = self.table
        for offset in range(len(ref) + 1):
            found = table.row_at_offset(offset)
            if offset < snap.scanned_bytes or snap.complete:
                row, (start, content_end, end) = reference_row_at_offset(ref, offset)
                assert found is not None, offset
                assert found == (row, RowRange(start, content_end, end)), offset
            else:
                assert found is None, offset
        assert table.row_at_offset(-1) is None
        assert table.row_at_offset(len(ref) + 1) is None


def _run(name: str, *, scan_block: int | None, seed: int, steps: int = 120) -> None:
    harness = Harness(ORIGINALS[name], scan_block=scan_block, seed=seed)
    try:
        for _ in range(steps):
            harness.random_step()
        harness.finish_scan()
    finally:
        harness.stop()


@pytest.mark.parametrize("name", list(ORIGINALS))
@pytest.mark.parametrize("seed", range(4))
def test_fuzz_with_the_scan_complete(name: str, seed: int) -> None:
    _run(name, scan_block=None, seed=seed)


@pytest.mark.parametrize("name", list(ORIGINALS))
@pytest.mark.parametrize("seed", range(4))
@pytest.mark.parametrize("scan_block", [3, 7])
def test_fuzz_with_a_stepped_scan(name: str, seed: int, scan_block: int) -> None:
    _run(name, scan_block=scan_block, seed=seed + 50)


@pytest.mark.parametrize("name", ["lf", "crlf", "mixed", "no-trailing"])
def test_edits_behind_the_frontier_open_the_tail_piece_by_piece(name: str) -> None:
    harness = Harness(ORIGINALS[name], scan_block=5, seed=9)
    stepper = harness.stepper
    assert stepper is not None
    while not stepper.finished:
        stepper.step()
        snap = harness.table.snapshot()
        if snap.scanned_bytes > 2 and not snap.complete:
            at = harness.rng.randint(0, snap.scanned_bytes - 1)
            if not inside_crlf(harness.ref, at):
                data = b"X\n"
                harness.splice(at, at, harness.from_store(data), data)
    assert not harness.table.has_open_tail
    harness.check()


def test_tail_folds_on_the_first_call_after_completion() -> None:
    harness = Harness(ORIGINALS["lf"], scan_block=6, seed=1)
    harness.splice(0, 0, harness.from_store(b"Z"), b"Z")
    assert harness.table.has_open_tail
    assert harness.stepper is not None
    while not harness.stepper.finished:
        harness.stepper.step()
    assert harness.table.has_open_tail  # nothing called the table yet
    assert harness.table.snapshot().complete
    assert not harness.table.has_open_tail
    harness.check()


def test_junction_of_cr_in_the_tree_and_lf_in_the_tail() -> None:
    harness = Harness(b"\nab\r\ncd\r\nef\r\n", scan_block=3, seed=2)
    assert harness.stepper is not None
    harness.stepper.step()
    harness.stepper.step()
    harness.splice(0, 0, harness.from_store(b"\r"), b"\r")
    with pytest.raises(ValueError, match="CRLF"):
        harness.table.splice(1, 1, harness.from_store(b"x"))
    harness.finish_scan()


# ----- identity fast path ------------------------------------------------------------------------------------------------------------


class SpyIndex(LineIndex):
    """`LineIndex` that counts the row queries it receives."""

    def __init__(self, source: ByteSource) -> None:
        super().__init__(source, stride=2)
        self.calls = 0

    def row_range(self, row: int) -> RowRange | None:
        self.calls += 1
        return super().row_range(row)

    def lines(self, first: int, count: int) -> list[RowRange]:
        self.calls += 1
        return super().lines(first, count)

    def row_at_offset(self, offset: int) -> tuple[int, RowRange] | None:
        self.calls += 1
        return super().row_at_offset(offset)


@pytest.mark.parametrize("complete", [True, False])
def test_identity_delegates_every_row_query(complete: bool) -> None:
    data = ORIGINALS["mixed"]
    source = BytesSource(data)
    index = SpyIndex(source)
    if complete:
        index.scan_now()
    table = PieceTable(source, index, AddStore())
    assert table.snapshot() == index.snapshot()
    for row in range(len(reference_rows(data)) + 1):
        index.calls = 0
        got = (table.row_range(row), table.lines(row, 5))
        assert index.calls == 2
        assert got == (LineIndex.row_range(index, row), LineIndex.lines(index, row, 5))
    index.calls = 0
    for offset in range(len(data) + 2):
        assert table.row_at_offset(offset) == LineIndex.row_at_offset(index, offset)
    assert index.calls >= len(data) + 2
    assert table.read(2, 5) == data[2:7]


def test_identity_is_left_by_an_edit_and_restored_by_the_inverse_splice() -> None:
    data = ORIGINALS["lf"]
    source = BytesSource(data)
    index = SpyIndex(source)
    index.scan_now()
    table = PieceTable(source, index, AddStore())
    assert table.is_identity
    removed = table.splice(6, 11, Content.from_pieces((), 0))
    assert not table.is_identity
    assert table.row_range(1) == RowRange(6, 6, 7)
    table.splice(6, 6, removed)
    assert table.is_identity
    index.calls = 0
    assert table.row_range(1) == LineIndex.row_range(index, 1)
    assert index.calls == 1


def test_empty_original_and_empty_document() -> None:
    harness = Harness(b"", scan_block=None, seed=3)
    harness.splice(0, 0, harness.from_store(b"a\nb"), b"a\nb")
    harness.splice(0, 3, Content.from_pieces((), 0), b"")
    assert harness.ref == b""
    assert harness.table.row_range(0) == RowRange(0, 0, 0)


# ----- budget ------------------------------------------------------------------------------------------------------------------------


def test_row_queries_stay_within_the_per_call_budget_after_edits() -> None:
    data = b"short\n" + b"x" * 5000 + b"\nafter\nlast\n"
    counting = CountingSource(BytesSource(data))
    index = LineIndex(counting, stride=1, long_line_threshold=64, long_line_cap=0, read_budget=256, scan_block=64)
    index.scan_now()
    store = AddStore()
    table = PieceTable(counting, index, store)
    segment, a, b = store.append(b"Q")
    table.splice(0, 0, Content.of([make_piece(store.segment(segment), segment, a, b)], store.segment))
    assert not table.is_identity
    for row in range(table.snapshot().count):
        before = counting.bytes_read
        table.row_range(row)
        table.lines(row, 3)
        assert counting.bytes_read - before <= 2 * 4 * 256 + 64, row
    assert table.row_range(0) == RowRange(0, 6, 7)
    assert table.row_range(1) == RowRange(7, 5007, 5008)
    assert table.row_at_offset(100) is None  # the walk over the unrecorded long row would exceed the budget


def test_edit_beyond_the_scanned_part_is_rejected_and_changes_nothing() -> None:
    harness = Harness(ORIGINALS["lf"], scan_block=6, seed=4)
    try:
        assert harness.stepper is not None
        harness.stepper.step()
        scanned = harness.table.snapshot().scanned_bytes
        assert 0 < scanned < len(harness.ref)
        with pytest.raises(RowNotIndexed):
            harness.table.splice(scanned, scanned, harness.from_store(b"x"))
        with pytest.raises(RowNotIndexed):
            harness.table.content(0, len(harness.ref))
        harness.check()
        harness.splice(scanned - 1, scanned - 1, harness.from_store(b"x"), b"x")
        assert harness.table.has_open_tail
    finally:
        harness.stop()
