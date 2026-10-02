"""Lock audit of `LazyDocument` (ACT5 Task 10, DEC-26 note 1): no access to the piece table without the document lock.

The document's lock is replaced by a `TrackedLock` and its table by a `GuardedTable`; every public member is driven on four kinds of
document, from one thread and from two threads at once, and any table access made without the lock fails the test.
"""

from __future__ import annotations

import contextlib
import tempfile
import threading
from collections.abc import Callable
from typing import TYPE_CHECKING, cast

import pytest

from nova_editor.core import BytesSource, Content, SourceChanged
from nova_editor.core.save import SaveFailed, SaveJob, SaveSettings
from nova_editor.document._lazy_config import LazyConfig
from nova_editor.document._lazy_document import EditsLocked, LazyDocument, RowUnavailable, WholeLineAccess
from tests.nova_editor.document.helpers_lock import GuardedTable, TrackedLock, install_guards
from tests.nova_editor.document.helpers_save import DocPlanner

if TYPE_CHECKING:
    from tree_sitter import Query

LONG_ROWS = LazyConfig(stride=4, index_long_line_threshold=8, long_row_threshold=16, word_wrap_limit=8, checkpoint_chars=8)
TEXT = "ab\n" + "é" * 40 + "\ncd\r\nef"
ROWS = (0, 1, 2, -1)
JOIN_SECONDS = 20.0
ROUNDS = 3
EXPECTED = (RowUnavailable, WholeLineAccess, IndexError, ValueError, SourceChanged)
UNEXPECTED = (AssertionError, TypeError, AttributeError, KeyError, RuntimeError, LookupError)

Call = Callable[[LazyDocument, int], object]
SAVE_GATE = threading.RLock()
"""Saves and everything that changes the content or the edit lock take this gate: a save assumes that nothing edits the document under it (the
widget guarantees that with the edit lock; the audit drives members from two threads at random, so it serialises these few entries)."""
AUDIT_SAVE = SaveSettings(chunk=7, fsync_every=64, stride=4, long_line_threshold=8)


def _content(doc: LazyDocument) -> Content:
    return doc.selection_content((0, 0), (0, 2))


def _no_query() -> Query:
    """A stand-in for a query: a document without a syntax mirror never looks at it."""
    return cast("Query", None)


def _locked_edit(doc: LazyDocument, row: int) -> None:
    """Lock edits, try one (refused unless the other thread unlocked first), and unlock again so the other entries keep editing."""
    del row
    with SAVE_GATE:
        doc.lock_edits("audit")
        try:
            with contextlib.suppress(EditsLocked):
                doc.replace_range((0, 0), (0, 0), "x")
        finally:
            doc.unlock_edits()


def _finished_thread() -> threading.Thread:
    """A started thread that has already returned (what `join_on_close` is given when a save ends before the close)."""
    thread = threading.Thread(target=len, args=((),))
    thread.start()
    thread.join()
    return thread


def _gated(call: Call) -> Call:
    """Run `call` under `SAVE_GATE`."""

    def gated(doc: LazyDocument, row: int) -> object:
        with SAVE_GATE:
            return call(doc, row)

    return gated


def _save_and_rebase(doc: LazyDocument, row: int, *, apply: bool) -> None:
    """Save the document to a temporary file, prepare the rebase and (with `apply`) apply it, as the widget does with its edits locked."""
    del row
    with SAVE_GATE, tempfile.TemporaryDirectory() as directory:
        if doc.saving:
            return
        doc.lock_edits("saving")
        doc.begin_save()
        try:
            try:
                result = SaveJob(DocPlanner(doc), f"{directory}/audit.bin", AUDIT_SAVE, progress=None, foreground=None).run()
            except SaveFailed:
                return  # a closed document cannot be planned
            contents = [doc.selection_content((0, 0), (0, 2))] if doc.length > 2 else []
            plan = doc.prepare_rebase(result, contents)
            if apply:
                doc.apply_rebase(plan)
            else:
                result.source.close()
        finally:
            doc.end_save()
            doc.unlock_edits()


CALLS: dict[str, Call] = {
    "anchor_index": lambda d, r: d.anchor_index(r),
    "byte_offset": lambda d, r: d.byte_offset(r, 3),
    "close": lambda d, _: d.close(),
    "column_at_display": lambda d, r: d.column_at_display(r, 5),
    "column_slice": lambda d, r: d.column_slice(r, 1, 6),
    "content_text": lambda d, _: d.content_text(d.selection_content((0, 0), (0, 2))),
    "display_column": lambda d, r: d.display_column(r, 4),
    "end": lambda d, _: d.end,
    "get_line": lambda d, r: d.get_line(r),
    "get_size": lambda d, _: d.get_size(4),
    "get_text_range": lambda d, r: d.get_text_range((0, 0), (max(r, 0), 3)),
    "has_char_at": lambda d, r: d.has_char_at(r, 2),
    "has_syntax": lambda d, _: d.has_syntax,
    "is_growing": lambda d, _: d.is_growing(),
    "is_long": lambda d, r: d.is_long(r),
    "length": lambda d, _: d.length,
    "line_count": lambda d, _: d.line_count,
    "line_length": lambda d, r: d.line_length(r),
    "lines": lambda d, _: d.lines,
    "long_index": lambda d, r: d.long_index(r),
    "newline": lambda d, _: d.newline,
    "note_x": lambda d, _: d.note_x(3),
    "prepare_query": lambda d, _: d.prepare_query("(x)"),
    "query_syntax_tree": lambda d, _: d.query_syntax_tree(_no_query()),
    "read_all": lambda d, _: d.read_all(1 << 20),
    "read_bytes": lambda d, _: d.read_bytes(0, 40),
    "replace_range": _gated(lambda d, r: d.replace_range((max(r, 0), 1), (max(r, 0), 2), "x\ny")),
    "row_at_offset": lambda d, _: d.row_at_offset(5),
    "row_byte_length": lambda d, r: d.row_byte_length(r),
    "row_class": lambda d, r: d.row_class(r),
    "row_display_width": lambda d, r: d.row_display_width(r),
    "selection_content": lambda d, r: d.selection_content((0, 0), (max(r, 0), 2)),
    "snapshot": lambda d, _: d.snapshot(),
    "splice": _gated(lambda d, _: d.splice((0, 1), (0, 1), _content(d))),
    "splice_bytes": _gated(lambda d, _: d.splice_bytes(1, 2, d.selection_content((0, 0), (0, 1)))),
    "start": lambda d, _: d.start,
    "start_scan": lambda d, _: d.start_scan(),
    "subscribe": lambda d, _: d.subscribe(lambda: None),
    "tab_width": lambda d, _: d.tab_width,
    "text": lambda d, _: d.text,
    "wait_closed": lambda d, _: d.wait_closed(0.0),
    "wait_indexed": lambda d, _: d.wait_indexed(5.0),
    "__getitem__": lambda d, r: (d[r], d[0:2]),
    "begin_save": _gated(lambda d, _: (d.begin_save(), d.end_save())),
    "end_save": _gated(lambda d, _: d.end_save()),
    "lock_edits": _locked_edit,
    "unlock_edits": _gated(lambda d, _: d.unlock_edits()),
    "prepare_rebase": lambda d, r: _save_and_rebase(d, r, apply=False),
    "apply_rebase": lambda d, r: _save_and_rebase(d, r, apply=True),
    "wait_rebased": lambda d, _: d.wait_rebased(0.0),
    "join_on_close": lambda d, _: d.join_on_close(_finished_thread()),
    "plan": lambda d, r: (d.plan(0, d.length, unverified=False), d.plan(max(r, 0), 7, unverified=True)),
    "require_not_saving": _gated(lambda d, _: d.require_not_saving("audit")),
    "saving": lambda d, _: d.saving,
}
ATTRIBUTES = frozenset({"attach_syntax", "call_log", "foreground", "from_bytes", "from_path", "from_text"})
"""Public members that are not driven: constructors, plain attributes and the syntax mirror hook (it touches no table)."""


def _public_members(doc: LazyDocument) -> set[str]:
    return {name for name in dir(doc) if not name.startswith("_")} | {"__getitem__"}


def _build(kind: str) -> tuple[LazyDocument, TrackedLock, GuardedTable]:
    config = LONG_ROWS if kind.startswith("long") else LazyConfig(stride=4)
    doc = LazyDocument(BytesSource(TEXT.encode()), config)
    lock, table = install_guards(doc)
    if kind in {"edited", "long-edited"}:
        doc.replace_range((0, 1), (0, 1), "q\nr")
        doc.replace_range((0, 0), (1, 1), "")
        doc.splice_bytes(1, 2, doc.selection_content((0, 0), (0, 1)))
        doc.replace_range((2, 1), (2, 1), "w" * 30)
    if kind == "closed":
        doc.close()
        assert doc.wait_closed(JOIN_SECONDS)
    return doc, lock, table


def _drive(doc: LazyDocument, errors: list[BaseException]) -> None:
    """Call every table entry on every row, ignoring the refusals a document is allowed to make; `close` goes last."""
    for round_number in range(ROUNDS):
        for name in sorted(CALLS, key=lambda key: key == "close"):
            if name == "close" and round_number < ROUNDS - 1:
                continue
            for row in ROWS:
                try:
                    CALLS[name](doc, row)
                except EXPECTED:
                    continue
                except UNEXPECTED as error:
                    errors.append(error)


KINDS = ["plain", "edited", "long", "long-edited", "closed"]


def test_the_table_covers_every_public_member() -> None:
    doc = LazyDocument.from_text(TEXT)
    try:
        assert _public_members(doc) - ATTRIBUTES == set(CALLS)
    finally:
        doc.close()
        assert doc.wait_closed(JOIN_SECONDS)


@pytest.mark.parametrize("kind", KINDS)
def test_every_table_access_is_under_the_lock(kind: str) -> None:
    doc, _lock, table = _build(kind)
    errors: list[BaseException] = []
    _drive(doc, errors)
    doc.close()
    assert doc.wait_closed(JOIN_SECONDS)
    assert not errors, errors
    assert not table.violations, sorted(set(table.violations))


@pytest.mark.parametrize("kind", KINDS)
def test_every_table_access_is_under_the_lock_from_two_threads(kind: str) -> None:
    doc, _lock, table = _build(kind)
    errors: list[BaseException] = []
    barrier = threading.Barrier(2, timeout=JOIN_SECONDS)

    def worker() -> None:
        barrier.wait()
        _drive(doc, errors)

    thread = threading.Thread(target=worker, name="audit-worker", daemon=True)
    thread.start()
    barrier.wait()
    _drive(doc, errors)
    thread.join(JOIN_SECONDS)
    assert not thread.is_alive()
    doc.close()
    assert doc.wait_closed(JOIN_SECONDS)
    assert not errors, errors
    assert not table.violations, sorted(set(table.violations))


def test_the_guard_detects_an_unguarded_access() -> None:
    """The audit must be able to fail: an access outside the lock is recorded and raised, one inside is not."""
    doc, lock, table = _build("plain")
    try:
        with pytest.raises(AssertionError, match="without the document lock"):
            table.read(0, 1)
        with pytest.raises(AssertionError, match="without the document lock"):
            _ = table.length
        assert len(table.violations) == 2
        with lock:
            assert table.read(0, 1) == b"a"
            assert table.length == len(TEXT.encode())
    finally:
        doc.close()
        assert doc.wait_closed(JOIN_SECONDS)
