"""Tests for the cursor state machine: every row of the transition table in design 7.2."""

from __future__ import annotations

import pytest
from hypothesis import given, settings
from hypothesis import strategies as st

from nova_editor.document._cursor_anchor import CursorMachine, CursorState, Op, Verdict


class FakeRow:
    """AnchorIndex over a plain string with a movable frontier."""

    def __init__(self, text: str) -> None:
        self.text = text
        self.data = text.encode()
        self.frontier = 0

    def frontier_byte(self) -> int:
        return self.frontier

    def complete(self) -> bool:
        return self.frontier >= len(self.data)

    def exact_column(self, byte_rel: int) -> int | None:
        return len(self.data[:byte_rel].decode("utf-8", "surrogateescape")) if byte_rel <= self.frontier else None

    def approx_column(self, byte_rel: int) -> int:
        exact = self.exact_column(byte_rel)
        return exact if exact is not None else byte_rel // 2

    def step(self, byte_rel: int, chars: int) -> int:
        col = len(self.data[:byte_rel].decode("utf-8", "surrogateescape"))
        col = max(0, min(len(self.text), col + chars))
        return len(self.text[:col].encode())


class LimitedRow(FakeRow):
    """FakeRow that also provides an estimate length."""

    def __init__(self, text: str, limit: int) -> None:
        super().__init__(text)
        self.limit = limit

    def estimate_length(self) -> int:
        return self.limit


class JumpyRow(FakeRow):
    """FakeRow whose estimate is not monotone at one byte."""

    def approx_column(self, byte_rel: int) -> int:
        return 1000 if byte_rel == 500 else byte_rel // 2


DEFERRED = [Op.UP, Op.DOWN, Op.PAGE_UP, Op.PAGE_DOWN, Op.SELECT_EXACT, Op.GOTO_COLUMN, Op.WRAP_TOGGLE]


def _provisional(n: int = 1000, at: int = 900) -> tuple[FakeRow, CursorMachine]:
    row = FakeRow("a" * n)
    machine = CursorMachine(0, row)
    machine.jump_to_byte(at)
    assert machine.state is CursorState.PROVISIONAL
    return row, machine


# RESOLVED rows


def test_resolved_jump_inside_frontier_is_done() -> None:
    row = FakeRow("é" * 100)
    row.frontier = 200
    machine = CursorMachine(0, row)
    assert machine.state is CursorState.RESOLVED
    assert machine.jump_to_byte(100) is Verdict.DONE
    assert machine.state is CursorState.RESOLVED
    assert machine.anchor.column == 50


def test_end_beyond_frontier_is_provisional_and_resolves_in_place() -> None:
    row = FakeRow("é" * 1000)
    machine = CursorMachine(0, row)
    assert machine.jump_to_byte(2000) is Verdict.DONE
    assert machine.state is CursorState.PROVISIONAL
    before = machine.anchor.byte_rel
    row.frontier = 2000
    assert machine.on_frontier() is True
    assert machine.state is CursorState.RESOLVED
    assert machine.anchor.byte_rel == before
    assert machine.anchor.column == 1000


def test_wrap_far_jump_is_pending_and_cancel_restores() -> None:
    row = FakeRow("a" * 1000)
    row.frontier = 100
    machine = CursorMachine(0, row, anchor_byte=10, wrap=True)
    assert machine.jump_to_byte(900) is Verdict.PENDING
    assert machine.state is CursorState.PENDING
    assert machine.anchor.byte_rel == 10
    machine.cancel()
    assert machine.state is CursorState.RESOLVED
    assert machine.anchor.byte_rel == 10
    assert machine.anchor.column == 10


def test_wrap_pending_resolves_at_target() -> None:
    row = FakeRow("a" * 1000)
    machine = CursorMachine(0, row, wrap=True)
    machine.jump_to_byte(900)
    row.frontier = 500
    assert machine.on_frontier() is False
    assert machine.state is CursorState.PENDING
    row.frontier = 1000
    assert machine.on_frontier() is True
    assert machine.state is CursorState.RESOLVED
    assert (machine.anchor.byte_rel, machine.anchor.column) == (900, 900)
    assert machine.take_pending_op() is None


@pytest.mark.parametrize("op", DEFERRED)
def test_resolved_every_op_is_done(op: Op) -> None:
    row = FakeRow("a" * 100)
    row.frontier = 100
    machine = CursorMachine(0, row, anchor_byte=50)
    assert machine.apply(op) is Verdict.DONE
    assert machine.state is CursorState.RESOLVED
    assert machine.anchor.byte_rel == 50


def test_resolved_home_and_horizontal_steps() -> None:
    row = FakeRow("abcdefghijklmnopqrstuvwxyz")
    row.frontier = 26
    machine = CursorMachine(0, row, anchor_byte=10)
    assert machine.apply(Op.LEFT) is Verdict.DONE
    assert machine.anchor.byte_rel == 9
    assert machine.apply(Op.RIGHT) is Verdict.DONE
    assert machine.apply(Op.RIGHT) is Verdict.DONE
    assert machine.anchor.byte_rel == 11
    assert machine.apply(Op.WORD_RIGHT) is Verdict.DONE
    assert machine.anchor.byte_rel > 11
    assert machine.apply(Op.WORD_LEFT) is Verdict.DONE
    assert machine.anchor.byte_rel == 11
    assert machine.apply(Op.HOME) is Verdict.DONE
    assert (machine.anchor.byte_rel, machine.anchor.column) == (0, 0)
    assert machine.apply(Op.END) is Verdict.DONE
    assert (machine.anchor.byte_rel, machine.anchor.column) == (26, 26)
    assert machine.state is CursorState.RESOLVED


def test_resolved_right_beyond_frontier_becomes_provisional() -> None:
    row = FakeRow("a" * 100)
    row.frontier = 10
    machine = CursorMachine(0, row, anchor_byte=10)
    assert machine.apply(Op.RIGHT) is Verdict.DONE
    assert machine.state is CursorState.PROVISIONAL


def test_resolved_end_beyond_frontier_is_provisional_at_row_end() -> None:
    row = FakeRow("a" * 100)
    machine = CursorMachine(0, row)
    machine.apply(Op.END)
    assert machine.state is CursorState.PROVISIONAL
    assert machine.anchor.byte_rel == 100


def test_resolved_cancel_and_on_frontier_are_noops() -> None:
    row = FakeRow("a" * 100)
    row.frontier = 100
    machine = CursorMachine(0, row, anchor_byte=5)
    machine.cancel()
    assert machine.state is CursorState.RESOLVED
    assert machine.anchor.byte_rel == 5
    assert machine.on_frontier() is False


def test_set_wrap_in_resolved_keeps_state() -> None:
    row = FakeRow("a" * 100)
    row.frontier = 100
    machine = CursorMachine(0, row, anchor_byte=5)
    machine.set_wrap(True)
    assert machine.state is CursorState.RESOLVED
    assert machine.jump_to_byte(50) is Verdict.DONE


# PROVISIONAL rows


def test_provisional_frontier_before_anchor_stays_provisional() -> None:
    row, machine = _provisional()
    row.frontier = 899
    assert machine.on_frontier() is False
    assert machine.state is CursorState.PROVISIONAL


@pytest.mark.parametrize("op", DEFERRED)
def test_provisional_defers_exact_operations(op: Op) -> None:
    row, machine = _provisional()
    assert machine.apply(op) is Verdict.PENDING
    assert machine.state is CursorState.PENDING
    row.frontier = 1000
    assert machine.on_frontier() is True
    assert machine.state is CursorState.RESOLVED
    assert machine.anchor.byte_rel == 900
    assert machine.take_pending_op() is op
    assert machine.take_pending_op() is None


def test_provisional_home_resolves_without_scan() -> None:
    _, machine = _provisional()
    assert machine.apply(Op.HOME) is Verdict.DONE
    assert machine.state is CursorState.RESOLVED
    assert (machine.anchor.byte_rel, machine.anchor.column) == (0, 0)


@pytest.mark.parametrize(("op", "delta"), [(Op.LEFT, -1), (Op.RIGHT, 1)])
def test_provisional_single_char_moves(op: Op, delta: int) -> None:
    _, machine = _provisional()
    assert machine.apply(op) is Verdict.DONE
    assert machine.state is CursorState.PROVISIONAL
    assert machine.anchor.byte_rel == 900 + delta


@pytest.mark.parametrize("op", [Op.WORD_LEFT, Op.WORD_RIGHT])
def test_provisional_word_moves_are_relative(op: Op) -> None:
    _, machine = _provisional()
    assert machine.apply(op) is Verdict.DONE
    assert machine.state is CursorState.PROVISIONAL
    assert (machine.anchor.byte_rel < 900) == (op is Op.WORD_LEFT)
    assert machine.anchor.byte_rel != 900


def test_provisional_end_is_done_and_stays_provisional() -> None:
    _, machine = _provisional()
    assert machine.apply(Op.END) is Verdict.DONE
    assert machine.state is CursorState.PROVISIONAL
    assert machine.anchor.byte_rel == 1000


def test_provisional_jump_inside_frontier_resolves() -> None:
    row, machine = _provisional()
    row.frontier = 500
    assert machine.jump_to_byte(400) is Verdict.DONE
    assert machine.state is CursorState.RESOLVED


def test_provisional_wrap_on_becomes_pending_same_byte() -> None:
    row, machine = _provisional()
    machine.set_wrap(True)
    assert machine.state is CursorState.PENDING
    assert machine.anchor.byte_rel == 900
    row.frontier = 1000
    assert machine.on_frontier() is True
    assert machine.state is CursorState.RESOLVED
    assert machine.anchor.byte_rel == 900
    assert machine.take_pending_op() is None


def test_provisional_wrap_off_is_noop() -> None:
    _, machine = _provisional()
    machine.set_wrap(False)
    assert machine.state is CursorState.PROVISIONAL


def test_provisional_cancel_is_noop() -> None:
    _, machine = _provisional()
    machine.cancel()
    assert machine.state is CursorState.PROVISIONAL
    assert machine.anchor.byte_rel == 900


def test_estimate_column_is_clamped_to_estimate_length() -> None:
    row = LimitedRow("a" * 1000, limit=100)
    machine = CursorMachine(0, row)
    machine.jump_to_byte(900)
    assert machine.anchor.column == 100


def test_estimate_column_is_monotone_in_byte_position() -> None:
    machine = CursorMachine(0, JumpyRow("a" * 1000))
    columns = []
    for byte_rel in (400, 500, 600, 700):
        machine.jump_to_byte(byte_rel)
        columns.append(machine.anchor.column)
    assert columns == sorted(columns)
    machine.jump_to_byte(300)
    assert machine.anchor.column <= columns[-1]
    machine.jump_to_byte(100)
    machine.jump_to_byte(200)
    assert machine.anchor.column >= 50


# PENDING rows


def test_pending_apply_is_ignored_and_keeps_state() -> None:
    _, machine = _provisional()
    machine.apply(Op.UP)
    for op in Op:
        assert machine.apply(op) is Verdict.IGNORED
    assert machine.state is CursorState.PENDING


def test_pending_keeps_first_deferred_op() -> None:
    row, machine = _provisional()
    machine.apply(Op.PAGE_DOWN)
    machine.apply(Op.UP)
    row.frontier = 1000
    machine.on_frontier()
    assert machine.take_pending_op() is Op.PAGE_DOWN


def test_pending_from_provisional_op_cancel_restores_previous_resolved() -> None:
    row = FakeRow("a" * 1000)
    row.frontier = 20
    machine = CursorMachine(0, row, anchor_byte=7)
    machine.jump_to_byte(900)
    machine.apply(Op.DOWN)
    machine.cancel()
    assert machine.state is CursorState.RESOLVED
    assert machine.anchor.byte_rel == 7
    assert machine.take_pending_op() is None


def test_pending_jump_retargets() -> None:
    row = FakeRow("a" * 1000)
    machine = CursorMachine(0, row, wrap=True)
    machine.jump_to_byte(900)
    assert machine.jump_to_byte(300) is Verdict.PENDING
    row.frontier = 400
    assert machine.on_frontier() is True
    assert machine.anchor.byte_rel == 300


def test_pending_jump_inside_frontier_resolves() -> None:
    row = FakeRow("a" * 1000)
    machine = CursorMachine(0, row, wrap=True)
    machine.jump_to_byte(900)
    row.frontier = 100
    assert machine.jump_to_byte(50) is Verdict.DONE
    assert machine.state is CursorState.RESOLVED
    assert machine.anchor.byte_rel == 50


def test_pending_wrap_off_without_op_becomes_provisional_at_target() -> None:
    row = FakeRow("a" * 1000)
    machine = CursorMachine(0, row, wrap=True)
    machine.jump_to_byte(900)
    machine.set_wrap(False)
    assert machine.state is CursorState.PROVISIONAL
    assert machine.anchor.byte_rel == 900


def test_pending_wrap_off_with_op_stays_pending() -> None:
    _, machine = _provisional()
    machine.apply(Op.UP)
    machine.set_wrap(False)
    assert machine.state is CursorState.PENDING


def test_pending_wrap_on_stays_pending() -> None:
    _, machine = _provisional()
    machine.apply(Op.UP)
    machine.set_wrap(True)
    assert machine.state is CursorState.PENDING


def test_pending_frontier_short_of_target_stays_pending() -> None:
    row, machine = _provisional()
    machine.apply(Op.UP)
    row.frontier = 500
    assert machine.on_frontier() is False
    assert machine.state is CursorState.PENDING


# property


@settings(deadline=None, max_examples=200, derandomize=True)
@given(moves=st.lists(st.sampled_from([Op.LEFT, Op.RIGHT]), max_size=60), text=st.text(alphabet="abé日", min_size=1, max_size=200))
def test_provisional_moves_match_reference_after_resolve(moves: list[Op], text: str) -> None:
    row = FakeRow(text)
    machine = CursorMachine(0, row)
    machine.jump_to_byte(len(row.data))
    reference = len(text)
    for op in moves:
        machine.apply(op)
        reference = max(0, min(len(text), reference + (1 if op is Op.RIGHT else -1)))
    row.frontier = len(row.data)
    machine.on_frontier()
    assert machine.state is CursorState.RESOLVED
    assert machine.anchor.column == reference
