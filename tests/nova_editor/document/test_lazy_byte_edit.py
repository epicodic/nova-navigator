"""`LazyDocument.splice_bytes` at offsets the line scan has not reached (ACT1 Task 4)."""

from __future__ import annotations

import re
from collections.abc import Iterator

import pytest
from textual._tree_sitter import get_language

from nova_editor.core import Content
from nova_editor.document._document import EditResult, Location
from nova_editor.document._lazy_config import LazyConfig
from nova_editor.document._lazy_document import LazyDocument, RowUnavailable
from nova_editor.document._syntax_aware_document import TREE_SITTER, SyntaxAwareDocument
from tests.nova_editor.core.reference import StepSource

BLOCK = 16
ROWS = 40
DATA = b"".join(b"row %02d\n" % i for i in range(ROWS))
CRLF_DATA = b"".join(b"row %02d\r\n" % i for i in range(ROWS))
TERMINATOR = re.compile(rb"\r\n|\n|\r")


class Stepped:
    """A document over a source whose scan advances one block per `step()`, plus the bytes the document must equal."""

    def __init__(self, data: bytes) -> None:
        self.ref = data
        self.source = StepSource(data, BLOCK)
        self.doc = LazyDocument(self.source, LazyConfig(stride=2, scan_block=BLOCK), autostart=False)
        self.source.attach(self.doc._line_index)

    def step(self, count: int = 1) -> None:
        for _ in range(count):
            self.source.step()

    def finish(self) -> None:
        while not self.source.finished:
            self.source.step()
        for _ in range(10):
            self.doc.snapshot()

    def insert(self, offset: int, data: bytes) -> EditResult:
        """Insert `data` at `offset` through `splice_bytes` and mirror it in the reference."""
        result = self.doc.splice_bytes(offset, offset, self.doc._table.add(data))
        self.ref = self.ref[:offset] + data + self.ref[offset:]
        return result

    def delete(self, begin: int, end: int) -> EditResult:
        result = self.doc.splice_bytes(begin, end, Content.from_pieces((), 0))
        self.ref = self.ref[:begin] + self.ref[end:]
        return result

    def rows(self) -> list[str]:
        return [row.decode("utf-8", "surrogateescape") for row in TERMINATOR.split(self.ref)]

    def content(self) -> bytes:
        return self.doc.read_bytes(0, self.doc.length)


@pytest.fixture
def stepped() -> Iterator[type[Stepped]]:
    made: list[Stepped] = []

    class Tracked(Stepped):
        def __init__(self, data: bytes) -> None:
            super().__init__(data)
            made.append(self)

    yield Tracked
    for item in made:
        item.source.stop()
        item.doc.close()


def test_splice_bytes_at_an_unscanned_offset_succeeds(stepped: type[Stepped]) -> None:
    case = stepped(DATA)
    case.step(5)
    assert case.doc.snapshot().scanned_bytes < len(DATA) - 2
    case.insert(len(DATA) - 2, b"ZZ")
    assert case.doc.length == len(case.ref)
    assert case.content() == case.ref


def test_rows_before_the_edit_are_unchanged_and_rows_after_are_unavailable(stepped: type[Stepped]) -> None:
    case = stepped(DATA)
    case.step(5)
    case.insert(len(DATA) - 2, b"ZZ")
    reference = case.rows()
    for row in range(4):
        assert case.doc.get_line(row) == reference[row]
    assert case.doc.line_count <= len(reference)
    assert case.doc.row_at_offset(case.doc.length - 1) is None
    with pytest.raises(RowUnavailable):
        case.doc.get_line(case.doc.line_count - 1)


def test_after_the_scan_completes_every_row_equals_the_reference(stepped: type[Stepped]) -> None:
    case = stepped(DATA)
    case.step(5)
    case.insert(len(DATA) - 2, b"ZZ")
    case.insert(10, b"ab\ncd")
    case.delete(100, 130)
    case.finish()
    reference = case.rows()
    assert case.doc.line_count == len(reference)
    assert [case.doc.get_line(row) for row in range(len(reference))] == reference
    assert case.content() == case.ref
    assert case.doc.snapshot().complete


def test_undo_of_a_blind_edit_restores_the_bytes_and_the_identity(stepped: type[Stepped]) -> None:
    case = stepped(DATA)
    case.step(3)
    edit = case.insert(len(DATA) - 2, b"ZZ")
    assert edit.start_byte is not None
    assert edit.inserted is not None
    assert edit.removed is not None
    assert not case.doc._table.is_identity
    case.doc.splice_bytes(edit.start_byte, edit.start_byte + edit.inserted.length, edit.removed)
    case.ref = DATA
    assert case.content() == DATA
    case.finish()
    assert case.doc._table.is_identity


def test_blind_edit_evicts_row_caches(stepped: type[Stepped]) -> None:
    case = stepped(DATA)
    case.step(5)
    frontier = case.doc.snapshot()
    resolved = frontier.count - 1
    assert resolved >= 4
    reference = case.rows()
    for row in range(resolved):
        assert case.doc.get_line(row) == reference[row]
    assert case.doc._texts
    # Starts inside the last resolved row and ends at the end: the end offset cannot be resolved.
    last = case.doc.row_at_offset(frontier.scanned_bytes - 1)
    assert last is not None
    begin = last[1].start + 2
    case.delete(begin, len(DATA))
    # The cached text of the edited row is gone: the row is the open row now and not resolved.
    with pytest.raises(RowUnavailable):
        case.doc.get_line(last[0])
    assert case.doc.get_line(last[0] - 1) == case.rows()[last[0] - 1]
    case.insert(begin, b"Q\n")
    assert case.doc.get_line(last[0]) == "roQ"
    assert case.doc.get_line(last[0] - 1) == case.rows()[last[0] - 1]


def test_text_edit_at_an_unscanned_position_is_still_refused(stepped: type[Stepped]) -> None:
    case = stepped(DATA)
    case.step(5)
    location: Location = (case.doc.line_count + 5, 0)
    content = case.doc._table.add(b"x")
    with pytest.raises(RowUnavailable):
        case.doc.splice(location, location, content)
    assert case.content() == DATA


def test_blind_edit_inside_a_crlf_takes_the_halves_along(stepped: type[Stepped]) -> None:
    case = stepped(CRLF_DATA)
    case.step(4)
    lf = len(CRLF_DATA) - 1  # between the CR and the LF of the last row
    assert CRLF_DATA[lf - 1 : lf + 1] == b"\r\n"
    case.doc.splice_bytes(lf, lf, case.doc._table.add(b"X"))
    case.ref = CRLF_DATA[:lf] + b"X" + CRLF_DATA[lf:]
    assert case.content() == case.ref
    case.finish()
    assert case.content() == case.ref
    reference = case.rows()
    assert [case.doc.get_line(row) for row in range(case.doc.line_count)] == reference


def test_stale_unknown_content_inserted_at_a_resolved_offset_takes_the_blind_path(stepped: type[Stepped]) -> None:
    case = stepped(DATA)
    case.step(3)
    begin, end = len(DATA) - 60, len(DATA) - 20
    removed = case.delete(begin, end).removed
    assert removed is not None
    assert removed.unknown > 0
    case.finish()
    assert case.doc.row_at_offset(begin) is not None
    case.doc.splice_bytes(begin, begin, removed)
    case.ref = DATA
    assert case.content() == DATA
    case.finish()
    reference = case.rows()
    assert case.doc.line_count == len(reference)
    assert [case.doc.get_line(row) for row in range(len(reference))] == reference


@pytest.mark.skipif(not TREE_SITTER, reason="tree-sitter is not installed")
def test_blind_edit_detaches_the_syntax_mirror(stepped: type[Stepped]) -> None:
    class Recording(SyntaxAwareDocument):
        calls = 0

        def replace_range(self, start: Location, end: Location, text: str) -> EditResult:
            self.calls += 1
            return super().replace_range(start, end, text)

    data = b"".join(b"x%02d = %d\n" % (i, i) for i in range(ROWS))
    case = stepped(data)
    case.step(6)
    language = get_language("python")
    assert language is not None
    mirror = Recording(data.decode(), language)
    case.doc.attach_syntax(mirror)
    assert case.doc.has_syntax
    query = case.doc.prepare_query("(identifier) @name")
    assert query is not None
    resolved = case.doc.row_at_offset(5)
    assert resolved is not None
    case.insert(resolved[1].start + 1, b"y")
    assert case.doc.has_syntax
    assert mirror.calls == 1
    case.insert(len(case.ref) - 2, b"zz")
    assert not case.doc.has_syntax
    assert mirror.calls == 1
    assert case.doc.prepare_query("(identifier) @name") is None
    assert case.doc.query_syntax_tree(query) == {}
    assert case.content() == case.ref
