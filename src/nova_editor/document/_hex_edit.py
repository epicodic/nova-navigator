"""The history entry of the hex view: a `ByteEdit` that knows how typing, deleting and pasting group (ACT5)."""

from __future__ import annotations

from typing import Literal

from nova_editor.core.pieces import Content
from nova_editor.document._byte_edit import ByteEdit, EditShape
from nova_editor.document._edit import _join

HexKind = Literal["type", "delete", "paste"]
"""What produced a `HexEdit`: typed digits or characters, Delete/Backspace/Cut, or a paste."""


class HexEdit(ByteEdit):
    """A byte splice made in the hex view (`origin="hex"`), with the grouping rules of the text view's typing.

    The history decides undo grouping by batch membership; `coalesce` only saves entries.
    Entries with unknown pieces, line breaks or non-ASCII bytes are never merged.
    """

    kind: HexKind
    """What produced the entry; `"paste"` always gets its own batch, `"delete"` entries count as replacements."""

    completes: bool
    """Whether the entry typed the low digit of a byte whose high digit was typed just before (joins that batch)."""

    def __init__(
        self,
        start_byte: int,
        removed: Content,
        inserted: Content,
        *,
        kind: HexKind = "type",
        completes: bool = False,
        origin: str = "hex",
        cursor_before: int | None = None,
        cursor_after: int | None = None,
    ) -> None:
        super().__init__(start_byte, removed, inserted, origin=origin, cursor_before=cursor_before, cursor_after=cursor_after)
        self.kind = kind
        self.completes = completes

    def shape(self) -> EditShape | None:
        """The batching facts: typing never counts as a replacement, so a mode switch does not split a run of typing."""
        shape = super().shape()
        if shape is None:
            return None
        return EditShape(shape.is_replacement and self.kind != "type" and not self.completes, shape.may_contain_newline)

    @property
    def characters(self) -> int:
        """The size the history counts for batching; a paste counts at least two, so it gets its own batch."""
        _, removed, inserted = self._parts()
        if self.kind == "paste":
            return max(2, inserted.length)
        return max(inserted.length, removed.length)

    def coalesce(self, later: ByteEdit) -> bool:
        """Merge `later`, performed right after this entry, when it extends the same run of typing or deleting.

        The merged cases are adjacent insertions, the low digit completing a one-byte insertion, adjacent equal-length overwrites,
        and forward or backward runs of single-byte deletions.
        Nothing merges when a content holds a line break, an unknown piece or a non-ASCII byte.

        Returns:
            True when `later` was absorbed (the caller drops it), False when the two stay separate.
        """
        if not isinstance(later, HexEdit) or later.origin != self.origin or self.start_byte is None or later.start_byte is None:
            return False
        if self.removed is None or self.inserted is None or later.removed is None or later.inserted is None:
            return False
        if not all(_plain(content) for content in (self.removed, self.inserted, later.removed, later.inserted)):
            return False
        start, removed, inserted = self.start_byte, self.removed, self.inserted
        typed = self.kind == "type" and later.kind == "type"
        if typed and removed.length == 0 and later.removed.length == 0 and inserted.length and later.inserted.length:
            if later.start_byte != start + inserted.length:
                return False
            self.inserted = _join(inserted, later.inserted)
        elif typed and later.completes and removed.length == 0 and inserted.length == 1 and later.start_byte == start and later.removed.length == 1:
            self.inserted = later.inserted
        elif typed and removed.length == inserted.length > 0 and later.removed.length == later.inserted.length > 0:
            if later.start_byte != start + inserted.length:
                return False
            self.removed = _join(removed, later.removed)
            self.inserted = _join(inserted, later.inserted)
        elif self.kind == "delete" and later.kind == "delete" and inserted.length == 0 and later.inserted.length == 0 and removed.length and later.removed.length:
            if later.start_byte + later.removed.length == start:  # backspace run: the new bytes precede
                self.removed = _join(later.removed, removed)
                self.start_byte = later.start_byte
            elif later.start_byte == start:  # delete run: the new bytes follow
                self.removed = _join(removed, later.removed)
            else:
                return False
        else:
            return False
        self.cursor_before = self.start_byte
        self.cursor_after = self.start_byte + self.inserted.length
        return True


def _plain(content: Content) -> bool:
    """Whether `content` has no line break, no unknown piece and only single-byte characters (so the sum of tail characters stays exact)."""
    return content.breaks == 0 and content.unknown == 0 and content.tail_chars == content.length
