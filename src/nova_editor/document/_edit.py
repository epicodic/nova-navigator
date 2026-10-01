from __future__ import annotations

from dataclasses import dataclass, field
from typing import TYPE_CHECKING

from nova_editor.core.pieces import Content, merge_pieces
from nova_editor.document._document import EditResult, Location, Selection
from nova_editor.document._lazy_document import LazyDocument

if TYPE_CHECKING:
    from nova_editor.widget._text_area import NovaTextArea as TextArea


@dataclass
class Edit:
    """Implements the Undoable protocol to replace text at some range within a document."""

    text: str
    """The text to insert. An empty string is equivalent to deletion."""

    from_location: Location
    """The start location of the insert."""

    to_location: Location
    """The end location of the insert"""

    maintain_selection_offset: bool
    """If True, the selection will maintain its offset to the replacement range."""

    _original_selection: Selection | None = field(init=False, default=None)
    """The Selection when the edit was originally performed, to be restored on undo."""

    _updated_selection: Selection | None = field(init=False, default=None)
    """Where the selection should move to after the replace happens."""

    _edit_result: EditResult | None = field(init=False, default=None)
    """The result of doing the edit."""

    start_byte: int | None = field(init=False, default=None)
    """Byte offset where the edit started (lazy documents; set by `do`)."""

    removed: Content | None = field(init=False, default=None)
    """The bytes the edit removed, as piece references (lazy documents; set by `do`)."""

    inserted: Content | None = field(init=False, default=None)
    """The bytes the edit inserted, as piece references (lazy documents; set by `do`)."""

    end_location: Location | None = field(init=False, default=None)
    """The location after the inserted text (set by `do`)."""

    def do(self, text_area: TextArea, record_selection: bool = True) -> EditResult:
        """Perform the edit operation.

        Args:
            text_area: The `TextArea` to perform the edit on.
            record_selection: If True, record the current selection in the TextArea
                so that it may be restored if this Edit is undone in the future.

        Returns:
            An `EditResult` containing information about the replace operation.
        """
        if record_selection:
            self._original_selection = text_area.selection

        text = self.text

        # This code is mostly handling how we adjust TextArea.selection
        # when an edit is made to the document programmatically.
        # We want a user who is typing away to maintain their relative
        # position in the document even if an insert happens before
        # their cursor position.

        edit_bottom_row, edit_bottom_column = self.bottom

        selection_start, selection_end = text_area.selection
        selection_start_row, selection_start_column = selection_start
        selection_end_row, selection_end_column = selection_end

        document = text_area.document
        if self.inserted is not None and self.removed is not None and self.start_byte is not None and isinstance(document, LazyDocument):
            # Redo: the byte offsets are exact because undo and redo are strictly last in, first out.
            edit_result = document.splice_bytes(self.start_byte, self.start_byte + self.removed.length, self.inserted)
        else:
            edit_result = document.replace_range(self.top, self.bottom, text)
            if edit_result.removed is not None and edit_result.inserted is not None and edit_result.start_byte is not None:
                self.start_byte = edit_result.start_byte
                self.removed = edit_result.removed
                self.inserted = edit_result.inserted

        new_edit_to_row, new_edit_to_column = edit_result.end_location

        column_offset = new_edit_to_column - edit_bottom_column
        target_selection_start_column = selection_start_column + column_offset if edit_bottom_row == selection_start_row and edit_bottom_column <= selection_start_column else selection_start_column
        target_selection_end_column = selection_end_column + column_offset if edit_bottom_row == selection_end_row and edit_bottom_column <= selection_end_column else selection_end_column

        row_offset = new_edit_to_row - edit_bottom_row
        target_selection_start_row = selection_start_row + row_offset if edit_bottom_row <= selection_start_row else selection_start_row
        target_selection_end_row = selection_end_row + row_offset if edit_bottom_row <= selection_end_row else selection_end_row

        if self.maintain_selection_offset:
            self._updated_selection = Selection(
                start=(target_selection_start_row, target_selection_start_column),
                end=(target_selection_end_row, target_selection_end_column),
            )
        else:
            self._updated_selection = Selection.cursor(edit_result.end_location)

        self._edit_result = edit_result
        self.end_location = edit_result.end_location
        return edit_result

    def undo(self, text_area: TextArea) -> EditResult:
        """Undo the edit operation.

        Looks at the data stored in the edit, and performs the inverse operation of `Edit.do`.

        Args:
            text_area: The `TextArea` to undo the insert operation on.

        Returns:
            An `EditResult` containing information about the replace operation.
        """
        assert self._edit_result is not None, "undo() called before do()"
        document = text_area.document
        if self.start_byte is not None and self.removed is not None and self.inserted is not None and isinstance(document, LazyDocument):
            # Replace the inserted span by the removed pieces; no text is read.
            undo_result = document.splice_bytes(self.start_byte, self.start_byte + self.inserted.length, self.removed)
            self._updated_selection = self._original_selection
            return undo_result
        replaced_text = self._edit_result.replaced_text
        edit_end = self._edit_result.end_location

        # Replace the span of the edit with the text that was originally there.
        undo_edit_result = text_area.document.replace_range(self.top, edit_end, replaced_text)
        self._updated_selection = self._original_selection

        return undo_edit_result

    def after(self, text_area: TextArea) -> None:
        """Hook for running code after an Edit has been performed via `Edit.do` *and*
        side effects such as re-wrapping the document and refreshing the display
        have completed.

        For example, we can't record cursor visual offset until we know where the cursor will
        land *after* wrapping has been performed, so we must wait until here to do it.

        Args:
            text_area: The `TextArea` this operation was performed on.
        """
        if self._updated_selection is not None:
            text_area.selection = self._updated_selection
        text_area.record_cursor_width()

    def coalesce(self, later: Edit) -> bool:
        """Merge `later`, performed right after this edit, into this edit when both are pure insertions or both pure deletions that touch.

        Args:
            later: The edit that was just performed after this one.

        Returns:
            True when `later` was absorbed (the caller drops it), False when the two stay separate.
        """
        start, removed, inserted, result = self.start_byte, self.removed, self.inserted, self._edit_result
        later_start, later_removed, later_inserted, later_result = later.start_byte, later.removed, later.inserted, later._edit_result
        if start is None or removed is None or inserted is None or result is None:
            return False
        if later_start is None or later_removed is None or later_inserted is None or later_result is None:
            return False
        if removed.breaks or inserted.breaks or later_removed.breaks or later_inserted.breaks:
            return False
        if removed.length == 0 and later_removed.length == 0 and inserted.length and later_inserted.length:
            if later_start != start + inserted.length or _has_escape(later.text):
                return False
            self.inserted = _join(inserted, later_inserted)
            self.text += later.text
        elif inserted.length == 0 and later_inserted.length == 0 and removed.length and later_removed.length:
            if later_start + later_removed.length == start:  # backspace run: the new bytes precede
                self.removed = _join(later_removed, removed)
                self.start_byte = later_start
                self.from_location = later.top
                self.to_location = self.bottom
            elif later_start == start:  # delete run: the new bytes follow
                self.removed = _join(removed, later_removed)
                self.to_location = max(self.bottom, later.bottom)
                self.from_location = self.top
            else:
                return False
        else:
            return False
        self.end_location = later.end_location
        self._updated_selection = later._updated_selection
        self._edit_result = EditResult(later_result.end_location, "", self.removed, self.start_byte, self.inserted)
        return True

    @property
    def top(self) -> Location:
        """The Location impacted by this edit that is nearest the start of the document."""
        return min([self.from_location, self.to_location])

    @property
    def bottom(self) -> Location:
        """The Location impacted by this edit that is nearest the end of the document."""
        return max([self.from_location, self.to_location])


def _has_escape(text: str) -> bool:
    """Whether `text` holds an escaped invalid byte (U+DC80 to U+DCFF), whose bytes may merge with neighbours."""
    return any("\udc80" <= char <= "\udcff" for char in text)


def _join(left: Content, right: Content) -> Content:
    """Concatenate two contents without line breaks; adjacent pieces of one source merge into one piece."""
    pieces = list(left.pieces)
    rest = list(right.pieces)
    if pieces and rest and (merged := merge_pieces(pieces[-1], rest[0])) is not None:
        pieces[-1] = merged
        rest.pop(0)
    return Content.from_pieces([*pieces, *rest], left.tail_chars + right.tail_chars)
