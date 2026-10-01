"""Tests of the highlight limit of `NovaTextArea.open` (ACT3 design 10)."""

from __future__ import annotations

from pathlib import Path

import pytest
from textual.app import App, ComposeResult

from nova_editor.document._lazy_config import LazyConfig
from nova_editor.document._lazy_document import LazyDocument
from nova_editor.widget import NovaTextArea
from nova_editor.widget._text_area import TREE_SITTER
from tests.nova_editor.helpers_view import LOWERED_OPTIONS, HostApp, await_first_layout

PY = "def f(x):\n    return x + 1\n" * 20


class PairApp(App[None]):
    """Hosts a stock and a lazy widget side by side."""

    def __init__(self, *widgets: NovaTextArea) -> None:
        super().__init__()
        self._widgets = widgets

    def compose(self) -> ComposeResult:
        yield from self._widgets


def _write(tmp_path: Path) -> Path:
    path = tmp_path / "a.py"
    path.write_text(PY)
    return path


def _normalised(area: NovaTextArea) -> dict[int, list[tuple[int, int | None, str]]]:
    """The highlight map with the captures of every row in a stable order (the order within a row is not defined)."""
    return {row: sorted(items, key=repr) for row, items in area._highlights.items()}


def _spy_read_all(area: NovaTextArea, monkeypatch: pytest.MonkeyPatch) -> list[int]:
    """Record every `read_all` call of the widget's lazy document."""
    doc = area.document
    assert isinstance(doc, LazyDocument)
    calls: list[int] = []
    original = doc.read_all

    def spy(limit: int) -> str:
        calls.append(limit)
        return original(limit)

    monkeypatch.setattr(doc, "read_all", spy)
    return calls


@pytest.mark.skipif(not TREE_SITTER, reason="tree-sitter is not installed")
@pytest.mark.asyncio
async def test_small_python_file_is_highlighted_like_stock(tmp_path: Path) -> None:
    path = _write(tmp_path)
    stock = NovaTextArea(PY, language="python")
    lazy = NovaTextArea.open(path, language="python", highlight_limit=10_000)
    assert lazy.highlight_active
    async with PairApp(stock, lazy).run_test() as pilot:
        await pilot.pause()
        assert stock._highlights
        assert _normalised(lazy) == _normalised(stock)


@pytest.mark.asyncio
async def test_file_above_limit_builds_no_parser(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    path = _write(tmp_path)
    lazy = NovaTextArea.open(path, language="python", highlight_limit=100)
    calls = _spy_read_all(lazy, monkeypatch)
    async with HostApp(lazy).run_test() as pilot:
        await await_first_layout(pilot, lazy)
        assert lazy._highlight_query is None
        assert not lazy._highlights
        assert not lazy.highlight_active
        assert calls == []
        doc = lazy.document
        assert isinstance(doc, LazyDocument)
        assert not doc.call_log.refusals


@pytest.mark.asyncio
async def test_no_language_is_not_highlighted(tmp_path: Path) -> None:
    lazy = NovaTextArea.open(_write(tmp_path), highlight_limit=10_000)
    assert not lazy.highlight_active
    assert lazy._highlight_query is None


@pytest.mark.skipif(not TREE_SITTER, reason="tree-sitter is not installed")
@pytest.mark.asyncio
async def test_small_file_document_delegates_to_the_parser(tmp_path: Path) -> None:
    lazy = NovaTextArea.open(_write(tmp_path), language="python", highlight_limit=10_000)
    doc = lazy.document
    assert isinstance(doc, LazyDocument)
    query = doc.prepare_query("(identifier) @name")
    assert query is not None
    assert len(doc.query_syntax_tree(query)["name"]) > 0
    assert lazy.is_lazy
    assert not lazy.read_only


@pytest.mark.asyncio
async def test_lazy_python_file_above_lowered_limit_opens_renders_and_is_editable(tmp_path: Path) -> None:
    lazy = NovaTextArea.open(_write(tmp_path), language="python", highlight_limit=100, config=LazyConfig(**LOWERED_OPTIONS))
    async with HostApp(lazy).run_test() as pilot:
        await await_first_layout(pilot, lazy)
        assert "def f(x)" in "".join(segment.text for segment in lazy.render_line(0))
        assert not lazy.read_only
        assert not lazy.highlight_active
