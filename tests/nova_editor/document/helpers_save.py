"""Helpers of the save and rebase tests: a planner over a `LazyDocument`, `run_save`, and an editing session with undo, redo and a clipboard."""

from __future__ import annotations

import re
from dataclasses import dataclass
from pathlib import Path

from nova_editor.core import Content, LineIndex, PreadSource
from nova_editor.core.pieces import aggregate_of_bytes
from nova_editor.core.rebase import RebasePlan
from nova_editor.core.save import PlanPart, SaveJob, SaveResult, SaveSettings
from nova_editor.document._document import EditResult
from nova_editor.document._edit import Edit
from nova_editor.document._lazy_config import LazyConfig
from nova_editor.document._lazy_document import LazyDocument
from tests.nova_editor.document.reference_text import RefText, decode, encode

SMALL = LazyConfig(stride=4, index_long_line_threshold=64, long_row_threshold=512, word_wrap_limit=128, checkpoint_chars=64)
SAVE_SETTINGS = SaveSettings(chunk=7, fsync_every=64, stride=4, long_line_threshold=64)
JOIN_SECONDS = 10.0
ROW_SPLIT = re.compile(rb"\r\n|\n|\r")


class DocPlanner:
    """`SavePlanner` over a `LazyDocument` (the document's `length` is a property, the planner's a method)."""

    def __init__(self, doc: LazyDocument) -> None:
        self._doc = doc

    def length(self) -> int:
        return self._doc.length

    def plan(self, offset: int, limit: int, unverified: bool) -> list[PlanPart]:
        return self._doc.plan(offset, limit, unverified)


def open_pread_document(directory: Path, data: bytes, config: LazyConfig = SMALL, name: str = "doc.bin") -> tuple[LazyDocument, bytes]:
    """Write `data` to a file in `directory` and open it through a `PreadSource`; return the document and the bytes."""
    path = directory / name
    path.write_bytes(data)
    return LazyDocument.from_path(path, config), data


def save_result(doc: LazyDocument, target: Path, settings: SaveSettings = SAVE_SETTINGS) -> SaveResult:
    """Run a `SaveJob` over `doc` with its edits locked (the document is not rebased)."""
    doc.lock_edits("saving")
    doc.begin_save()
    try:
        return SaveJob(DocPlanner(doc), target, settings, progress=None, foreground=None).run()
    finally:
        doc.end_save()
        doc.unlock_edits()


def run_save(doc: LazyDocument, target: Path, contents: list[Content], settings: SaveSettings = SAVE_SETTINGS) -> tuple[SaveResult, RebasePlan]:
    """Save `doc` to `target` and rebase it: edits are locked from the first plan until the rebase is applied."""
    doc.lock_edits("saving")
    doc.begin_save()
    try:
        result = SaveJob(DocPlanner(doc), target, settings, progress=None, foreground=None).run()
        plan = doc.prepare_rebase(result, contents)
        doc.apply_rebase(plan)
    finally:
        doc.end_save()
        doc.unlock_edits()
    return result, plan


@dataclass
class Entry:
    """One recorded edit with the document bytes before and after it."""

    edit: Edit
    before: bytes
    after: bytes


def same_rows(doc: LazyDocument, fresh: LineIndex) -> None:
    """Assert that `doc` and a freshly scanned index of the same bytes agree on every row."""
    assert doc.line_count == fresh.snapshot().count
    for row in range(doc.line_count):
        found = fresh.row_range(row)
        assert found is not None
        assert doc.byte_offset(row, 0) == found.start
        assert doc.row_byte_length(row) == found.content_end - found.start


class Session:
    """A document with the byte reference of its content, an undo and a redo stack of real `Edit` records, and an internal clipboard."""

    def __init__(self, doc: LazyDocument, original: bytes, directory: Path) -> None:
        self.doc = doc
        self.directory = directory
        self.original = original
        self.ref = RefText(decode(original))
        self.undo_stack: list[Entry] = []
        self.redo_stack: list[Entry] = []
        self.clipboard: tuple[Content, bytes] | None = None
        self.saves = 0
        self.cleared = 0

    # -- editing -----------------------------------------------------------------------------
    @staticmethod
    def _record(edit: Edit, result: EditResult) -> None:
        edit.start_byte = result.start_byte
        edit.removed = result.removed
        edit.inserted = result.inserted
        edit.end_location = result.end_location
        edit._edit_result = result

    def edit(self, start: tuple[int, int], end: tuple[int, int], text: str) -> None:
        """Replace the text between two locations in the document and in the model."""
        before = self.ref.data()
        self.ref.replace_range(start, end, text)
        result = self.doc.replace_range(start, end, text)
        edit = Edit(text, start, end, False)
        self._record(edit, result)
        self.undo_stack.append(Entry(edit, before, self.ref.data()))
        self.redo_stack.clear()

    def copy(self, start: tuple[int, int], end: tuple[int, int]) -> None:
        """Put the bytes between two locations on the internal clipboard."""
        top, bottom = sorted((start, end))
        content = self.doc.selection_content(top, bottom)
        self.clipboard = (content, encode(self.ref.text[self.ref.offset(top) : self.ref.offset(bottom)]))

    def paste(self, location: tuple[int, int]) -> None:
        """Insert the clipboard bytes at `location` (nothing happens without a clipboard)."""
        if self.clipboard is None:
            return
        content, data = self.clipboard
        before = self.ref.data()
        at = len(encode(self.ref.text[: self.ref.offset(location)]))
        self.ref.text = decode(before[:at] + data + before[at:])
        result = self.doc.splice(location, location, content)
        edit = Edit("", location, location, False, insert_content=content)
        self._record(edit, result)
        self.undo_stack.append(Entry(edit, before, self.ref.data()))
        self.redo_stack.clear()

    def undo(self) -> bool:
        """Undo the last edit; return whether there was one."""
        if not self.undo_stack:
            return False
        entry = self.undo_stack.pop()
        edit = entry.edit
        assert edit.start_byte is not None
        assert edit.inserted is not None
        assert edit.removed is not None
        self.doc.splice_bytes(edit.start_byte, edit.start_byte + edit.inserted.length, edit.removed)
        self.ref.text = decode(entry.before)
        self.redo_stack.append(entry)
        return True

    def redo(self) -> bool:
        """Redo the last undone edit; return whether there was one."""
        if not self.redo_stack:
            return False
        entry = self.redo_stack.pop()
        edit = entry.edit
        assert edit.start_byte is not None
        assert edit.inserted is not None
        assert edit.removed is not None
        self.doc.splice_bytes(edit.start_byte, edit.start_byte + edit.removed.length, edit.inserted)
        self.ref.text = decode(entry.after)
        self.undo_stack.append(entry)
        return True

    # -- saving ------------------------------------------------------------------------------
    def references(self) -> list[Content]:
        """Every content the session holds, in the order `rewrite` consumes them."""
        found = [content for entry in [*self.undo_stack, *self.redo_stack] for content in entry.edit.contents()]
        if self.clipboard is not None:
            found.append(self.clipboard[0])
        return found

    def save(self, target: Path) -> RebasePlan:
        """Save and rebase the document, then write the translated contents back (or clear the history when the plan says so)."""
        _, plan = run_save(self.doc, target, self.references())
        self.saves += 1
        if plan.clear_history:
            self.undo_stack.clear()
            self.redo_stack.clear()
            self.clipboard = None
            self.cleared += 1
            return plan
        translated = iter(plan.contents)
        for entry in [*self.undo_stack, *self.redo_stack]:
            entry.edit.rewrite(translated)
        if self.clipboard is not None:
            self.clipboard = (next(translated), self.clipboard[1])
        assert next(translated, None) is None
        return plan

    # -- checks ------------------------------------------------------------------------------
    def check_content(self, content: Content, expected: bytes) -> None:
        """Assert that `content` still names `expected` through the document's current sources."""
        assert self.doc._table.content_bytes(content, 0, content.length) == expected
        assert content.aggregate == aggregate_of_bytes(expected)

    def check_records(self) -> None:
        """Every held reference reads the bytes it was made from."""
        for entry in [*self.undo_stack, *self.redo_stack]:
            edit = entry.edit
            assert edit.start_byte is not None
            assert edit.removed is not None
            assert edit.inserted is not None
            start = edit.start_byte
            self.check_content(edit.removed, entry.before[start : start + edit.removed.length])
            self.check_content(edit.inserted, entry.after[start : start + edit.inserted.length])
            result = edit._edit_result
            assert result is not None
            assert result.removed is not None
            assert result.inserted is not None
            self.check_content(result.removed, entry.before[start : start + edit.removed.length])
            self.check_content(result.inserted, entry.after[start : start + edit.inserted.length])
            if edit.insert_content is not None:
                self.check_content(edit.insert_content, entry.after[start : start + edit.inserted.length])
        if self.clipboard is not None:
            self.check_content(*self.clipboard)

    def check_document(self) -> None:
        """The document holds the model's bytes with the model's row structure."""
        data = self.ref.data()
        assert self.doc.read_bytes(0, self.doc.length) == data
        assert self.doc.length == len(data)
        assert self.doc.snapshot().count == len(ROW_SPLIT.split(data))

    def check_saved(self, target: Path) -> None:
        """The file holds the model's bytes and the document agrees with a fresh scan of it."""
        data = self.ref.data()
        assert target.read_bytes() == data
        self.check_document()
        source = PreadSource(target)
        try:
            fresh = LineIndex(source, stride=4, long_line_threshold=64)
            fresh.scan_now()
            same_rows(self.doc, fresh)
        finally:
            source.close()
