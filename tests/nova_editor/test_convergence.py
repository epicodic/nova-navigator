"""`text=`, `load_text` and small files converge onto the lazy document and the piece table (ACT4 design 8, DEC-17)."""

from __future__ import annotations

import re
from collections.abc import Callable
from pathlib import Path

import pytest
from textual.app import App, ComposeResult
from textual.pilot import Pilot

from nova_editor.core import BytesSource, PreadSource
from nova_editor.document._lazy_document import LazyDocument, WholeLineAccess
from nova_editor.document._syntax_aware_document import SyntaxAwareDocument
from nova_editor.widget import NovaTextArea
from nova_editor.widget import _text_area as text_area_module

WIDGET_DIR = Path(text_area_module.__file__).parent
PYTHON_TEXT = "def f(x):\n    return x + 1\n"


class _Host(App[None]):
    def __init__(self, widget: NovaTextArea) -> None:
        super().__init__()
        self._widget = widget
        self.indexing_complete = 0

    def compose(self) -> ComposeResult:
        yield self._widget

    def on_nova_text_area_indexing_complete(self, _message: NovaTextArea.IndexingComplete) -> None:
        self.indexing_complete += 1


def _source(area: NovaTextArea) -> object:
    return area.document._source


async def _wait_for(pilot: Pilot[None], condition: Callable[[], bool], limit: int = 200) -> None:
    for _ in range(limit):
        if condition():
            return
        await pilot.pause()
    pytest.fail("condition not reached")


def test_widget_never_constructs_a_stock_document() -> None:
    """The widget package builds neither `Document(...)` nor the non-lazy `WrappedDocument(...)`."""
    pattern = re.compile(r"(?<![A-Za-z_])(?:Document|WrappedDocument|SyntaxAwareDocument)\(")
    hits: list[str] = []
    for path in sorted(WIDGET_DIR.glob("*.py")):
        for number, line in enumerate(path.read_text().splitlines(), start=1):
            code = line.split("#", 1)[0]
            match = pattern.search(code)
            if match is not None and match.group(0) != "SyntaxAwareDocument(":
                hits.append(f"{path.name}:{number}: {line.strip()}")
    assert hits == []
    for path in sorted(WIDGET_DIR.glob("*.py")):
        source = path.read_text()
        assert "import Document," not in source
        assert "_wrapped_document import WrappedDocument" not in source


def test_text_argument_is_a_lazy_document_over_bytes() -> None:
    area = NovaTextArea(text="é\r\nb\rc\nd")
    assert isinstance(area.document, LazyDocument)
    assert isinstance(_source(area), BytesSource)
    assert area.document.snapshot().complete
    assert area.document.line_count == 4
    assert area.text == "é\r\nb\rc\nd"  # exact bytes: mixed endings are not normalised (REQ-13)


def test_text_argument_keeps_invalid_bytes_as_escapes() -> None:
    area = NovaTextArea(text="a\udcffb")
    assert area.document.length == 3
    assert area.text == "a\udcffb"


def test_large_text_is_scanned_at_once() -> None:
    text = "line\n" * 300_000  # above the 1 MiB default of the synchronous scan
    area = NovaTextArea(text=text)
    assert area.document.snapshot().complete
    assert area.line_count == 300_001
    assert area.text == text


def test_unicode_separators_do_not_split_rows() -> None:
    """DEC-13: only LF, CRLF and CR end a row (the stock `splitlines` also split on VT, FF, FS, GS, RS, NEL, LS and PS)."""
    text = "a\x0bb\x0cc\x1cd\x1de\x1ef\x85g" + chr(0x2028) + "h" + chr(0x2029) + "i"
    area = NovaTextArea(text=text)
    assert area.line_count == 1
    assert area.document.get_line(0) == text
    assert area.text == text


def test_text_property_is_empty_above_its_limit(monkeypatch: pytest.MonkeyPatch) -> None:
    area = NovaTextArea(text="12345\n67890")
    assert area.text == "12345\n67890"
    monkeypatch.setattr(text_area_module, "TEXT_LIMIT", 10)
    assert area.text == ""
    monkeypatch.setattr(text_area_module, "TEXT_LIMIT", 11)
    assert area.text == "12345\n67890"


def test_selected_text_refuses_what_get_text_range_refuses() -> None:
    area = NovaTextArea(text="\n".join(f"row {n}" for n in range(300)))
    area.select_all()
    with pytest.raises(WholeLineAccess):
        area.get_text_range(*area.selection)
    with pytest.raises(WholeLineAccess):
        _ = area.selected_text
    area.selection = type(area.selection)((0, 0), (5, 3))
    assert area.selected_text == area.get_text_range((0, 0), (5, 3))
    assert area.selected_text == "row 0\nrow 1\nrow 2\nrow 3\nrow 4\nrow"


@pytest.mark.asyncio
async def test_copy_of_a_selection_over_many_rows_works() -> None:
    text = "\n".join(f"row {n}" for n in range(300))
    area = NovaTextArea(text=text)
    async with _Host(area).run_test() as pilot:
        await pilot.pause()
        area.select_all()
        area.action_copy()
        await pilot.pause()
        assert pilot.app.clipboard == text


@pytest.mark.skipif(not text_area_module.TREE_SITTER, reason="tree-sitter is not installed")
def test_text_argument_with_a_language_gets_the_highlight_mirror() -> None:
    area = NovaTextArea(text=PYTHON_TEXT, language="python")
    assert area.highlight_active
    assert area.is_syntax_aware
    assert isinstance(area.document._syntax, SyntaxAwareDocument)
    assert area._highlights


@pytest.mark.skipif(not text_area_module.TREE_SITTER, reason="tree-sitter is not installed")
def test_edits_are_forwarded_to_the_mirror_of_a_text_widget() -> None:
    area = NovaTextArea(text=PYTHON_TEXT, language="python")
    area.insert("x = 1\n", (0, 0))
    mirror = area.document._syntax
    assert mirror is not None
    assert mirror.text == area.text == "x = 1\n" + PYTHON_TEXT
    area.delete((0, 0), (1, 0))
    assert mirror.text == area.text == PYTHON_TEXT


@pytest.mark.skipif(not text_area_module.TREE_SITTER, reason="tree-sitter is not installed")
def test_text_above_the_highlight_limit_stays_plain(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(text_area_module, "DEFAULT_HIGHLIGHT_LIMIT", 10)
    area = NovaTextArea(text=PYTHON_TEXT, language="python")
    assert area.highlight_active is False
    assert not area.document.has_syntax


@pytest.mark.skipif(not text_area_module.TREE_SITTER, reason="tree-sitter is not installed")
def test_changing_the_language_keeps_the_document_and_edits() -> None:
    area = NovaTextArea(text=PYTHON_TEXT)
    assert not area.highlight_active
    document = area.document
    area.insert("# c\n", (0, 0))
    area.language = "python"
    assert area.document is document
    assert area.highlight_active
    assert area.text == "# c\n" + PYTHON_TEXT
    area.language = None
    assert not area.highlight_active
    assert area.text == "# c\n" + PYTHON_TEXT


@pytest.mark.skipif(not text_area_module.TREE_SITTER, reason="tree-sitter is not installed")
def test_load_text_applies_the_current_language() -> None:
    area = NovaTextArea(text="", language="python")
    area.load_text(PYTHON_TEXT)
    assert area.highlight_active
    assert area.document.has_syntax


def test_open_reads_a_small_file_into_memory(tmp_path: Path) -> None:
    path = tmp_path / "small.txt"
    path.write_bytes(b"one\r\ntwo\n\xff")
    area = NovaTextArea.open(path)
    assert isinstance(_source(area), BytesSource)
    assert area.document.snapshot().complete  # scanned on the calling thread: exact at once
    assert area.line_count == 3
    assert area.text == "one\r\ntwo\n\udcff"
    path.write_bytes(b"changed")  # the file is not held open and the document does not read it again
    assert area.text == "one\r\ntwo\n\udcff"
    area.close()


def test_open_keeps_a_large_file_on_a_pread_source(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    path = tmp_path / "large.txt"
    path.write_bytes(b"x\n" * 100)
    monkeypatch.setattr(text_area_module, "SMALL_FILE_LIMIT", 199)
    area = NovaTextArea.open(path)
    assert isinstance(_source(area), PreadSource)
    area.close()
    monkeypatch.setattr(text_area_module, "SMALL_FILE_LIMIT", 200)
    area = NovaTextArea.open(path)
    assert isinstance(_source(area), BytesSource)
    area.close()


def test_open_of_a_missing_file_raises(tmp_path: Path) -> None:
    with pytest.raises(FileNotFoundError):
        NovaTextArea.open(tmp_path / "missing.txt")


@pytest.mark.skipif(not text_area_module.TREE_SITTER, reason="tree-sitter is not installed")
def test_open_of_a_small_python_file_highlights(tmp_path: Path) -> None:
    path = tmp_path / "a.py"
    path.write_text(PYTHON_TEXT)
    area = NovaTextArea.open(path, language="python")
    assert area.highlight_active
    area.close()


@pytest.mark.asyncio
async def test_text_widget_edits_undoes_and_reports_indexing() -> None:
    area = NovaTextArea(text="hello\nworld")
    host = _Host(area)
    async with host.run_test() as pilot:
        await pilot.pause()
        assert area.indexing_complete
        await _wait_for(pilot, lambda: host.indexing_complete >= 1)
        area.insert("big ", (0, 0))
        assert area.text == "big hello\nworld"
        area.undo()
        assert area.text == "hello\nworld"
        area.redo()
        assert area.text == "big hello\nworld"


@pytest.mark.asyncio
async def test_load_text_after_mount_replaces_the_document() -> None:
    area = NovaTextArea(text="old")
    host = _Host(area)
    async with host.run_test() as pilot:
        await pilot.pause()
        old = area.document
        area.insert("x", (0, 0))
        area.text = "fresh\nstart"
        await pilot.pause()
        assert area.document is not old
        assert area.text == "fresh\nstart"
        assert area.cursor_location == (0, 0)
        assert not area.history._undo_stack
        assert area.document.snapshot().complete
        await _wait_for(pilot, lambda: host.indexing_complete >= 2)
        await pilot.press("end", "!")
        await pilot.pause()
        assert area.text == "fresh!\nstart"


@pytest.mark.asyncio
async def test_load_text_replaces_an_opened_file(tmp_path: Path) -> None:
    path = tmp_path / "f.txt"
    path.write_text("from disk\n")
    area = NovaTextArea.open(path)
    async with _Host(area).run_test() as pilot:
        await pilot.pause()
        area.load_text("from text")
        await pilot.pause()
        assert area.text == "from text"
        assert area.line_count_exact


@pytest.mark.asyncio
async def test_bracket_matching_works_on_a_small_document(monkeypatch: pytest.MonkeyPatch) -> None:
    area = NovaTextArea(text="f(a, (b))\n")
    async with _Host(area).run_test() as pilot:
        await pilot.pause()
        assert area.find_matching_bracket("(", (0, 1)) == (0, 8)
        assert area.find_matching_bracket(")", (0, 8)) == (0, 1)
        monkeypatch.setattr(text_area_module, "BRACKET_SEARCH_LIMIT", 3)
        assert area.find_matching_bracket("(", (0, 1)) is None


@pytest.mark.asyncio
async def test_empty_text_shows_the_placeholder() -> None:
    area = NovaTextArea(text="", placeholder="type here")
    async with _Host(area).run_test(size=(30, 5)) as pilot:
        await pilot.pause()
        assert "type here" in "".join(segment.text for segment in area.render_line(0))
        area.insert("x", (0, 0))
        await pilot.pause()
        assert "type here" not in "".join(segment.text for segment in area.render_line(0))
