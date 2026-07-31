"""Tests for the VFS shell line editor."""

from __future__ import annotations

from nova_navigator.terminal.vfs_shell.line_editor import LineEditor, LineEditorEvent


def test_basic_typing() -> None:
    editor = LineEditor()
    for ch in "hello":
        event = editor.feed(ch)
        assert event == LineEditorEvent.CHAR
    assert editor.line == "hello"


def test_enter_returns_complete_line() -> None:
    editor = LineEditor()
    for ch in "ls -la":
        editor.feed(ch)
    event = editor.feed("\r")
    assert event == LineEditorEvent.COMPLETE_LINE
    assert editor.line == "ls -la"


def test_backspace_deletes_char() -> None:
    editor = LineEditor()
    for ch in "abc":
        editor.feed(ch)
    editor.feed("\x7f")  # DEL / backspace
    assert editor.line == "ab"


def test_backspace_at_start_does_nothing() -> None:
    editor = LineEditor()
    event = editor.feed("\x7f")
    assert event == LineEditorEvent.CHAR
    assert editor.line == ""


def test_ctrl_c_interrupt() -> None:
    editor = LineEditor()
    for ch in "partial":
        editor.feed(ch)
    event = editor.feed("\x03")  # Ctrl+C
    assert event == LineEditorEvent.INTERRUPT


def test_ctrl_d_eof_on_empty() -> None:
    editor = LineEditor()
    event = editor.feed("\x04")  # Ctrl+D
    assert event == LineEditorEvent.EOF


def test_ctrl_d_not_eof_when_content() -> None:
    editor = LineEditor()
    editor.feed("x")
    event = editor.feed("\x04")
    assert event == LineEditorEvent.CHAR


def test_ctrl_u_kills_line() -> None:
    editor = LineEditor()
    for ch in "hello world":
        editor.feed(ch)
    editor.feed("\x15")  # Ctrl+U
    assert editor.line == ""


def test_ctrl_w_kills_word() -> None:
    editor = LineEditor()
    for ch in "hello world":
        editor.feed(ch)
    editor.feed("\x17")  # Ctrl+W
    assert editor.line == "hello "


def test_history_up() -> None:
    editor = LineEditor(history=["first", "second"])
    editor.feed("\x1b[A")  # Up arrow (as single escape sequence)
    assert editor.line == "second"


def test_history_down() -> None:
    editor = LineEditor(history=["first", "second"])
    editor.feed("\x1b[A")  # Up
    editor.feed("\x1b[A")  # Up
    editor.feed("\x1b[B")  # Down
    assert editor.line == "second"


def test_reset_clears_line() -> None:
    editor = LineEditor()
    for ch in "content":
        editor.feed(ch)
    editor.reset()
    assert editor.line == ""


def test_echo_for_regular_char() -> None:
    editor = LineEditor()
    editor.feed("a")
    assert editor.echo == "a"


def test_echo_for_backspace() -> None:
    editor = LineEditor()
    editor.feed("a")
    editor.feed("\x7f")
    assert editor.echo == "\b \b"


def test_tab_returns_tab_event() -> None:
    editor = LineEditor()
    event = editor.feed("\t")
    assert event == LineEditorEvent.TAB
