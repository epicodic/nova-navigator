"""The view-neutral history entry: a byte splice described by piece references."""

from __future__ import annotations

from collections.abc import Iterator
from typing import TYPE_CHECKING, NamedTuple

from nova_editor.core.pieces import Content
from nova_editor.document._document import EditResult

if TYPE_CHECKING:
    from nova_editor.document._lazy_document import LazyDocument


class HistoryException(Exception):
    """Indicates misuse of the EditHistory API.

    For example, trying to undo() an Edit that has yet to be done.
    """


class EditShape(NamedTuple):
    """What the history needs to know to batch an entry."""

    is_replacement: bool
    """Whether the entry removed bytes."""

    may_contain_newline: bool
    """Whether it may hold a line break (`breaks` is only a lower bound while `unknown > 0`)."""


class ByteEdit:
    """A splice `[start_byte, start_byte + removed.length) -> inserted`, recorded by any view.

    Entries are strictly last in, first out, so the offsets are exact.
    The text view's `Edit` adds locations and selections; the hex view records plain `ByteEdit` objects with `origin="hex"`.
    """

    origin: str = "hex"
    """The view that made the entry (`"text"` or `"hex"`); entries of different origins never share a batch."""

    cursor_before: int | None = None
    """Byte offset where a view should place its cursor after undoing the entry."""

    cursor_after: int | None = None
    """Byte offset where a view should place its cursor after redoing the entry."""

    start_byte: int | None
    """Byte offset where the splice starts."""

    removed: Content | None
    """The bytes the splice removed, as piece references."""

    inserted: Content | None
    """The bytes the splice inserted, as piece references."""

    def __init__(
        self,
        start_byte: int,
        removed: Content,
        inserted: Content,
        *,
        origin: str = "hex",
        cursor_before: int | None = None,
        cursor_after: int | None = None,
    ) -> None:
        self.start_byte = start_byte
        self.removed = removed
        self.inserted = inserted
        self.origin = origin
        self.cursor_before = start_byte if cursor_before is None else cursor_before
        self.cursor_after = start_byte + inserted.length if cursor_after is None else cursor_after

    def _parts(self) -> tuple[int, Content, Content]:
        """The performed splice, or a `HistoryException` when the entry holds none yet."""
        if self.start_byte is None or self.removed is None or self.inserted is None:
            raise HistoryException("Cannot add an edit to history before it has been performed using `Edit.do`.")
        return self.start_byte, self.removed, self.inserted

    def apply(self, document: LazyDocument) -> EditResult:
        """Redo: replace `[start, start + removed.length)` by `inserted`."""
        start, removed, inserted = self._parts()
        return document.splice_bytes(start, start + removed.length, inserted)

    def revert(self, document: LazyDocument) -> EditResult:
        """Undo: replace `[start, start + inserted.length)` by `removed`."""
        start, removed, inserted = self._parts()
        return document.splice_bytes(start, start + inserted.length, removed)

    def shape(self) -> EditShape | None:
        """The batching facts, or `None` for a no-op (the history then ignores the entry)."""
        _, removed, inserted = self._parts()
        if removed.length == 0 and inserted.length == 0:
            return None
        newline = any(content.breaks > 0 or content.unknown > 0 for content in (removed, inserted))
        return EditShape(removed.length > 0, newline)

    def coalesce(self, later: ByteEdit) -> bool:
        """Merge `later` into this entry; the base never merges.

        Returns:
            True when `later` was absorbed (the caller drops it), False when the two stay separate.
        """
        return False

    def contents(self) -> list[Content]:
        """Every `Content` held, in the order `rewrite` consumes them."""
        return [content for content in (self.removed, self.inserted) if content is not None]

    def rewrite(self, contents: Iterator[Content]) -> None:
        """Replace the contents listed by `contents()` with the next ones of `contents`, in the same order."""
        if self.removed is not None:
            self.removed = next(contents)
        if self.inserted is not None:
            self.inserted = next(contents)

    @property
    def characters(self) -> int:
        """The size the history counts for batching (one byte counts as one character)."""
        _, removed, inserted = self._parts()
        return max(inserted.length, removed.length)
