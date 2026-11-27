"""View-only mode of the editor widget: the three guards hold without the read-only flag."""

from __future__ import annotations

from pathlib import Path

import pytest
from textual.app import App, ComposeResult

from nova_editor.document_view import DocumentView
from nova_editor.timed_text_area import TimedNovaTextArea
from nova_editor.widget import NovaTextArea


def _open(path: Path) -> DocumentView:
    view, error = DocumentView.open(
        path,
        editor_class=TimedNovaTextArea,
        soft_wrap=False,
        show_line_numbers=True,
        config=None,
        timing_file=None,
        view_only=True,
    )
    assert error is None
    return view


class _WidgetApp(App[None]):
    def __init__(self, view: DocumentView) -> None:
        super().__init__()
        self.view = view

    def compose(self) -> ComposeResult:
        yield self.view.editor


def _try_every_programmatic_edit(editor: NovaTextArea) -> None:
    editor.insert("x")
    editor.delete((0, 0), (0, 1))
    editor.replace("y", (0, 0), (0, 1))
    editor.clear()


@pytest.mark.asyncio
async def test_view_only_refuses_save_undo_and_programmatic_edits(tmp_path: Path) -> None:
    source = tmp_path / "a.txt"
    source.write_bytes(b"one\ntwo\n")
    app = _WidgetApp(_open(source))
    async with app.run_test() as pilot:
        await pilot.pause()
        editor = app.view.editor
        assert editor.read_only
        editor.read_only = False  # the lock and the refused save must hold without layer 1
        _try_every_programmatic_edit(editor)
        editor.undo()
        editor.redo()
        await pilot.pause()
        assert editor.text == "one\ntwo\n"
        assert editor.save() is False
        assert editor.save(tmp_path / "other.txt", overwrite=True) is False
    assert not (tmp_path / "other.txt").exists()
    assert source.read_bytes() == b"one\ntwo\n"


@pytest.mark.asyncio
async def test_view_only_lock_survives_reload(tmp_path: Path) -> None:
    source = tmp_path / "a.txt"
    source.write_bytes(b"one\n")
    app = _WidgetApp(_open(source))
    async with app.run_test() as pilot:
        await pilot.pause()
        editor = app.view.editor
        editor.read_only = False
        source.write_bytes(b"changed\n")
        assert editor.reload()
        await pilot.pause()
        editor.insert("x")
        assert editor.text == "changed\n"
        editor.load_text("loaded\n")
        editor.insert("x")
        assert editor.text == "loaded\n"
