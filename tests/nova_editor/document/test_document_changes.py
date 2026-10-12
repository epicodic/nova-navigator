"""`LazyDocument.history`, change listeners and `change_kind` (ACT2 slice 3)."""

from __future__ import annotations

import logging
import subprocess
import sys
import threading
from collections.abc import Callable
from pathlib import Path

import pytest

from nova_editor.core import Content
from nova_editor.document import DocumentChange
from nova_editor.document._document import EditResult
from nova_editor.document._errors import EditsLocked, RowUnavailable
from nova_editor.document._history import EditHistory
from nova_editor.document._lazy_document import LazyDocument
from tests.nova_editor.document.helpers_save import JOIN_SECONDS, Session, open_pread_document
from tests.nova_editor.document.test_byte_edit import hex_edit
from tests.nova_editor.document.test_lazy_byte_edit import DATA, Stepped
from tests.nova_editor.document.test_lazy_edit import SMALL

ROOT = Path(__file__).parents[3]


def make_doc(data: bytes = b"hello world\nsecond row\n") -> LazyDocument:
    return LazyDocument.from_bytes(data, SMALL)


def watch(doc: LazyDocument) -> list[DocumentChange]:
    changes: list[DocumentChange] = []
    doc.add_change_listener(changes.append)
    return changes


def test_document_owns_one_history() -> None:
    doc = make_doc()
    assert isinstance(doc.history, EditHistory)
    assert doc.history is doc.history
    assert doc.history.undo_stack == []
    assert doc.history.redo_stack == []
    assert not doc.history.modified


def test_importing_the_history_module_first_works() -> None:
    code = "import nova_editor.document._history\nimport nova_editor.document._lazy_document\nprint('ok')"
    result = subprocess.run([sys.executable, "-c", code], capture_output=True, text=True, check=False, cwd=ROOT)
    assert result.returncode == 0, result.stderr
    assert result.stdout.strip() == "ok"


def _replace_range(doc: LazyDocument) -> None:
    doc.replace_range((0, 1), (0, 3), "XYZ!")


def _splice(doc: LazyDocument) -> None:
    doc.splice((0, 1), (0, 3), doc._table.add(b"XYZ!"))


def _splice_bytes_located(doc: LazyDocument) -> None:
    doc.splice_bytes(1, 3, doc._table.add(b"XYZ!"))


@pytest.mark.parametrize("edit", [_replace_range, _splice, _splice_bytes_located])
def test_one_notice_per_located_commit_path(edit: Callable[[LazyDocument], None]) -> None:
    doc = make_doc()
    changes = watch(doc)
    edit(doc)
    assert changes == [DocumentChange("edit", 1, 2, 4, doc.revision)]


def test_one_notice_for_a_blind_splice() -> None:
    stepped = Stepped(DATA)
    try:
        changes = watch(stepped.doc)
        far = len(DATA) - 3
        assert stepped.doc.snapshot().scanned_bytes < far
        stepped.insert(far, b"zz")
        assert changes == [DocumentChange("edit", far, 0, 2, stepped.doc.revision)]
    finally:
        stepped.source.stop()
        stepped.doc.close()


def test_undo_redo_kind() -> None:
    doc = make_doc()
    entry = hex_edit(doc, 0, 5, b"HOWDY!")
    doc.history.record(entry)
    changes = watch(doc)
    assert doc.history.undo(doc) is not None
    assert doc.history.redo(doc) is not None
    doc.splice_bytes(0, 0, doc._table.add(b"q"))
    assert [change.kind for change in changes] == ["undo", "redo", "edit"]
    assert changes[0].start_byte == 0
    assert changes[0].removed_length == 6
    assert changes[0].inserted_length == 5


def test_kind_is_restored_after_an_exception() -> None:
    doc = make_doc()
    changes = watch(doc)
    with pytest.raises(RuntimeError), doc.change_kind("undo"):
        raise RuntimeError
    doc.splice_bytes(0, 0, doc._table.add(b"q"))
    assert [change.kind for change in changes] == ["edit"]


def test_rolled_back_step_announces_with_the_step_kind(monkeypatch: pytest.MonkeyPatch) -> None:
    doc = make_doc()
    history = doc.history
    history.record(hex_edit(doc, 0, 0, b"A"))
    history.record(hex_edit(doc, 5, 5, b"B"))
    changes = watch(doc)
    real = doc.splice_bytes
    calls = 0

    def flaky(start: int, end: int, content: Content) -> EditResult:
        nonlocal calls
        calls += 1
        if calls == 2:
            raise EditsLocked("save")
        return real(start, end, content)

    monkeypatch.setattr(doc, "splice_bytes", flaky)
    with pytest.raises(RowUnavailable):
        history.undo(doc)
    assert [change.kind for change in changes] == ["undo", "undo"]


@pytest.mark.parametrize("closed", [False, True])
def test_refused_edit_sends_no_notice(closed: bool) -> None:
    doc = make_doc()
    changes = watch(doc)
    if closed:
        doc.close()
    else:
        doc.lock_edits("save")
    with pytest.raises(RowUnavailable):
        doc.splice_bytes(0, 0, doc._table.add(b"q"))
    with pytest.raises(RowUnavailable):
        doc.replace_range((0, 0), (0, 0), "q")
    assert changes == []


def test_remover_and_raising_listener(caplog: pytest.LogCaptureFixture) -> None:
    doc = make_doc()
    seen: list[DocumentChange] = []

    def boom(change: DocumentChange) -> None:
        del change
        raise RuntimeError("listener failure")

    remove_seen = doc.add_change_listener(seen.append)
    doc.add_change_listener(boom)
    later: list[DocumentChange] = []
    doc.add_change_listener(later.append)
    with caplog.at_level(logging.ERROR):
        doc.splice_bytes(0, 0, doc._table.add(b"q"))
    assert len(seen) == 1
    assert len(later) == 1
    assert "listener failure" in caplog.text
    assert doc.read_bytes(0, 1) == b"q"
    remove_seen()
    remove_seen()
    doc.splice_bytes(0, 0, doc._table.add(b"r"))
    assert len(seen) == 1
    assert len(later) == 2


def test_apply_rebase_announces_save(tmp_path: Path) -> None:
    doc, original = open_pread_document(tmp_path, b"alpha\nbeta\ngamma\n")
    session = Session(doc, original, tmp_path)
    try:
        session.edit((0, 1), (1, 2), "XY\nZ")
        changes = watch(doc)
        revision = doc.revision
        session.save(tmp_path / "out.bin")
        assert changes == [DocumentChange("save", 0, 0, 0, revision)]
    finally:
        doc.close()
        assert doc.wait_closed(JOIN_SECONDS)


def test_listener_runs_after_the_lock_is_released() -> None:
    doc = make_doc()
    outcome: list[bytes] = []

    def listener(change: DocumentChange) -> None:
        del change

        def read() -> None:
            outcome.append(doc.read_bytes(0, 3))

        worker = threading.Thread(target=read)
        worker.start()
        worker.join(1.0)
        assert not worker.is_alive()

    doc.add_change_listener(listener)
    doc.splice_bytes(0, 0, doc._table.add(b"q"))
    assert outcome == [b"qhe"]
