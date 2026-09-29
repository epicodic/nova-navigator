"""Provisional and pending cursor states for long rows (Textual-free, document layer).

The byte position is the truth; the column is a label (exact when ``RESOLVED``, an estimate otherwise).
Choices where the design leaves room:

* ``END`` is expressed as ``jump_to_byte(step(anchor, huge))`` so it is clamped by the index and follows the same
  RESOLVED / PROVISIONAL / PENDING rules as any other jump.
* ``WORD_LEFT`` / ``WORD_RIGHT`` move by a fixed ``WORD_STEP_CHARS`` characters: the machine has no text, so the
  widget refines word moves itself while the row is resolved.
* While ``PENDING`` every ``apply`` is ``IGNORED`` (the first deferred operation wins); ``jump_to_byte`` retargets.
* The estimate column is monotone in the byte position across moves and clamped to ``estimate_length()`` when the
  index also implements :class:`EstimateLengthIndex`.
"""

from __future__ import annotations

from dataclasses import dataclass
from enum import Enum
from typing import Protocol, runtime_checkable

WORD_STEP_CHARS = 8
"""Characters moved by one word operation while the machine has no text to look at."""

_END_STEP = 1 << 62
"""Step count that always reaches the end of a row (``step`` clamps)."""


class CursorState(Enum):
    """State of the cursor on a long row."""

    RESOLVED = "resolved"
    PROVISIONAL = "provisional"
    PENDING = "pending"


class AnchorIndex(Protocol):
    """What the machine needs from one long row."""

    def frontier_byte(self) -> int:
        """Return the bytes (relative to the row start) scanned so far."""
        ...

    def complete(self) -> bool:
        """Return whether the whole row has been scanned."""
        ...

    def exact_column(self, byte_rel: int) -> int | None:
        """Return the exact column of a byte offset, or ``None`` beyond the frontier."""
        ...

    def approx_column(self, byte_rel: int) -> int:
        """Return the column of a byte offset, estimated beyond the frontier."""
        ...

    def step(self, byte_rel: int, chars: int) -> int:
        """Return the byte offset ``chars`` characters away (negative is left), clamped to the row."""
        ...


@runtime_checkable
class EstimateLengthIndex(Protocol):
    """Optional extension of :class:`AnchorIndex`: an upper bound for estimated columns."""

    def estimate_length(self) -> int:
        """Return the estimated length of the row in columns."""
        ...


@dataclass(frozen=True)
class Anchor:
    """Cursor position on a long row: exact byte, column exact when resolved."""

    row: int
    byte_rel: int
    column: int


class Op(Enum):
    """Operations the widget asks the machine about."""

    LEFT = "left"
    RIGHT = "right"
    WORD_LEFT = "word_left"
    WORD_RIGHT = "word_right"
    HOME = "home"
    END = "end"
    UP = "up"
    DOWN = "down"
    PAGE_UP = "page_up"
    PAGE_DOWN = "page_down"
    SELECT_EXACT = "select_exact"
    GOTO_COLUMN = "goto_column"
    WRAP_TOGGLE = "wrap_toggle"


class Verdict(Enum):
    """Answer to a request."""

    DONE = "done"
    PENDING = "pending"
    IGNORED = "ignored"


_RELATIVE_MOVES = {Op.LEFT: -1, Op.RIGHT: 1, Op.WORD_LEFT: -WORD_STEP_CHARS, Op.WORD_RIGHT: WORD_STEP_CHARS}


class CursorMachine:
    """Cursor state machine of one long row (design 7.2)."""

    def __init__(self, row: int, index: AnchorIndex, anchor_byte: int = 0, *, wrap: bool = False) -> None:
        """Create a machine; the initial anchor is resolved when its column is known, else provisional."""
        self._row = row
        self._index = index
        self._wrap = wrap
        self._pending_op: Op | None = None
        self._target: int | None = None
        exact = index.exact_column(anchor_byte)
        if exact is None:
            self._state = CursorState.PROVISIONAL
            self._anchor = Anchor(row, anchor_byte, self._estimate(anchor_byte, None))
            self._previous_resolved = Anchor(row, 0, 0)
        else:
            self._state = CursorState.RESOLVED
            self._anchor = Anchor(row, anchor_byte, exact)
            self._previous_resolved = self._anchor

    @property
    def state(self) -> CursorState:
        """The current state."""
        return self._state

    @property
    def anchor(self) -> Anchor:
        """The current anchor (unchanged while PENDING: the cursor is not moved)."""
        return self._anchor

    def take_pending_op(self) -> Op | None:
        """Return the deferred operation to replay after resolution, once."""
        op, self._pending_op = self._pending_op, None
        return op

    def _estimate(self, byte_rel: int, previous: Anchor | None) -> int:
        column = self._index.approx_column(byte_rel)
        if isinstance(self._index, EstimateLengthIndex):
            column = min(column, self._index.estimate_length())
        if previous is not None:
            column = max(column, previous.column) if byte_rel >= previous.byte_rel else min(column, previous.column)
        return max(0, column)

    def _resolve_at(self, byte_rel: int, column: int) -> None:
        self._anchor = Anchor(self._row, byte_rel, column)
        self._previous_resolved = self._anchor
        self._state = CursorState.RESOLVED
        self._target = None

    def jump_to_byte(self, byte_rel: int) -> Verdict:
        """Move to a byte offset: RESOLVED when known, else PROVISIONAL (no wrap) or PENDING (wrap)."""
        exact = self._index.exact_column(byte_rel)
        if exact is not None:
            self._pending_op = None
            self._resolve_at(byte_rel, exact)
            return Verdict.DONE
        if self._wrap:
            self._state = CursorState.PENDING
            self._target = byte_rel
            return Verdict.PENDING
        self._pending_op = None
        self._target = None
        self._anchor = Anchor(self._row, byte_rel, self._estimate(byte_rel, self._anchor))
        self._state = CursorState.PROVISIONAL
        return Verdict.DONE

    def apply(self, op: Op) -> Verdict:
        """Ask for an operation; see the transition table in design 7.2."""
        if self._state is CursorState.PENDING:
            return Verdict.IGNORED
        if op in _RELATIVE_MOVES:
            return self.jump_to_byte(self._index.step(self._anchor.byte_rel, _RELATIVE_MOVES[op]))
        if op is Op.END:
            return self.jump_to_byte(self._index.step(self._anchor.byte_rel, _END_STEP))
        if op is Op.HOME:
            self._resolve_at(0, 0)
            return Verdict.DONE
        if self._state is CursorState.RESOLVED:
            return Verdict.DONE
        self._pending_op = op
        self._target = self._anchor.byte_rel
        self._state = CursorState.PENDING
        return Verdict.PENDING

    def on_frontier(self) -> bool:
        """Reconcile after the frontier advanced; return True when the state changed to RESOLVED."""
        if self._state is CursorState.RESOLVED:
            return False
        byte_rel = self._anchor.byte_rel if self._target is None else self._target
        exact = self._index.exact_column(byte_rel)
        if exact is None:
            return False
        self._resolve_at(byte_rel, exact)
        return True

    def cancel(self) -> None:
        """Drop a pending jump and return to the previous resolved anchor (no-op unless PENDING)."""
        if self._state is not CursorState.PENDING:
            return
        self._anchor = self._previous_resolved
        self._state = CursorState.RESOLVED
        self._pending_op = None
        self._target = None

    def set_wrap(self, wrap: bool) -> None:
        """Change the wrap mode; a PROVISIONAL cursor becomes PENDING on the same byte when wrap turns on."""
        self._wrap = wrap
        if wrap and self._state is CursorState.PROVISIONAL:
            self._state = CursorState.PENDING
            self._target = self._anchor.byte_rel
        elif not wrap and self._state is CursorState.PENDING and self._pending_op is None and self._target is not None:
            self._anchor = Anchor(self._row, self._target, self._estimate(self._target, self._anchor))
            self._state = CursorState.PROVISIONAL
            self._target = None
