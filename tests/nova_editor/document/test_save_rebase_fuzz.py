"""Save, rebase, undo and redo fuzz against the `bytes` reference (ACT5 Task 11, REQ-10 across repeated saves).

Seeded cases plus a hypothesis state machine.
Half of the seeded cases lower `UNDO_COPY_LIMIT` to 4 so that retained generations occur.
"""

from __future__ import annotations

import shutil
import tempfile
from pathlib import Path
from unittest.mock import _patch, patch

import pytest
from hypothesis import settings
from hypothesis import strategies as st
from hypothesis.stateful import RuleBasedStateMachine, initialize, rule

from nova_editor.document import _lazy_document
from nova_editor.document._lazy_document import LazyDocument
from tests.nova_editor.core.reference import ALPHABET, Rng
from tests.nova_editor.document.helpers_save import JOIN_SECONDS, SMALL, Session, open_pread_document
from tests.nova_editor.document.test_lazy_edit import settle

SEEDS = range(24)
STEPS = 28
LOW_LIMIT = 4
MACHINE_EXAMPLES = 25
MACHINE_STEPS = 20
TEXT_ALPHABET = [item.decode("utf-8", "surrogateescape") for item in ALPHABET] + ["\n", "\r\n"]
ACTIONS = ["insert", "delete", "replace", "undo", "redo", "save", "save", "copy", "paste"]


def _original(rng: Rng, long_row: bool) -> bytes:
    text = "".join(rng.choice(TEXT_ALPHABET) for _ in range(rng.randint(0, 60)))
    if long_row:
        text += "\n" + "".join(rng.choice(TEXT_ALPHABET[:6]) for _ in range(300)) + "\n" + text[:10]
    return text.encode("utf-8", "surrogateescape")


def _location(session: Session, rng: Rng) -> tuple[int, int]:
    row = rng.randint(0, session.ref.line_count - 1)
    return (row, rng.randint(0, len(session.ref.rows[row])))


def _text(rng: Rng, limit: int = 6) -> str:
    return "".join(rng.choice(TEXT_ALPHABET) for _ in range(rng.randint(0, limit)))


def _step(session: Session, rng: Rng, action: str, target: Path) -> None:
    settle(session.doc)  # long rows are scanned in the background; edits need their columns
    if action in {"insert", "replace"}:
        start = _location(session, rng)
        end = start if action == "insert" else _location(session, rng)
        session.edit(start, end, _text(rng))
    elif action == "delete":
        session.edit(_location(session, rng), _location(session, rng), "")
    elif action == "undo":
        session.undo()
    elif action == "redo":
        session.redo()
    elif action == "copy":
        session.copy(_location(session, rng), _location(session, rng))
    elif action == "paste":
        session.paste(_location(session, rng))
    else:
        session.save(target)
        assert session.doc.wait_rebased(JOIN_SECONDS)
        session.check_saved(target)
    session.check_document()
    if action == "save":
        session.check_records()


def _finish_all(session: Session) -> None:
    """Undo everything back to the original, then redo everything back to the end (REQ-10 across repeated saves)."""
    end = session.redo_stack[0].after if session.redo_stack else session.undo_stack[-1].after if session.undo_stack else session.original
    if session.cleared == 0:
        while session.undo():
            session.check_document()
        assert session.doc.read_bytes(0, session.doc.length) == session.original
        while session.redo():
            session.check_document()
        assert session.doc.read_bytes(0, session.doc.length) == end


@pytest.mark.parametrize("seed", SEEDS)
def test_save_rebase_fuzz(seed: int, tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    rng = Rng(seed)
    if seed % 2:
        monkeypatch.setattr(_lazy_document, "UNDO_COPY_LIMIT", LOW_LIMIT)
    doc, original = open_pread_document(tmp_path, _original(rng, seed % 3 == 0), SMALL)
    session = Session(doc, original, tmp_path)
    targets = [tmp_path / "doc.bin", tmp_path / "out.bin"]
    try:
        for _ in range(STEPS):
            _step(session, rng, rng.choice(ACTIONS), targets[rng.randint(0, 1) if seed % 4 == 0 else 0])
        session.save(targets[0])
        assert session.doc.wait_rebased(JOIN_SECONDS)
        session.check_saved(targets[0])
        session.check_records()
        _finish_all(session)
    finally:
        doc.close()
        assert doc.wait_closed(JOIN_SECONDS)


def test_the_fuzz_reaches_retained_generations_and_a_second_path(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    """A guard against a vacuous fuzz: a fixed script keeps a generation and saves to a second path."""
    monkeypatch.setattr(_lazy_document, "UNDO_COPY_LIMIT", LOW_LIMIT)
    doc, original = open_pread_document(tmp_path, b"one\ntwo\r\nthree\nfour\n", SMALL)
    session = Session(doc, original, tmp_path)
    try:
        session.edit((0, 0), (2, 0), "")
        session.edit((0, 0), (0, 0), "typed")
        session.save(tmp_path / "second.bin")
        assert len(doc._table.legacy) == 1
        assert doc.wait_rebased(JOIN_SECONDS)
        session.check_saved(tmp_path / "second.bin")
        _finish_all(session)
    finally:
        doc.close()
        assert doc.wait_closed(JOIN_SECONDS)


class SaveMachine(RuleBasedStateMachine):
    """Hypothesis state machine over the same session."""

    def __init__(self) -> None:
        super().__init__()
        self.directory = Path(tempfile.mkdtemp(prefix="rebase-fuzz-"))
        self.doc: LazyDocument | None = None
        self.session: Session | None = None
        self.rng = Rng(0)
        self.patch: _patch[int] | None = None

    @initialize(seed=st.integers(0, 1_000_000), small_limit=st.booleans())
    def start(self, seed: int, small_limit: bool) -> None:
        self.rng = Rng(seed)
        self.patch = patch.object(_lazy_document, "UNDO_COPY_LIMIT", LOW_LIMIT if small_limit else _lazy_document.UNDO_COPY_LIMIT)
        self.patch.start()
        self.doc, original = open_pread_document(self.directory, _original(self.rng, seed % 3 == 0), SMALL)
        self.session = Session(self.doc, original, self.directory)

    @rule(action=st.sampled_from(ACTIONS), salt=st.integers(0, 1_000_000))
    def act(self, action: str, salt: int) -> None:
        assert self.session is not None
        rng = Rng(salt)
        _step(self.session, rng, action, self.directory / "doc.bin")

    def teardown(self) -> None:
        if self.patch is not None:
            self.patch.stop()
        if self.session is not None and self.doc is not None:
            _finish_all(self.session)
            self.doc.close()
            assert self.doc.wait_closed(JOIN_SECONDS)
        shutil.rmtree(self.directory, ignore_errors=True)


TestSaveMachine = SaveMachine.TestCase
TestSaveMachine.settings = settings(max_examples=MACHINE_EXAMPLES, stateful_step_count=MACHINE_STEPS, deadline=None, derandomize=True)
