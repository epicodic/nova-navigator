"""`HexEdit`, the history entry of the hex view: shape, characters, coalescing and grouping in a real `EditHistory` (ACT5 commit 1)."""

from __future__ import annotations

from typing import TYPE_CHECKING, Literal

from nova_editor.core.pieces import Content
from nova_editor.document._history import EditHistory
from nova_editor.document._lazy_document import LazyDocument
from tests.nova_editor.document.test_lazy_edit import SMALL

if TYPE_CHECKING:
    from nova_editor.document._hex_edit import HexEdit

EMPTY = Content.from_pieces((), 0)
Kind = Literal["type", "delete", "paste"]


class ClockHistory(EditHistory):
    """An `EditHistory` whose clock the test sets."""

    clock: float = 0.0

    def _get_time(self) -> float:
        return self.clock


def new_history(*, timer: float = 2.0, chars: int = 100) -> ClockHistory:
    return ClockHistory(checkpoint_timer=timer, checkpoint_max_characters=chars)


def make(doc: LazyDocument, start: int, end: int, data: bytes, *, kind: Kind = "type", completes: bool = False) -> HexEdit:
    """Apply a byte splice to `doc` and wrap the result the way the hex view will."""
    from nova_editor.document._hex_edit import HexEdit

    content = doc.content_of_bytes(data) if data else EMPTY
    result = doc.splice_bytes(start, end, content)
    assert result.removed is not None
    assert result.inserted is not None
    assert result.start_byte is not None
    return HexEdit(result.start_byte, result.removed, result.inserted, kind=kind, completes=completes)


def bytes_of(doc: LazyDocument) -> bytes:
    return doc.read_bytes(0, doc.length)


def do(doc: LazyDocument, history: EditHistory, start: int, end: int, data: bytes, *, kind: Kind = "type", completes: bool = False) -> HexEdit:
    entry = make(doc, start, end, data, kind=kind, completes=completes)
    history.record(entry)
    return entry


def undo_all(doc: LazyDocument, history: EditHistory) -> int:
    steps = 0
    while history.undo(doc) is not None:
        steps += 1
    return steps


# ----- shape and characters -------------------------------------------------------------------------------------------------------


def test_export_and_defaults() -> None:
    from nova_editor.document import HexEdit

    doc = LazyDocument.from_bytes(b"abc", SMALL)
    entry = make(doc, 1, 1, b"x")
    assert isinstance(entry, HexEdit)
    assert entry.origin == "hex"
    assert entry.kind == "type"
    assert entry.completes is False
    doc.close()


def test_noop_shape_is_none() -> None:
    from nova_editor.document._hex_edit import HexEdit

    assert HexEdit(0, EMPTY, EMPTY).shape() is None


def test_typing_is_never_a_replacement() -> None:
    doc = LazyDocument.from_bytes(b"abcdef", SMALL)
    insert = make(doc, 1, 1, b"x")
    overwrite = make(doc, 3, 4, b"y")
    completing = make(doc, 3, 4, b"z", completes=True)
    assert insert.shape() == (False, False)
    assert overwrite.shape() == (False, False)
    assert completing.shape() == (False, False)
    doc.close()


def test_delete_and_replacing_paste_are_replacements_and_a_pure_paste_is_not() -> None:
    doc = LazyDocument.from_bytes(b"abcdef", SMALL)
    assert make(doc, 0, 1, b"", kind="delete").shape() == (True, False)
    assert make(doc, 1, 2, b"QQ", kind="paste").shape() == (True, False)
    assert make(doc, 0, 0, b"QQ", kind="paste").shape() == (False, False)
    doc.close()


def test_line_breaks_count_as_newlines() -> None:
    doc = LazyDocument.from_bytes(b"ab\ncd", SMALL)
    assert make(doc, 2, 3, b"", kind="delete").shape() == (True, True)
    assert make(doc, 0, 0, b"\n").shape() == (False, True)
    doc.close()


def test_characters_per_kind() -> None:
    doc = LazyDocument.from_bytes(b"abcdef", SMALL)
    assert make(doc, 0, 0, b"x").characters == 1
    assert make(doc, 1, 2, b"y").characters == 1
    assert make(doc, 0, 1, b"", kind="delete").characters == 1
    assert make(doc, 0, 3, b"", kind="delete").characters == 3
    assert make(doc, 0, 0, b"q", kind="paste").characters == 2
    assert make(doc, 0, 0, b"qrstu", kind="paste").characters == 5
    doc.close()


# ----- coalesce -------------------------------------------------------------------------------------------------------------------


def test_adjacent_insertions_merge() -> None:
    doc = LazyDocument.from_bytes(b"abc", SMALL)
    first = make(doc, 1, 1, b"X")
    second = make(doc, 2, 2, b"Y")
    assert first.coalesce(second) is True
    assert first.start_byte == 1
    assert first.inserted is not None
    assert first.inserted.length == 2
    assert first.removed is not None
    assert first.removed.length == 0
    assert first.cursor_after == 3
    first.revert(doc)
    assert bytes_of(doc) == b"abc"
    first.apply(doc)
    assert bytes_of(doc) == b"aXYbc"
    doc.close()


def test_non_adjacent_insertions_do_not_merge() -> None:
    doc = LazyDocument.from_bytes(b"abc", SMALL)
    first = make(doc, 1, 1, b"X")
    second = make(doc, 0, 0, b"Y")
    assert first.coalesce(second) is False
    assert first.inserted is not None
    assert first.inserted.length == 1
    doc.close()


def test_the_low_digit_completes_a_one_byte_insertion() -> None:
    doc = LazyDocument.from_bytes(b"abc", SMALL)
    high = make(doc, 1, 1, b"\x40")
    low = make(doc, 1, 2, b"\x41", completes=True)
    assert high.coalesce(low) is True
    assert high.start_byte == 1
    assert high.removed is not None
    assert high.removed.length == 0
    assert high.inserted is not None
    assert high.inserted.length == 1
    assert doc.read_bytes(1, 1) == b"A"
    high.revert(doc)
    assert bytes_of(doc) == b"abc"
    high.apply(doc)
    assert bytes_of(doc) == b"aAbc"
    doc.close()


def test_a_completing_edit_elsewhere_does_not_merge() -> None:
    doc = LazyDocument.from_bytes(b"abc", SMALL)
    high = make(doc, 1, 1, b"\x40")
    elsewhere = make(doc, 3, 4, b"\x41", completes=True)
    assert high.coalesce(elsewhere) is False
    doc.close()


def test_adjacent_overwrites_merge_and_undo_restores() -> None:
    doc = LazyDocument.from_bytes(b"abcdef", SMALL)
    first = make(doc, 1, 2, b"X")
    second = make(doc, 2, 3, b"Y")
    assert first.coalesce(second) is True
    assert first.removed is not None
    assert first.removed.length == 2
    first.revert(doc)
    assert bytes_of(doc) == b"abcdef"
    first.apply(doc)
    assert bytes_of(doc) == b"aXYdef"
    doc.close()


def test_overwrites_at_the_same_offset_do_not_merge() -> None:
    doc = LazyDocument.from_bytes(b"abcdef", SMALL)
    first = make(doc, 1, 2, b"X")
    second = make(doc, 1, 2, b"Y")
    assert first.coalesce(second) is False
    doc.close()


def test_forward_delete_run_merges() -> None:
    doc = LazyDocument.from_bytes(b"abcdef", SMALL)
    first = make(doc, 1, 2, b"", kind="delete")
    second = make(doc, 1, 2, b"", kind="delete")
    assert first.coalesce(second) is True
    assert first.start_byte == 1
    assert first.removed is not None
    assert first.removed.length == 2
    first.revert(doc)
    assert bytes_of(doc) == b"abcdef"
    doc.close()


def test_backspace_run_merges_and_moves_the_start() -> None:
    doc = LazyDocument.from_bytes(b"abcdef", SMALL)
    first = make(doc, 3, 4, b"", kind="delete")
    second = make(doc, 2, 3, b"", kind="delete")
    assert first.coalesce(second) is True
    assert first.start_byte == 2
    assert first.cursor_before == 2
    first.revert(doc)
    assert bytes_of(doc) == b"abcdef"
    first.apply(doc)
    assert bytes_of(doc) == b"abef"
    doc.close()


def test_deletes_that_do_not_touch_do_not_merge() -> None:
    doc = LazyDocument.from_bytes(b"abcdef", SMALL)
    first = make(doc, 1, 2, b"", kind="delete")
    second = make(doc, 3, 4, b"", kind="delete")
    assert first.coalesce(second) is False
    doc.close()


def test_a_paste_never_merges() -> None:
    doc = LazyDocument.from_bytes(b"abcdef", SMALL)
    first = make(doc, 0, 0, b"X")
    pasted = make(doc, 1, 1, b"YZ", kind="paste")
    assert first.coalesce(pasted) is False
    assert pasted.coalesce(make(doc, 3, 3, b"Q")) is False
    doc.close()


def test_a_line_break_blocks_merging() -> None:
    doc = LazyDocument.from_bytes(b"abc", SMALL)
    first = make(doc, 1, 1, b"X")
    second = make(doc, 2, 2, b"\n")
    assert first.coalesce(second) is False
    doc.close()


def test_a_non_ascii_tail_blocks_merging() -> None:
    doc = LazyDocument.from_bytes(b"abc", SMALL)
    first = make(doc, 1, 1, "é".encode())
    second = make(doc, 3, 3, b"Y")
    assert first.coalesce(second) is False
    third = make(doc, 0, 0, b"P")
    fourth = make(doc, 1, 1, "é".encode())
    assert third.coalesce(fourth) is False
    doc.close()


def test_unknown_content_blocks_merging() -> None:
    from typing import cast

    from nova_editor.core.pieces import PieceSource, make_unknown_piece
    from nova_editor.document._hex_edit import HexEdit

    doc = LazyDocument.from_bytes(b"abcdef", SMALL)
    piece = make_unknown_piece(cast("PieceSource", doc._table._source), 0, 0, 1)
    unknown = Content.from_pieces((piece,), 0)
    first = HexEdit(0, EMPTY, unknown)
    second = make(doc, 1, 1, b"Y")
    assert first.coalesce(second) is False
    assert second.coalesce(HexEdit(2, EMPTY, unknown)) is False
    doc.close()


def test_a_foreign_origin_blocks_merging() -> None:
    from nova_editor.document._byte_edit import ByteEdit

    doc = LazyDocument.from_bytes(b"abc", SMALL)
    first = make(doc, 1, 1, b"X")
    plain = ByteEdit(2, EMPTY, first.inserted if first.inserted is not None else EMPTY, origin="hex")
    assert first.coalesce(plain) is False
    doc.close()


# ----- grouping in a real EditHistory ---------------------------------------------------------------------------------------------


def test_overwrite_digits_group_into_one_undo_step() -> None:
    doc = LazyDocument.from_bytes(b"abcdef", SMALL)
    history = new_history()
    do(doc, history, 0, 1, b"\x40")
    do(doc, history, 0, 1, b"\x41")
    do(doc, history, 1, 2, b"\x62")
    assert len(history.undo_stack) == 1
    assert history.undo(doc) is not None
    assert bytes_of(doc) == b"abcdef"
    assert history.undo(doc) is None
    doc.close()


def test_insert_high_and_low_digit_group_and_merge_into_one_entry() -> None:
    doc = LazyDocument.from_bytes(b"abc", SMALL)
    history = new_history()
    do(doc, history, 1, 1, b"\x40")
    do(doc, history, 1, 2, b"\x41", completes=True)
    assert len(history.undo_stack) == 1
    assert len(history.undo_stack[0]) == 1
    assert bytes_of(doc) == b"aAbc"
    history.undo(doc)
    assert bytes_of(doc) == b"abc"
    history.redo(doc)
    assert bytes_of(doc) == b"aAbc"
    doc.close()


def test_two_inserted_bytes_group() -> None:
    doc = LazyDocument.from_bytes(b"abc", SMALL)
    history = new_history()
    do(doc, history, 1, 1, b"\x40")
    do(doc, history, 1, 2, b"\x41", completes=True)
    do(doc, history, 2, 2, b"\x50")
    do(doc, history, 2, 3, b"\x51", completes=True)
    assert len(history.undo_stack) == 1
    assert bytes_of(doc) == b"aAQbc"
    history.undo(doc)
    assert bytes_of(doc) == b"abc"
    doc.close()


def test_delete_after_typing_starts_a_new_batch() -> None:
    doc = LazyDocument.from_bytes(b"abcdef", SMALL)
    history = new_history()
    do(doc, history, 0, 0, b"X")
    do(doc, history, 1, 2, b"", kind="delete")
    assert len(history.undo_stack) == 2
    doc.close()


def test_a_mode_switch_between_overwrite_and_insert_typing_keeps_the_batch() -> None:
    doc = LazyDocument.from_bytes(b"abcdef", SMALL)
    history = new_history()
    do(doc, history, 0, 1, b"X")
    do(doc, history, 1, 1, b"Y")
    assert len(history.undo_stack) == 1
    doc.close()


def test_delete_run_groups() -> None:
    doc = LazyDocument.from_bytes(b"abcdef", SMALL)
    history = new_history()
    do(doc, history, 1, 2, b"", kind="delete")
    do(doc, history, 1, 2, b"", kind="delete")
    do(doc, history, 1, 2, b"", kind="delete")
    assert len(history.undo_stack) == 1
    assert bytes_of(doc) == b"aef"
    history.undo(doc)
    assert bytes_of(doc) == b"abcdef"
    doc.close()


def test_a_selection_delete_is_its_own_batch_with_a_checkpoint_after_it() -> None:
    doc = LazyDocument.from_bytes(b"abcdefgh", SMALL)
    history = new_history()
    do(doc, history, 0, 1, b"", kind="delete")
    do(doc, history, 0, 3, b"", kind="delete")
    do(doc, history, 0, 1, b"", kind="delete")
    assert len(history.undo_stack) == 3
    history.undo(doc)
    history.undo(doc)
    history.undo(doc)
    assert bytes_of(doc) == b"abcdefgh"
    doc.close()


def test_a_paste_is_its_own_batch_with_a_checkpoint_after_it() -> None:
    doc = LazyDocument.from_bytes(b"abcdef", SMALL)
    history = new_history()
    do(doc, history, 0, 0, b"X")
    do(doc, history, 1, 1, b"QRS", kind="paste")
    do(doc, history, 4, 4, b"Y")
    assert [len(batch) for batch in history.undo_stack] == [1, 1, 1]
    assert undo_all(doc, history) == 3
    assert bytes_of(doc) == b"abcdef"
    doc.close()


def test_a_gap_over_the_timer_starts_a_new_batch() -> None:
    doc = LazyDocument.from_bytes(b"abcdef", SMALL)
    history = new_history()
    do(doc, history, 0, 0, b"X")
    history.clock = 1.0
    do(doc, history, 1, 1, b"Y")
    assert len(history.undo_stack) == 1
    history.clock = 4.5
    do(doc, history, 2, 2, b"Z")
    assert len(history.undo_stack) == 2
    doc.close()


def test_the_character_limit_starts_a_new_batch() -> None:
    doc = LazyDocument.from_bytes(b"abcdef", SMALL)
    history = new_history(chars=3)
    for index in range(4):
        do(doc, history, index, index, b"X")
    assert len(history.undo_stack) == 2
    doc.close()


def test_unknown_content_never_joins_a_batch() -> None:
    from typing import cast

    from nova_editor.core.pieces import PieceSource, make_unknown_piece
    from nova_editor.document._hex_edit import HexEdit

    doc = LazyDocument.from_bytes(b"abcdef", SMALL)
    history = new_history()
    piece = make_unknown_piece(cast("PieceSource", doc._table._source), 0, 0, 1)
    unknown = Content.from_pieces((piece,), 0)
    history.record(HexEdit(0, unknown, unknown))
    history.record(make(doc, 1, 1, b"Y"))
    assert len(history.undo_stack) == 2
    doc.close()


def test_a_crlf_widened_edit_is_recorded_exactly_and_undoes() -> None:
    doc = LazyDocument.from_bytes(b"a\r\nb", SMALL)
    history = new_history()
    entry = do(doc, history, 2, 2, b"X")  # between CR and LF: the splice takes the pair along
    assert entry.start_byte == 1
    assert entry.removed is not None
    assert entry.removed.length == 2
    after = bytes_of(doc)
    assert after == b"a\rX\nb"
    history.undo(doc)
    assert bytes_of(doc) == b"a\r\nb"
    history.redo(doc)
    assert bytes_of(doc) == after
    doc.close()


def test_typing_over_the_lf_of_a_crlf_undoes_and_redoes() -> None:
    doc = LazyDocument.from_bytes(b"a\r\nb", SMALL)
    history = new_history()
    do(doc, history, 2, 3, b"X")
    after = bytes_of(doc)
    assert after == b"a\rXb"
    history.undo(doc)
    assert bytes_of(doc) == b"a\r\nb"
    history.redo(doc)
    assert bytes_of(doc) == after
    doc.close()
