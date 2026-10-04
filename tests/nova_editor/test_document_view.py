"""The per-document state object (ADR-4, REQ-15)."""

from __future__ import annotations

import dataclasses
import os
from pathlib import Path

import pytest

from nova_editor.document_view import DocumentView
from nova_editor.timed_text_area import TimedNovaTextArea


def _open(path: Path | None) -> tuple[DocumentView, str | None]:
    return DocumentView.open(path, editor_class=TimedNovaTextArea, soft_wrap=False, show_line_numbers=True, config=None, timing_file=None)


def test_no_path_gives_an_empty_loaded_document() -> None:
    view, error = _open(None)
    assert error is None
    assert view.file_path is None
    assert view.load_state == "loaded"
    assert view.editor.text == ""


def test_an_existing_file_is_loaded(tmp_path: Path) -> None:
    path = tmp_path / "f.txt"
    path.write_text("one\ntwo\n")
    view, error = _open(path)
    assert error is None
    assert view.file_path == path
    assert view.load_state == "loaded"
    assert view.editor.text == "one\ntwo\n"


def test_a_missing_file_is_a_new_document(tmp_path: Path) -> None:
    path = tmp_path / "missing.txt"
    view, error = _open(path)
    assert view.load_state == "new"
    assert view.file_path == path
    assert error is not None
    assert error.startswith("Error loading file:")


def test_a_directory_is_a_failed_load(tmp_path: Path) -> None:
    view, error = _open(tmp_path)
    assert view.load_state == "failed"
    assert error == f"Error loading file: {tmp_path}: not a regular file"


def test_a_fifo_is_a_failed_load_and_is_not_opened(tmp_path: Path) -> None:
    fifo = tmp_path / "pipe"
    os.mkfifo(fifo)
    view, error = _open(fifo)
    assert view.load_state == "failed"
    assert error is not None
    assert "not a regular file" in error


def test_the_view_holds_no_wrap_or_line_number_state() -> None:
    """The widget owns `soft_wrap` and `show_line_numbers` (ADR-4); the view keeps no copy."""
    names = {field.name for field in dataclasses.fields(DocumentView)}
    assert names == {"editor", "file_path", "load_state", "needle", "last_backward", "deferred_change", "polling"}


def test_two_views_do_not_share_state() -> None:
    first, _ = _open(None)
    second, _ = _open(None)
    first.needle = "x"
    assert second.needle is None
    assert first.editor is not second.editor


@pytest.mark.parametrize("soft_wrap", [False, True])
def test_the_start_wrap_is_given_to_the_widget(soft_wrap: bool) -> None:
    view, _ = DocumentView.open(None, editor_class=TimedNovaTextArea, soft_wrap=soft_wrap, show_line_numbers=True, config=None, timing_file=None)
    assert view.editor.soft_wrap is soft_wrap


@pytest.mark.parametrize("show_line_numbers", [False, True])
def test_the_start_gutter_is_given_to_the_widget(show_line_numbers: bool) -> None:
    view, _ = DocumentView.open(None, editor_class=TimedNovaTextArea, soft_wrap=False, show_line_numbers=show_line_numbers, config=None, timing_file=None)
    assert view.editor.show_line_numbers is show_line_numbers


def test_the_start_gutter_reaches_a_file_widget(tmp_path: Path) -> None:
    path = tmp_path / "f.txt"
    path.write_text("one\n")
    view, _ = _open(path)
    assert view.editor.show_line_numbers is True
