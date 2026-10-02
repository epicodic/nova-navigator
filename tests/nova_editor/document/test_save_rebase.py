"""`LazyDocument.prepare_rebase` and `apply_rebase` (ACT5 Task 11, design 7.3 and 8)."""

from __future__ import annotations

import dataclasses
import threading
from collections.abc import Callable
from pathlib import Path

import pytest

from nova_editor.core import ByteSource, Content, LineIndex, LongLineIndex, PreadSource
from nova_editor.core.pieces import generation_of
from nova_editor.core.rebase import RebasePlan
from nova_editor.core.save import SaveJob
from nova_editor.document import _lazy_document
from nova_editor.document._edit import Edit
from nova_editor.document._lazy_document import LazyDocument
from nova_editor.widget import NovaTextArea
from tests.nova_editor.document.helpers_save import JOIN_SECONDS, SAVE_SETTINGS, SMALL, DocPlanner, Session, open_pread_document, run_save, save_result
from tests.nova_editor.save_widget_helpers import open_files

ORIGINAL = b"alpha\nbeta\r\ngamma\rdelta\n" + b"0123456789" * 8 + b"\nend"


def _session(tmp_path: Path, data: bytes = ORIGINAL) -> Session:
    doc, original = open_pread_document(tmp_path, data)
    return Session(doc, original, tmp_path)


def _finish(session: Session) -> None:
    session.doc.close()
    assert session.doc.wait_closed(JOIN_SECONDS)


def test_a_save_rebases_the_document_onto_the_file(tmp_path: Path) -> None:
    session = _session(tmp_path)
    try:
        old_source = session.doc._source
        session.edit((0, 1), (1, 2), "XY\nZ")
        session.edit((2, 0), (2, 0), "new ")
        target = tmp_path / "doc.bin"
        plan = session.save(target)
        assert plan.retained is None
        session.check_saved(target)
        session.check_records()
        assert session.doc._source is plan.source
        assert session.doc._source is not old_source
        assert session.doc._table.is_identity
        assert session.doc.wait_rebased(JOIN_SECONDS)
        assert isinstance(old_source, PreadSource)
        with pytest.raises(ValueError, match="closed"):
            old_source.read(0, 1)
    finally:
        _finish(session)


def test_undo_after_a_save_restores_the_pre_save_content(tmp_path: Path) -> None:
    session = _session(tmp_path)
    try:
        session.edit((1, 0), (1, 4), "")
        session.edit((0, 0), (0, 0), "typed\nlines")
        session.save(tmp_path / "doc.bin")
        assert session.doc.wait_rebased(JOIN_SECONDS)
        session.check_records()
        assert session.undo()
        session.check_document()
        assert session.undo()
        assert session.doc.read_bytes(0, session.doc.length) == ORIGINAL
        assert session.redo()
        assert session.redo()
        session.check_document()
        session.edit((0, 0), (0, 1), "q")  # editing after the rebase works on the new add store
        session.check_document()
    finally:
        _finish(session)


def test_the_text_that_is_in_the_file_is_not_copied_into_the_store(tmp_path: Path) -> None:
    session = _session(tmp_path)
    try:
        session.edit((4, 0), (4, 20), "")  # a 20 byte deletion, then typing: both end up in the file or are orphans
        session.edit((0, 0), (0, 0), "typed")
        plan = session.save(tmp_path / "doc.bin")
        store = plan.new_add_store
        held = sum(store.length(k) for k in range(1, store.segment_count + 1))
        assert held == plan.orphan_bytes == 20
    finally:
        _finish(session)


def test_save_as_changes_the_source_to_the_new_file(tmp_path: Path) -> None:
    session = _session(tmp_path)
    try:
        session.edit((0, 0), (0, 5), "ALPHA")
        other = tmp_path / "other.bin"
        plan = session.save(other)
        session.check_saved(other)
        assert (tmp_path / "doc.bin").read_bytes() == ORIGINAL
        assert isinstance(session.doc._source, PreadSource)
        assert session.doc._source is plan.source
        session.undo()
        session.check_document()
        session.edit((1, 0), (1, 1), "B")
        assert session.save(other) is not None  # saving again to the new path
        session.check_saved(other)
        assert (tmp_path / "doc.bin").read_bytes() == ORIGINAL
    finally:
        _finish(session)


def test_orphans_over_the_limit_become_a_legacy_generation(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(_lazy_document, "UNDO_COPY_LIMIT", 4)
    session = _session(tmp_path)
    try:
        old_source = session.doc._source
        session.edit((3, 0), (4, 0), "")  # delete a long row
        plan = session.save(tmp_path / "doc.bin")
        assert plan.retained is not None
        assert len(session.doc._table.legacy) == 1
        generations = {generation_of(piece.src) for entry in session.undo_stack for content in entry.edit.contents() for piece in content.pieces}
        assert generations == {1}
        session.check_records()
        assert session.doc.wait_rebased(JOIN_SECONDS)
        assert isinstance(old_source, PreadSource)
        assert old_source.read(0, 1) == b"a"  # the old file stays open for the retained generation
        assert session.undo()
        session.check_document()
        assert session.doc.read_bytes(0, session.doc.length) == ORIGINAL
    finally:
        _finish(session)


def test_at_the_last_generation_the_history_is_cleared_and_the_old_files_are_released(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(_lazy_document, "UNDO_COPY_LIMIT", 0)
    monkeypatch.setattr(_lazy_document, "MAX_GENERATION", 1)
    session = _session(tmp_path)
    try:
        first = session.doc._source
        session.edit((3, 0), (4, 0), "")
        session.save(tmp_path / "doc.bin")
        assert session.cleared == 0
        second = session.doc._source
        session.edit((0, 0), (1, 0), "")
        session.save(tmp_path / "doc.bin")
        assert session.cleared == 1
        assert not session.undo_stack
        assert not session.doc._legacy_files
        assert not session.doc._table.legacy
        session.check_saved(tmp_path / "doc.bin")
        assert session.doc.wait_rebased(JOIN_SECONDS)
        for released in (first, second):
            assert isinstance(released, PreadSource)
            with pytest.raises(ValueError, match="closed"):
                released.read(0, 1)
    finally:
        _finish(session)


def test_apply_rebase_on_a_closed_document_closes_the_new_source(tmp_path: Path) -> None:
    session = _session(tmp_path)
    session.edit((0, 0), (0, 0), "x")
    target = tmp_path / "doc.bin"
    result = save_result(session.doc, target)
    plan = session.doc.prepare_rebase(result, [])
    _finish(session)
    session.doc.apply_rebase(plan)
    assert isinstance(result.source, PreadSource)
    with pytest.raises(ValueError, match="closed"):
        result.source.read(0, 1)
    assert target.read_bytes() == session.ref.data()


def test_a_changed_document_refuses_the_rebase_and_stays_as_it_is(tmp_path: Path) -> None:
    session = _session(tmp_path)
    try:
        target = tmp_path / "doc.bin"
        doc = session.doc
        doc.begin_save()
        result = SaveJob(DocPlanner(doc), target, SAVE_SETTINGS, progress=None, foreground=None).run()
        plan = doc.prepare_rebase(result, [])
        session.edit((0, 0), (0, 1), "Z")  # an edit of the same length slipped in: only the edit counter sees it
        before = doc._source
        with pytest.raises(ValueError, match="changed"):
            doc.apply_rebase(plan)
        assert doc._source is before
        session.check_document()
        assert isinstance(result.source, PreadSource)
        result.source.close()
        doc.end_save()
    finally:
        _finish(session)


class GatedScanSource:
    """Memory source whose reads from the line scan thread block until `gate` is set; the closing is recorded."""

    def __init__(self, data: bytes) -> None:
        self._data = data
        self.gate = threading.Event()
        self.entered = threading.Event()
        self.closed = threading.Event()

    def length(self) -> int:
        return len(self._data)

    def read(self, offset: int, size: int, *, cache: bool = True) -> bytes:
        del cache
        if threading.current_thread().name == "line-index-scan":
            self.entered.set()
            assert self.gate.wait(JOIN_SECONDS)
        return self._data[offset : offset + size]

    def close(self) -> None:
        self.closed.set()


def test_the_old_source_is_closed_only_after_the_blocked_scan_read_returns(tmp_path: Path) -> None:
    data = b"".join(f"row {n}\n".encode() for n in range(200))
    source = GatedScanSource(data)
    config = dataclasses.replace(SMALL, sync_scan_limit=16, scan_block=64)
    doc = LazyDocument(source, config)
    try:
        assert source.entered.wait(JOIN_SECONDS)  # the scan thread is inside a read
        target = tmp_path / "out.bin"
        _, plan = run_save(doc, target, [])
        assert plan.source is doc._source
        assert doc.snapshot().complete
        assert not source.closed.wait(0.2)  # the in-flight read keeps the old source open
        assert target.read_bytes() == data
        source.gate.set()
        assert doc.wait_rebased(JOIN_SECONDS)
        assert source.closed.is_set()
    finally:
        source.gate.set()
        doc.close()
        assert doc.wait_closed(JOIN_SECONDS)


def test_load_text_reload_and_open_are_refused_while_a_save_runs(tmp_path: Path) -> None:
    session = _session(tmp_path)
    try:
        session.doc.begin_save()
        for operation in ("load_text", "reload", "open"):
            with pytest.raises(RuntimeError, match=operation):
                session.doc.require_not_saving(operation)
        session.doc.end_save()
        session.doc.require_not_saving("load_text")
        area = NovaTextArea.open(tmp_path / "doc.bin")
        try:
            assert isinstance(area.document, LazyDocument)
            area.document.begin_save()
            with pytest.raises(RuntimeError, match="load_text"):
                area.load_text("replacement")
            assert area.document.read_bytes(0, 5) == b"alpha"  # nothing was replaced
        finally:
            area.close()
    finally:
        _finish(session)


def test_a_cached_long_index_is_rebased_and_keeps_its_answers(tmp_path: Path) -> None:
    row = "é" * 300 + "\t" + "x" * 300
    data = ("top\n" + row + "\nbottom").encode()
    session = _session(tmp_path, data)
    try:
        doc = session.doc
        session.edit((0, 0), (0, 3), "TOPTOP")
        index = doc.long_index(1)
        assert index.join(JOIN_SECONDS)
        offset = doc.byte_offset(1, 400)
        length = doc.line_length(1)
        session.save(tmp_path / "doc.bin")
        rebased = doc._long.get(1)
        assert rebased is not None
        assert rebased is not index
        assert doc.byte_offset(1, 400) == offset
        assert doc.line_length(1) == length
        assert doc.column_slice(1, 298, 304) == row[298:304]
        session.check_saved(tmp_path / "doc.bin")
    finally:
        _finish(session)


def test_subscribers_follow_the_new_line_index(tmp_path: Path) -> None:
    session = _session(tmp_path)
    try:
        calls: list[int] = []
        session.doc.subscribe(lambda: calls.append(1))
        session.edit((0, 0), (0, 1), "A")
        calls.clear()
        session.save(tmp_path / "doc.bin")
        assert calls  # a subscriber of a completed index is told once
        assert session.doc._line_index._subscribers
    finally:
        _finish(session)


def test_edit_contents_and_rewrite_use_one_order() -> None:
    doc = LazyDocument.from_text("abc\ndef", SMALL)
    try:
        edit = Edit("", (0, 1), (0, 2), False, insert_content=doc.selection_content((0, 0), (0, 1)))
        result = doc.replace_range((0, 1), (0, 2), "Z")
        edit.start_byte, edit.removed, edit.inserted, edit._edit_result = result.start_byte, result.removed, result.inserted, result
        found = edit.contents()
        assert found == [result.removed, result.inserted, edit.insert_content, result.removed, result.inserted]
        replacements = [Content.from_pieces([], 0) for _ in found]
        edit.rewrite(iter(replacements))
        assert edit.removed is replacements[0]
        assert edit.inserted is replacements[1]
        assert edit.insert_content is replacements[2]
        assert edit._edit_result is not None
        assert edit._edit_result.removed is replacements[3]
        assert edit._edit_result.inserted is replacements[4]
        assert SAVE_SETTINGS.chunk == 7
    finally:
        doc.close()
        assert doc.wait_closed(JOIN_SECONDS)


TAIL_STEPS = ["line_subscribe", "replacement_subscribe", "replacement_start", "closer_start", "bookkeeping"]


def _tail_patch(step: str, monkeypatch: pytest.MonkeyPatch) -> tuple[type, str, Callable[..., object]]:
    """What to replace so that `step` of the tail of `apply_rebase` fails (the flag that arms the `bookkeeping` failure is set once `_install` returned)."""
    armed = threading.Event()
    real_install = LazyDocument._install
    real_start = threading.Thread.start
    real_alive = threading.Thread.is_alive

    def install(self: LazyDocument, plan: RebasePlan, source: ByteSource, line_index: LineIndex) -> object:
        swap = real_install(self, plan, source, line_index)
        armed.set()
        return swap

    def boom(*_args: object, **_kwargs: object) -> None:
        raise RuntimeError(step)

    def start(self: threading.Thread) -> None:
        if self.name == "lazy-rebase-closer":
            raise RuntimeError(step)
        real_start(self)

    def alive(self: threading.Thread) -> bool:
        if self.name == "holder" and armed.is_set():
            raise RuntimeError(step)
        return real_alive(self)

    monkeypatch.setattr(LazyDocument, "_install", install)
    patches: dict[str, tuple[type, str, Callable[..., object]]] = {
        "line_subscribe": (LineIndex, "subscribe", boom),
        "replacement_subscribe": (LongLineIndex, "subscribe", boom),
        "replacement_start": (LongLineIndex, "start", boom),
        "closer_start": (threading.Thread, "start", start),
        "bookkeeping": (threading.Thread, "is_alive", alive),
    }
    return patches[step]


@pytest.mark.parametrize("step", TAIL_STEPS)
def test_a_failure_after_the_install_keeps_the_swap_consistent(tmp_path: Path, monkeypatch: pytest.MonkeyPatch, step: str) -> None:
    row = "é" * 300 + "\t" + "x" * 300
    session = _session(tmp_path, ("top\n" + row + "\nbottom").encode())
    doc = session.doc
    release = threading.Event()
    holder = threading.Thread(target=release.wait, args=(JOIN_SECONDS,), name="holder")
    holder.start()
    closers: list[threading.Thread] = []
    real_thread_start = threading.Thread.start

    def recording_start(self: threading.Thread) -> None:
        if self.name == "lazy-rebase-closer":
            closers.append(self)
        real_thread_start(self)

    try:
        session.edit((0, 0), (0, 3), "TOPTOP")
        session.edit((2, 0), (2, 0), "new ")
        assert doc.long_index(1).join(JOIN_SECONDS)
        doc.subscribe(lambda: None)
        doc.join_on_close(holder)
        owner, name, replacement = _tail_patch(step, monkeypatch)
        monkeypatch.setattr(owner, name, replacement)
        if step != "closer_start":
            monkeypatch.setattr(threading.Thread, "start", recording_start)  # keeps the unregistered closer reachable for the final join
        old_source = doc._source
        target = tmp_path / "doc.bin"
        plan = session.save(target)  # the real apply_rebase: it must not raise
        monkeypatch.undo()
        assert doc.rebase_installed
        assert doc.rebase_problems
        assert doc._source is plan.source
        session.check_saved(target)
        session.check_records()
        assert isinstance(old_source, PreadSource)
        assert doc.long_index(1).join(JOIN_SECONDS)  # a long index that could not start is built again
        assert doc.line_length(1) == len(row)
        assert session.undo()
        session.check_document()
        assert session.undo()
        session.check_document()
        assert doc.read_bytes(0, doc.length) == ("top\n" + row + "\nbottom").encode()
    finally:
        release.set()
        holder.join(JOIN_SECONDS)
        _finish(session)
    assert doc.wait_rebased(JOIN_SECONDS)
    for closer in closers:
        closer.join(JOIN_SECONDS)
        assert not closer.is_alive()
    assert open_files(tmp_path) == []


def test_a_failure_before_the_install_leaves_the_flag_down(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    session = _session(tmp_path)
    try:
        session.edit((0, 0), (0, 0), "x")
        monkeypatch.setattr(LazyDocument, "_build_table", lambda *_args, **_kwargs: (_ for _ in ()).throw(OSError(5, "boom")))
        with pytest.raises(OSError, match="boom"):
            session.save(tmp_path / "doc.bin")
        monkeypatch.undo()
        assert not session.doc.rebase_installed
        assert session.doc.rebase_problems == []
    finally:
        _finish(session)
    assert open_files(tmp_path) == []


def test_a_plan_refused_after_a_successful_rebase_leaves_the_flag_down(tmp_path: Path) -> None:
    session = _session(tmp_path)
    try:
        session.edit((0, 0), (0, 0), "x")
        session.save(tmp_path / "doc.bin")
        assert session.doc.rebase_installed
        empty = RebasePlan(contents=[], new_add_store=session.doc._add_store, retained=None, clear_history=False, orphan_bytes=0)
        with pytest.raises(ValueError, match="no saved file"):
            session.doc.apply_rebase(empty)
        assert not session.doc.rebase_installed
        assert session.doc.rebase_problems == []
    finally:
        _finish(session)
