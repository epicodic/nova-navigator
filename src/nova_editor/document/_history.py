from __future__ import annotations

import time
from collections import deque
from collections.abc import Callable, Iterator
from dataclasses import dataclass, field
from itertools import islice
from typing import TYPE_CHECKING, Any, Literal

from nova_editor.core.pieces import Content, generation_of, segment_of
from nova_editor.document._byte_edit import ByteEdit, HistoryException
from nova_editor.document._document import EditResult
from nova_editor.document._errors import RowUnavailable

if TYPE_CHECKING:
    from nova_editor.document._lazy_document import LazyDocument

__all__ = ["Batch", "EditHistory", "HistoryException", "HistoryRolledBack", "HistoryStep", "Mover"]

Mover = Callable[[ByteEdit, Literal["undo", "redo"]], EditResult]
"""Applies one entry in the given direction and returns the result; a view passes its own to keep cursors and selections in step."""


class Batch(list[Any]):
    """A batch of edits that are undone and redone together, with the revision states around it.

    A state is a `(revision, branch)` pair.
    The elements are `ByteEdit` entries or view entries that extend them (the text view's `Edit`), so the element type is left open:
    a view and its tests use the richer entry API without a cast.
    """

    def __init__(self, edits: list[ByteEdit], revision_before: int, branch_before: int, branch: int) -> None:
        super().__init__(edits)
        self.revision_before = revision_before
        """Revision of the document before the batch."""
        self.revision_after = revision_before + 1
        """Revision of the document after the batch (it grows when edits are coalesced into the batch)."""
        self.branch_before = branch_before
        """Branch id of the state before the batch."""
        self.branch = branch
        """Branch id of the batch (and of the state after it)."""


class HistoryRolledBack(Exception):
    """A refused undo or redo could not be rolled back: the document no longer matches the history."""


@dataclass
class HistoryStep:
    """What one undo or redo did, for the view that refreshes itself afterwards."""

    direction: Literal["undo", "redo"]
    """Which way the batch was moved."""

    batch: Batch
    """The batch that was moved."""

    applied: list[ByteEdit]
    """The entries in the order they were applied (an undo applies the batch last to first)."""

    results: list[EditResult]
    """One result per applied entry, in the same order."""

    byte: int
    """Cursor hint: after an undo the start of the first entry of the batch, after a redo the end of the last entry's insertion."""

    low: int
    """Lowest byte offset any applied entry touched."""

    high_old: int
    """Highest end offset of the replaced range, before the step."""

    high_new: int
    """Highest end offset of the replacement, after the step."""


@dataclass
class EditHistory:
    """Manages batching/checkpointing of Edits into groups that can be undone/redone, and owns the byte-level undo/redo loop.

    Use it on the UI thread only; the save thread works on the snapshot returned by `references()`.
    """

    max_checkpoints: int | None = None
    """Maximum number of batches kept in the undo stack; `None` keeps them all."""

    checkpoint_timer: float = 2.0
    """Maximum number of seconds since last edit until a new batch is created."""

    checkpoint_max_characters: int = 100
    """Maximum number of characters that can appear in a batch before a new batch is formed."""

    _last_edit_time: float = field(init=False, default_factory=time.monotonic)

    _character_count: int = field(init=False, default=0)
    """Track number of characters replaced + inserted since last batch creation."""

    _force_end_batch: bool = field(init=False, default=False)
    """Flag to force the creation of a new batch for the next recorded edit."""

    _previously_replaced: bool = field(init=False, default=False)
    """Records whether the most recent edit was a replacement or a pure insertion.
    
    If an edit removes any text from the document at all, it's considered a replacement.
    Every other edit is considered a pure insertion.
    """

    def __post_init__(self) -> None:
        self._revision = 0
        self._branch = 0
        self._next_branch = 1
        self._saved = (0, 0)
        self._undo_stack: deque[Batch] = deque(maxlen=self.max_checkpoints)
        """Batching Edit operations together (edits are simply grouped together in lists)."""
        self._redo_stack: deque[Batch] = deque()
        """Stores batches that have been undone, allowing them to be redone."""

    def record(self, edit: ByteEdit) -> None:
        """Record an Edit so that it may be undone and redone.

        Determines whether to batch the Edit with previous Edits, or create a new batch/checkpoint.

        This method must be called exactly once per edit, in chronological order.

        A new batch/checkpoint is created when:

        - The undo stack is empty.
        - The checkpoint timer expires.
        - The maximum number of characters permitted in a checkpoint is reached.
        - A redo is performed (we should not add new edits to a batch that has been redone).
        - The programmer has requested a new batch via a call to `force_new_batch`.
            - e.g. the TextArea widget may call this method in some circumstances.
            - Clicking to move the cursor elsewhere in the document should create a new batch.
            - Movement of the cursor via a keyboard action that is NOT an edit.
            - Blurring the TextArea creates a new checkpoint.
        - The current edit involves a deletion/replacement and the previous edit did not.
        - The current edit is a pure insertion and the previous edit was not.
        - The edit involves insertion or deletion of one or more newline characters.
        - An edit which inserts more than a single character (a paste) gets an isolated batch.

        Within a batch, an insertion that starts where the previous insertion ended, and a deletion that touches the previous deletion, are merged
        into the previous `Edit`, so a batch of typing holds one record.
        A batch ends after `checkpoint_max_characters` characters, so a million keystrokes leave about 10,000 batches of one `Edit` each.

        Args:
            edit: The edit to record.
        """
        shape = edit.shape()
        if shape is None:
            return
        is_replacement, contains_newline = shape
        undo_stack = self._undo_stack
        current_time = self._get_time()
        edit_characters = edit.characters
        branch_before = self._branch
        if self._redo_stack:
            # A record after an undo leaves the branch that holds the redo stack, so the revision counter alone cannot reach a state of that branch.
            self._branch = self._next_branch
            self._next_branch += 1
            self._force_end_batch = True

        # Determine whether to create a new batch, or add to the latest batch.
        if (
            not undo_stack
            or self._force_end_batch
            or undo_stack[-1][-1].origin != edit.origin
            or edit_characters > 1
            or contains_newline
            or is_replacement != self._previously_replaced
            or current_time - self._last_edit_time > self.checkpoint_timer
            or self._character_count + edit_characters > self.checkpoint_max_characters
        ):
            # Create a new batch (creating a "checkpoint").
            undo_stack.append(Batch([edit], self._revision, branch_before, self._branch))
            self._character_count = edit_characters
            self._last_edit_time = current_time
            self._force_end_batch = False
        else:
            # Update the latest batch; adjacent typing and adjacent backspace or delete runs extend the previous edit.
            batch = undo_stack[-1]
            if not batch[-1].coalesce(edit):
                batch.append(edit)
            batch.revision_after += 1
            self._character_count += edit_characters
            self._last_edit_time = current_time

        self._revision += 1
        self._previously_replaced = is_replacement
        self._redo_stack.clear()

        # For some edits, we want to ensure the NEXT edit cannot be added to its batch,
        # so enforce a checkpoint now.
        if contains_newline or edit_characters > 1:
            self.checkpoint()

    def _pop_undo(self) -> Batch | None:
        """Pop the latest batch from the undo stack and return it.

        This will also place it on the redo stack.

        Returns:
            The batch of Edits from the top of the undo stack or None if it's empty.
        """
        undo_stack = self._undo_stack
        redo_stack = self._redo_stack
        if undo_stack:
            batch = undo_stack.pop()
            redo_stack.append(batch)
            self._revision = batch.revision_before
            self._branch = batch.branch_before
            return batch
        return None

    def _pop_redo(self) -> Batch | None:
        """Redo the latest batch on the redo stack and return it.

        This will also place it on the undo stack (with a forced checkpoint to ensure
        this undo does not get batched with other edits).

        Returns:
            The batch of Edits from the top of the redo stack or None if it's empty.
        """
        undo_stack = self._undo_stack
        redo_stack = self._redo_stack
        if redo_stack:
            batch = redo_stack.pop()
            undo_stack.append(batch)
            self._revision = batch.revision_after
            self._branch = batch.branch
            # Ensure edits which follow cannot be added to the redone batch.
            self.checkpoint()
            return batch
        return None

    def _restore_undo(self, batch: Batch) -> None:
        """Put back a batch that `_pop_undo` returned but that was refused (it is again the top of the undo stack)."""
        if self._redo_stack and self._redo_stack[-1] is batch:
            self._redo_stack.pop()
        self._undo_stack.append(batch)
        self._revision = batch.revision_after
        self._branch = batch.branch

    def _restore_redo(self, batch: Batch) -> None:
        """Put back a batch that `_pop_redo` returned but that was refused (it is again the top of the redo stack)."""
        if self._undo_stack and self._undo_stack[-1] is batch:
            self._undo_stack.pop()
        self._redo_stack.append(batch)
        self._revision = batch.revision_before
        self._branch = batch.branch_before

    def undo(self, document: LazyDocument, mover: Mover | None = None) -> HistoryStep | None:
        """Undo the newest batch, all or nothing.

        Args:
            document: The document the entries were recorded on.
            mover: Applies one entry; the default calls `ByteEdit.revert` and `ByteEdit.apply`.

        Returns:
            What was done, or `None` when there is nothing to undo.

        Raises:
            RowUnavailable: An entry was refused; the entries applied so far were reversed and the history is unchanged.
            HistoryRolledBack: The reversal itself was refused; the document no longer matches the history.
        """
        batch = self._pop_undo()
        if batch is None:
            return None
        move = mover or _document_mover(document)
        applied: list[ByteEdit] = []
        results: list[EditResult] = []
        with document.change_kind("undo"):
            try:
                for entry in reversed(batch):
                    results.append(move(entry, "undo"))
                    applied.append(entry)
            except RowUnavailable as error:
                failure = self._reverse(applied, "redo", move)
                self._restore_undo(batch)
                if failure is not None:
                    raise HistoryRolledBack(str(failure)) from error
                raise
        return self._step("undo", batch, applied, results)

    def redo(self, document: LazyDocument, mover: Mover | None = None) -> HistoryStep | None:
        """Redo the newest undone batch, all or nothing (see `undo` for the arguments and errors)."""
        batch = self._pop_redo()
        if batch is None:
            return None
        move = mover or _document_mover(document)
        applied: list[ByteEdit] = []
        results: list[EditResult] = []
        with document.change_kind("redo"):
            try:
                for entry in batch:
                    results.append(move(entry, "redo"))
                    applied.append(entry)
            except RowUnavailable as error:
                failure = self._reverse(applied, "undo", move)
                self._restore_redo(batch)
                if failure is not None:
                    raise HistoryRolledBack(str(failure)) from error
                raise
        return self._step("redo", batch, applied, results)

    @staticmethod
    def _reverse(applied: list[ByteEdit], direction: Literal["undo", "redo"], move: Mover) -> RowUnavailable | None:
        """Move the entries applied before a refusal back, newest first.

        Returns:
            The refusal that stopped the reversal, or `None` when the document is as before.
        """
        try:
            for entry in reversed(applied):
                move(entry, direction)
        except RowUnavailable as failure:
            return failure
        return None

    @staticmethod
    def _step(direction: Literal["undo", "redo"], batch: Batch, applied: list[ByteEdit], results: list[EditResult]) -> HistoryStep:
        """Build the report of a finished step."""
        low = high_old = high_new = 0
        spans: list[tuple[int, int, int]] = []
        for entry in applied:
            if entry.start_byte is None or entry.removed is None or entry.inserted is None:
                continue
            old, new = (entry.inserted, entry.removed) if direction == "undo" else (entry.removed, entry.inserted)
            spans.append((entry.start_byte, entry.start_byte + old.length, entry.start_byte + new.length))
        if spans:
            low = min(span[0] for span in spans)
            high_old = max(span[1] for span in spans)
            high_new = max(span[2] for span in spans)
        if direction == "undo":
            byte = batch[0].start_byte or 0
        else:
            last = batch[-1]
            byte = (last.start_byte or 0) + (last.inserted.length if last.inserted is not None else 0)
        return HistoryStep(direction, batch, applied, results, byte, low, high_old, high_new)

    def peek_undo(self) -> Batch | None:
        """The batch an undo would move, without moving it (O(1), unlike `undo_stack`)."""
        return self._undo_stack[-1] if self._undo_stack else None

    def peek_redo(self) -> Batch | None:
        """The batch a redo would move, without moving it (O(1), unlike `redo_stack`)."""
        return self._redo_stack[-1] if self._redo_stack else None

    def set_max_checkpoints(self, count: int | None) -> None:
        """Limit the undo stack to the newest `count` batches (`None` keeps them all)."""
        self.max_checkpoints = count
        self._undo_stack = deque(self._undo_stack, maxlen=count)

    def _entries(self) -> Iterator[ByteEdit]:
        """Every entry in the order the reference API visits them: the undo stack, then the redo stack, oldest first."""
        for batch in (*self._undo_stack, *self._redo_stack):
            yield from batch

    def references(self) -> list[Content]:
        """Every `Content` the history holds, in the order `rewrite_references` consumes them."""
        return [content for entry in self._entries() for content in entry.contents()]

    def rewrite_references(self, translated: Iterator[Content]) -> None:
        """Replace every referenced `Content` by the next one of `translated`, in `references()` order.

        Exactly `len(references())` items are taken from the iterator, so a caller may hand over one shared iterator.

        Raises:
            ValueError: `translated` ran dry; nothing was changed.
        """
        needed = len(self.references())
        chunk = list(islice(translated, needed))
        if len(chunk) < needed:
            raise ValueError("too few contents")
        items = iter(chunk)
        for entry in self._entries():
            entry.rewrite(items)

    def map_references(self, fn: Callable[[Content], Content]) -> None:
        """Replace every referenced `Content` by `fn(content)`; when `fn` raises, nothing is changed."""
        mapped = [fn(content) for content in self.references()]
        self.rewrite_references(iter(mapped))

    def original_ranges(self) -> Iterator[tuple[int, int]]:
        """The byte ranges `(a, b)` of the original file (generation 0, segment 0) that the history still references."""
        for content in self.references():
            for piece in content.pieces:
                if generation_of(piece.src) == 0 and segment_of(piece.src) == 0:
                    yield piece.a, piece.b

    def clear(self) -> None:
        """Completely clear the history; the current text is then the saved state (`modified` is false)."""
        self._undo_stack.clear()
        self._redo_stack.clear()
        self._revision = 0
        self._branch = 0
        self._next_branch = 1
        self._saved = (0, 0)
        self._last_edit_time = time.monotonic()
        self._force_end_batch = False
        self._previously_replaced = False

    def checkpoint(self) -> None:
        """Ensure the next recorded edit starts a new batch."""
        self._force_end_batch = True

    @property
    def revision(self) -> int:
        """The number of recorded edits on the current branch up to the current state (undo and redo move it with the batch)."""
        return self._revision

    @property
    def branch(self) -> int:
        """The id of the current branch; a record after an undo opens a new one."""
        return self._branch

    def mark_saved(self) -> None:
        """Record the current state as the saved one and start a new batch, so that coalesced typing never spans a save."""
        self._saved = (self._revision, self._branch)
        self.checkpoint()

    @property
    def modified(self) -> bool:
        """Whether the current state differs from the saved one (the opening state until `mark_saved` is called)."""
        return (self._revision, self._branch) != self._saved

    @property
    def undo_stack(self) -> list[Batch]:
        """A copy of the undo stack, with references to the original Edits."""
        return list(self._undo_stack)

    @property
    def redo_stack(self) -> list[Batch]:
        """A copy of the redo stack, with references to the original Edits."""
        return list(self._redo_stack)

    def _get_time(self) -> float:
        """Get the time from the monotonic clock.

        Returns:
            The result of `time.monotonic()` as a float.
        """
        return time.monotonic()


def _document_mover(document: LazyDocument) -> Mover:
    """The mover for plain entries: `revert` on undo, `apply` on redo, both on `document`."""

    def move(entry: ByteEdit, direction: Literal["undo", "redo"]) -> EditResult:
        return entry.revert(document) if direction == "undo" else entry.apply(document)

    return move
