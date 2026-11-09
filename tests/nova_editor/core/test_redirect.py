"""`Redirect`: pure translation of references off overwritten original ranges (ACT3 design 5.1, risk R9)."""

from __future__ import annotations

import dataclasses
from typing import cast

import pytest

from nova_editor.core import AddStore, Content, Piece, PieceSource, make_piece, make_unknown_piece
from nova_editor.core.pieces import aggregate_of_bytes, generation_of, segment_of
from nova_editor.core.redirect import Copy, Redirect
from nova_editor.document._byte_edit import ByteEdit
from nova_editor.document._history import EditHistory
from nova_editor.document._lazy_document import LazyDocument
from tests.nova_editor.core.reference import FakeSource, Rng
from tests.nova_editor.document.test_byte_edit import EMPTY, hex_edit
from tests.nova_editor.document.test_edit_history import new_history
from tests.nova_editor.document.test_lazy_edit import SMALL

CRLF_TEXT = b"ab\r\ncd\r\n\r\nef\rgh\nij\r\n"


class World:
    """A fake original, a small add store and the copies made of original ranges."""

    def __init__(self, original: bytes) -> None:
        self.original = original
        self.source = FakeSource(original)
        self.store = AddStore(tail_limit=24, stride=2)
        self.copies: list[Copy] = []

    def copy(self, a: int, b: int) -> None:
        segment, start, _ = self.store.append(self.original[a:b])
        self.copies.append(Copy(a, b, segment, start))

    def redirect(self) -> Redirect:
        return Redirect(self.copies, self.source, self.store)

    def read(self, piece: Piece) -> bytes:
        if piece.src == 0:
            return self.original[piece.a : piece.b]
        return self.store.read(segment_of(piece.src), piece.a, piece.b)

    def bytes_of(self, content: Content) -> bytes:
        return b"".join(self.read(piece) for piece in content.pieces)

    def content(self, ranges: list[tuple[int, int]], *, unknown: bool = False) -> Content:
        pieces = [make_unknown_piece(cast("PieceSource", self.source), 0, a, b) if unknown else make_piece(cast("PieceSource", self.source), 0, a, b) for a, b in ranges]
        return Content.from_pieces(pieces, 0)

    def inside_copies(self, content: Content) -> bool:
        return any(piece.src == 0 and any(c.a < piece.b and piece.a < c.b for c in self.copies) for piece in content.pieces)


def assert_facts(world: World, content: Content) -> None:
    """The content's aggregate equals a recompute from its bytes (exact when no piece is unknown)."""
    data = world.bytes_of(content)
    truth = aggregate_of_bytes(data)
    assert content.length == truth.length
    assert content.first_is_lf == truth.first_is_lf
    assert content.last_is_cr == truth.last_is_cr
    if content.unknown == 0:
        assert content.breaks == truth.breaks
        assert content.lead_breaks == truth.breaks
    else:
        assert content.breaks <= truth.breaks


def random_original(rng: Rng, size: int = 60) -> bytes:
    return bytes(rng.choice([ord("a"), ord("b"), 13, 10, 13, 10, ord("c")]) for _ in range(size))


def random_ranges(rng: Rng, size: int, count: int) -> list[tuple[int, int]]:
    found = []
    for _ in range(count):
        a = rng.randint(0, size - 1)
        found.append((a, rng.randint(a + 1, size)))
    return found


def random_copies(rng: Rng, size: int, world: World) -> None:
    cursor = 0
    while cursor < size - 1 and rng.randint(0, 5) > 0:
        a = rng.randint(cursor, min(cursor + 15, size - 1))
        b = rng.randint(a + 1, min(a + 12, size))
        world.copy(a, b)
        cursor = b + rng.randint(0, 3)


@pytest.mark.parametrize("seed", range(60))
def test_property_bytes_and_break_facts_are_unchanged(seed: int) -> None:
    rng = Rng(seed)
    world = World(random_original(rng))
    world.store.append(b"pre\r")  # a CR tail: the first copy continues it
    random_copies(rng, len(world.original), world)
    unknown = seed % 3 == 0
    add = world.store.append(b"zz\n")
    for count in (1, 3, 6):
        content = world.content(random_ranges(rng, len(world.original), count), unknown=unknown)
        content = Content.from_pieces([*content.pieces, make_piece(world.store.segment(add[0]), add[0], add[1], add[2])], content.tail_chars)
        out = world.redirect().apply(content)
        assert world.bytes_of(out) == world.bytes_of(content)
        assert out.tail_chars == content.tail_chars
        assert_facts(world, out)
        assert not world.inside_copies(out)
        # known input stays exactly known: a recompute of the translated pieces equals the stored aggregate
        again = Content.from_pieces(out.pieces, out.tail_chars)
        assert again == out


def test_a_copy_boundary_splitting_a_crlf_keeps_both_halves_honest() -> None:
    world = World(CRLF_TEXT)
    world.copy(0, 3)  # ends between CR and LF of the first CRLF
    world.copy(8, 10)  # starts between CR and LF of the third terminator
    for unknown in (False, True):
        content = world.content([(0, len(CRLF_TEXT))], unknown=unknown)
        out = world.redirect().apply(content)
        assert world.bytes_of(out) == CRLF_TEXT
        assert_facts(world, out)
        assert out.first_is_lf is False
        assert any(piece.src == 0 for piece in out.pieces)
        assert out.pieces[0].src != 0


def test_the_cut_halves_of_a_crlf_count_one_break_when_known() -> None:
    world = World(b"x\r\ny")
    world.copy(0, 2)  # "x\r" copied, "\ny" stays original
    out = world.redirect().apply(world.content([(0, 4)]))
    assert world.bytes_of(out) == b"x\r\ny"
    assert out.breaks == 1
    assert out.pieces[0].last_is_cr
    assert out.pieces[1].first_is_lf


def test_a_copy_that_joins_a_cr_tail_of_the_store_reads_back_the_same() -> None:
    world = World(b"q\nr\r\ns\n")
    world.store.append(b"\r")  # the open tail ends in CR; the copy starts with LF
    world.copy(1, 3)  # "\nr"
    world.copy(3, 6)  # "\r\ns"
    out = world.redirect().apply(world.content([(0, 7)]))
    assert world.bytes_of(out) == b"q\nr\r\ns\n"
    assert_facts(world, out)


def test_unknown_pieces_keep_honest_junction_flags() -> None:
    world = World(b"ab\r\ncd\r\nef")
    world.copy(2, 3)  # the CR of the first CRLF
    world.copy(7, 8)  # the LF of the second CRLF
    content = world.content([(0, 10)], unknown=True)
    out = world.redirect().apply(content)
    assert world.bytes_of(out) == b"ab\r\ncd\r\nef"
    original_parts = [piece for piece in out.pieces if piece.src == 0]
    assert original_parts
    assert all(piece.unknown for piece in original_parts)
    for piece in out.pieces:
        data = world.read(piece)
        assert piece.first_is_lf == (data[:1] == b"\n")
        assert piece.last_is_cr == (data[-1:] == b"\r")
    assert out.first_is_lf is False
    assert out.last_is_cr is False
    assert out.length == content.length
    assert out.unknown == sum(1 for piece in out.pieces if piece.unknown)
    assert_facts(world, out)


def test_an_unknown_piece_is_not_made_known_outside_the_copies() -> None:
    world = World(b"0123456789")
    world.copy(4, 6)
    out = world.redirect().apply(world.content([(0, 10)], unknown=True))
    outside = [piece for piece in out.pieces if piece.src == 0]
    assert [(p.a, p.b) for p in outside] == [(0, 4), (6, 10)]
    assert all(piece.unknown for piece in outside)
    assert not any(piece.unknown for piece in out.pieces if piece.src != 0)


def test_pieces_outside_the_copies_are_returned_unchanged() -> None:
    world = World(b"abc\ndef\nghi\njkl\n")
    world.copy(8, 11)
    outside = world.content([(0, 4), (12, 16)])
    assert world.redirect().apply(outside) is outside
    mixed = world.content([(0, 4), (6, 10), (12, 16)])
    out = world.redirect().apply(mixed)
    assert out.pieces[0] == mixed.pieces[0]
    assert out.pieces[-1] == mixed.pieces[-1]
    assert world.bytes_of(out) == world.bytes_of(mixed)


def test_pieces_of_the_add_store_and_other_generations_pass_through() -> None:
    world = World(b"abcdef")
    world.copy(0, 6)
    segment, a, b = world.store.append(b"new")
    added = make_piece(world.store.segment(segment), segment, a, b)
    legacy = dataclasses.replace(Content.from_pieces([added], 0), pieces=(added._replace(src=(1 << 24) | 1),))
    content = Content.from_pieces([added], 0)
    assert world.redirect().apply(content) is content
    assert world.redirect().apply(legacy) is legacy
    assert generation_of(legacy.pieces[0].src) == 1


def test_adjacent_copies_merge_into_one_piece_when_contiguous_in_the_store() -> None:
    world = World(b"abcdefgh")
    world.copy(0, 4)
    world.copy(4, 8)
    out = world.redirect().apply(world.content([(0, 8)]))
    assert world.bytes_of(out) == b"abcdefgh"
    assert len(out.pieces) == 1
    assert out.pieces[0].src != 0


def test_empty_copies_and_empty_content() -> None:
    world = World(b"abc")
    content = world.content([(0, 3)])
    assert world.redirect().apply(content) is content
    world.copy(0, 1)
    assert world.redirect().apply(EMPTY) is EMPTY


def test_copies_must_be_disjoint_and_well_formed() -> None:
    world = World(b"abcdefgh")
    for bad in ([Copy(0, 4, 1, 0), Copy(3, 6, 1, 4)], [Copy(2, 2, 1, 0)], [Copy(0, 2, 1, -1)]):
        with pytest.raises(ValueError, match=r"copy|copies"):
            Redirect(bad, cast("PieceSource", world.source), world.store)


def test_unsorted_copies_are_accepted() -> None:
    world = World(b"abcdefgh")
    world.copy(5, 7)
    world.copy(0, 2)
    out = world.redirect().apply(world.content([(0, 8)]))
    assert world.bytes_of(out) == b"abcdefgh"
    assert not world.inside_copies(out)


def test_apply_mutates_nothing_shared() -> None:
    world = World(b"ab\r\ncd\r\nef")
    world.copy(2, 5)
    content = world.content([(0, 10)])
    snapshot = (content, content.pieces, world.store.segment_count, world.copies[:])
    world.redirect().apply(content)
    assert (content, content.pieces, world.store.segment_count, world.copies) == snapshot


# -- history and clipboard through the real API ------------------------------------------------------------------------------------
ORIGINAL = b"abc\r\ndef\nghi\rjkl\r\nmno"


def seeded() -> tuple[LazyDocument, EditHistory]:
    doc = LazyDocument.from_bytes(ORIGINAL, SMALL)
    history = new_history()
    for start, end, data in ((2, 7, b""), (0, 0, b"ZZ"), (10, 14, b"Q\r"), (4, 6, b"")):
        history.record(hex_edit(doc, start, end, data))
        history.checkpoint()
    history.undo(doc)
    return doc, history


def copies_for(doc: LazyDocument, ranges: list[tuple[int, int]]) -> Redirect:
    store = doc._table._add_store
    copies = []
    for a, b in ranges:
        segment, start, _ = store.append(ORIGINAL[a:b])
        copies.append(Copy(a, b, segment, start))
    return Redirect(copies, doc._table.source_of(0), store)


def test_history_references_leave_the_copied_ranges_and_keep_their_bytes() -> None:
    doc, history = seeded()
    before = [doc._table.content_bytes(c, 0, c.length) for c in history.references()]
    assert list(history.original_ranges())
    redirect = copies_for(doc, [(0, 9), (9, 15)])
    history.map_references(redirect.apply)
    after = [doc._table.content_bytes(c, 0, c.length) for c in history.references()]
    assert after == before
    copied = [(0, 15)]
    assert not [r for r in history.original_ranges() if any(r[0] < b and a < r[1] for a, b in copied)]
    doc.close()


def test_history_still_undoes_and_redoes_to_the_same_bytes_after_a_redirect() -> None:
    doc, history = seeded()
    redirect = copies_for(doc, [(2, 8), (10, 14)])
    expected_before = doc.read_bytes(0, doc.length)
    history.map_references(redirect.apply)
    assert doc.read_bytes(0, doc.length) == expected_before
    history.redo(doc)
    redone = doc.read_bytes(0, doc.length)
    history.undo(doc)
    assert doc.read_bytes(0, doc.length) == expected_before
    history.redo(doc)
    assert doc.read_bytes(0, doc.length) == redone
    doc.close()


def test_a_clipboard_shaped_content_goes_through_the_same_function() -> None:
    doc = LazyDocument.from_bytes(ORIGINAL, SMALL)
    content = doc._table.content(3, 12)
    redirect = copies_for(doc, [(0, 8)])
    out = redirect.apply(content)
    assert doc._table.content_bytes(out, 0, out.length) == ORIGINAL[3:12]
    assert not [p for p in out.pieces if p.src == 0 and p.a < 8]
    doc.close()


def test_map_references_is_all_or_nothing_with_a_redirect_that_fails_on_the_third_content() -> None:
    doc, history = seeded()
    redirect = copies_for(doc, [(0, 15)])
    before = history.references()
    calls = [0]

    def flaky(content: Content) -> Content:
        calls[0] += 1
        if calls[0] == 3:
            raise RuntimeError("boom")
        return redirect.apply(content)

    with pytest.raises(RuntimeError):
        history.map_references(flaky)
    assert all(a is b for a, b in zip(history.references(), before, strict=True))
    history.map_references(redirect.apply)
    assert not [r for r in history.original_ranges() if r[0] < 15]
    doc.close()


def test_a_byte_edit_referencing_the_original_is_moved_whole() -> None:
    doc = LazyDocument.from_bytes(ORIGINAL, SMALL)
    history = new_history()
    entry = hex_edit(doc, 0, 21, b"")
    history.record(entry)
    assert isinstance(entry, ByteEdit)
    redirect = copies_for(doc, [(0, 21)])
    history.map_references(redirect.apply)
    assert list(history.original_ranges()) == []
    assert entry.removed is not None
    assert doc._table.content_bytes(entry.removed, 0, entry.removed.length) == ORIGINAL
    doc.close()
