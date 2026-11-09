"""`Rebaser.translate` over pieces beyond the line scan of the old original (ACT1 design 11.5)."""

from __future__ import annotations

import pytest

from nova_editor.core import AddStore, BytesSource, Content, LineIndex, OriginalSource, Piece, PieceSource, make_piece
from nova_editor.core.pieces import generation_of, make_unknown_piece
from nova_editor.core.rebase import Rebaser
from nova_editor.core.save_layout import SaveLayout

from .reference import ALPHABET, FakeSource, Frontier, Rng
from .test_rebase import new_store

ORIGINAL = b"alpha\nbeta\r\ngamma\rdelta\nepsilon\nzeta\n" * 3


def _truth(content: Content, data: list[bytes]) -> bytes:
    return b"".join(data[p.src][p.a : p.b] for p in content.pieces)


def _rebaser(layout: SaveLayout, old: list[PieceSource], saved: bytes, *, limit: int, legacy: bytes | None) -> Rebaser:
    kept = None
    if legacy is not None:
        source = BytesSource(legacy)
        index = LineIndex(source, stride=4, long_line_threshold=64)
        index.scan_now()
        kept = (OriginalSource(index, source), AddStore())
    return Rebaser(layout, old, FakeSource(saved), new_store(), limit=limit, legacy=kept)


def test_unknown_orphan_in_the_retained_generation_is_kept_not_retain_all() -> None:
    frontier = Frontier(ORIGINAL)
    frontier.frontier = 10
    layout = SaveLayout()
    layout.add(0, 0, 6, 0)  # only the first row is saved: everything after it is an orphan
    piece = make_unknown_piece(frontier, 0, 3, 60)
    content = Content.from_pieces([piece], 0)
    plan = _rebaser(layout, [frontier], ORIGINAL[:6], limit=0, legacy=ORIGINAL).translate([content])
    assert plan.retained is not None
    assert not plan.clear_history
    translated = plan.contents[0]
    assert translated.length == content.length
    kept = [p for p in translated.pieces if generation_of(p.src) == 1]
    assert kept
    assert all(p.unknown for p in kept)
    assert any(generation_of(p.src) == 0 for p in translated.pieces)  # the saved part was translated, not retained whole
    assert translated.unknown == len(kept)
    assert plan.retained[0].read(kept[0].a, kept[0].b) == ORIGINAL[kept[0].a : kept[0].b]


def test_a_known_piece_spanning_an_unscanned_orphan_range_stays_a_piece() -> None:
    frontier = Frontier(ORIGINAL)
    frontier.frontier = 20
    piece = make_piece(frontier, 0, 0, 15)
    layout = SaveLayout()
    layout.add(0, 0, 4, 0)
    frontier.frontier = 0  # the oracle can no longer answer for the orphan part 4..15
    plan = _rebaser(layout, [frontier], ORIGINAL[:4], limit=0, legacy=ORIGINAL).translate([Content.from_pieces([piece], 0)])
    assert plan.retained is not None
    assert plan.contents[0].length == 15
    assert [generation_of(p.src) for p in plan.contents[0].pieces] == [0, 1]  # translated, not retained whole
    assert plan.contents[0].unknown == 1


def test_stale_unknown_pieces_become_known_when_the_new_original_has_them() -> None:
    frontier = Frontier(ORIGINAL)
    layout = SaveLayout()
    layout.add(0, 0, len(ORIGINAL), 0)
    piece = make_unknown_piece(frontier, 0, 5, 40)
    content = Content.from_pieces([piece], 0)
    assert content.unknown == 1
    plan = _rebaser(layout, [frontier], ORIGINAL, limit=1 << 20, legacy=None).translate([content])
    translated = plan.contents[0]
    assert translated.unknown == 0
    assert translated.length == content.length
    assert translated.breaks == len(ORIGINAL[5:40].replace(b"\r\n", b"\n").replace(b"\r", b"\n").split(b"\n")) - 1
    assert translated.lead_breaks == translated.breaks


def test_a_content_without_unknown_pieces_keeps_copied_aggregates() -> None:
    source = FakeSource(ORIGINAL)
    layout = SaveLayout()
    layout.add(0, 0, len(ORIGINAL), 0)
    content = Content.from_pieces([make_piece(source, 0, 2, 30)], 0)
    plan = _rebaser(layout, [source], ORIGINAL, limit=1 << 20, legacy=None).translate([content])
    translated = plan.contents[0]
    assert translated.breaks == content.breaks
    assert translated.lead_breaks == translated.breaks


@pytest.mark.parametrize("seed", range(60))
@pytest.mark.parametrize("limit", [0, 1 << 20])
def test_random_contents_over_an_unscanned_original_keep_their_bytes(seed: int, limit: int) -> None:
    rng = Rng(seed)
    data = b"".join(rng.choice(ALPHABET) for _ in range(80))
    frontier = Frontier(data)
    frontier.frontier = rng.randint(0, len(data))
    layout = SaveLayout()
    out = 0
    saved = bytearray()
    for _ in range(rng.randint(1, 4)):
        a = rng.randint(0, len(data) - 1)
        b = rng.randint(a + 1, min(len(data), a + 30))
        layout.add(0, a, b, out)
        saved += data[a:b]
        out += b - a
    contents: list[Content] = []
    for _ in range(rng.randint(1, 4)):
        pieces: list[Piece] = []
        for _ in range(rng.randint(1, 4)):
            a = rng.randint(0, len(data) - 1)
            b = rng.randint(a + 1, min(len(data), a + 25))
            try:
                pieces.append(make_piece(frontier, 0, a, b))
            except LookupError:
                pieces.append(make_unknown_piece(frontier, 0, a, b))
        contents.append(Content.from_pieces(pieces, 0))
    plan = _rebaser(layout, [frontier], bytes(saved), limit=limit, legacy=data if limit == 0 else None).translate(contents)
    assert not plan.clear_history
    for old, new in zip(contents, plan.contents, strict=True):
        assert new.length == old.length
        assert new == Content.from_pieces(new.pieces, new.tail_chars)
        if limit == 0:
            assert plan.retained is not None
        read = b"".join(
            (
                FakeSource(bytes(saved)).read(p.a, p.b)
                if p.src == 0 and generation_of(p.src) == 0
                else (plan.retained[0].read(p.a, p.b) if generation_of(p.src) == 1 and plan.retained else plan.new_add_store.read(p.src, p.a, p.b))
            )
            for p in new.pieces
        )
        assert read == _truth(old, [data])


def test_present_part_beyond_the_scan_of_the_new_original_becomes_an_unknown_piece() -> None:
    layout = SaveLayout()
    layout.add(0, 0, len(ORIGINAL), 0)
    old = FakeSource(ORIGINAL)
    piece = make_piece(old, 0, 2, 60)
    new_original = Frontier(ORIGINAL)  # the saved file: its line scan has not reached the part
    new_original.frontier = 5
    plan = Rebaser(layout, [old], new_original, new_store(), limit=1 << 20, legacy=None).translate([Content.from_pieces([piece], 0)])
    translated = plan.contents[0]
    assert translated.length == 58
    assert translated.unknown == 1
    assert all(generation_of(p.src) == 0 for p in translated.pieces)
