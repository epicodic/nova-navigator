"""Tests for the VFS shell line editor."""

from __future__ import annotations

import pytest

from nova_navigator.terminal.vfs_shell.line_editor import LineEditor, LineEditorEvent, LineEditorSnapshot


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


def test_backspace_mid_line_updates_buffer() -> None:
    editor = LineEditor()
    for ch in "abc":
        editor.feed(ch)
    editor.feed("\x1b[D")  # Left arrow — cursor between "b" and "c"
    editor.feed("\x7f")  # Backspace deletes "b"
    assert editor.line == "ac"
    assert editor.cursor == 1


def test_backspace_mid_line_echo_rewrites_tail() -> None:
    editor = LineEditor()
    for ch in "abc":
        editor.feed(ch)
    editor.feed("\x1b[D")  # Left arrow
    editor.feed("\x7f")  # Backspace
    # Move left over "b", print "c", erase stale trailing char, move back.
    assert editor.echo == "\bc \b\b"


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


def test_insert_mid_line_updates_buffer() -> None:
    editor = LineEditor()
    for ch in "abc":
        editor.feed(ch)
    editor.feed("\x1b[D")  # Left arrow — cursor now between "b" and "c"
    editor.feed("!")
    assert editor.line == "ab!c"
    assert editor.cursor == 3


def test_insert_mid_line_echo_rewrites_tail() -> None:
    editor = LineEditor()
    for ch in "abc":
        editor.feed(ch)
    editor.feed("\x1b[D")  # Left arrow
    editor.feed("!")
    # Terminal must display "!c", then move the cursor back over "c" so it
    # ends up right after "!" — not just print "!" and leave "c" untouched.
    assert editor.echo == "!c\b"


def test_echo_for_backspace() -> None:
    editor = LineEditor()
    editor.feed("a")
    editor.feed("\x7f")
    assert editor.echo == "\b \b"


def test_tab_returns_tab_event() -> None:
    editor = LineEditor()
    event = editor.feed("\t")
    assert event == LineEditorEvent.TAB


def test_delete_key_removes_char_at_cursor() -> None:
    editor = LineEditor()
    for ch in "abc":
        editor.feed(ch)
    editor.feed("\x1b[D")  # Left arrow — cursor between "b" and "c"
    editor.feed("\x1b[D")  # Left arrow — cursor between "a" and "b"
    editor.feed("\x1b[3~")  # Delete (forward delete) — removes "b"
    assert editor.line == "ac"
    assert editor.cursor == 1


def test_delete_key_echo_rewrites_tail() -> None:
    editor = LineEditor()
    for ch in "abc":
        editor.feed(ch)
    editor.feed("\x1b[D")
    editor.feed("\x1b[D")
    editor.feed("\x1b[3~")
    # Print "c" (shifted left), erase the stale trailing char, move back.
    assert editor.echo == "c \b\b"


def test_delete_key_does_not_leak_tilde_as_char() -> None:
    editor = LineEditor()
    editor.feed("ab")
    editor.feed("\x1b[3~")
    assert editor.line == "ab"


def test_delete_key_at_end_does_nothing() -> None:
    editor = LineEditor()
    for ch in "abc":
        editor.feed(ch)
    editor.feed("\x1b[3~")
    assert editor.line == "abc"


def test_history_prefix_match_up() -> None:
    editor = LineEditor(history=["ls -la", "cd /tmp", "ls foo", "cat bar"])
    # Type "ls" then press Up — should get "ls foo" (last ls-prefixed)
    for ch in "ls":
        editor.feed(ch)
    editor.feed("\x1b[A")  # Up
    assert editor.line == "ls foo"


def test_history_up_ss3_sequence() -> None:
    """Up arrow sent as SS3 (\\x1bOA, application cursor-key mode)."""
    editor = LineEditor(history=["first", "second"])
    editor.feed("\x1bOA")  # Up (SS3)
    assert editor.line == "second"


def test_history_down_ss3_sequence() -> None:
    """Down arrow sent as SS3 (\\x1bOB, application cursor-key mode)."""
    editor = LineEditor(history=["first", "second"])
    editor.feed("\x1bOA")  # Up → "second"
    editor.feed("\x1bOA")  # Up → "first"
    editor.feed("\x1bOB")  # Down (SS3) → "second"
    assert editor.line == "second"


def test_history_prefix_match_up_twice() -> None:
    editor = LineEditor(history=["ls -la", "cd /tmp", "ls foo", "cat bar"])
    for ch in "ls":
        editor.feed(ch)
    editor.feed("\x1b[A")  # Up → "ls foo"
    editor.feed("\x1b[A")  # Up → "ls -la"
    assert editor.line == "ls -la"


def test_history_prefix_match_down_restores() -> None:
    editor = LineEditor(history=["ls -la", "cd /tmp", "ls foo", "cat bar"])
    for ch in "ls":
        editor.feed(ch)
    editor.feed("\x1b[A")  # Up → "ls foo"
    editor.feed("\x1b[A")  # Up → "ls -la"
    editor.feed("\x1b[B")  # Down → "ls foo"
    assert editor.line == "ls foo"
    editor.feed("\x1b[B")  # Down → restore saved "ls"
    assert editor.line == "ls"


def test_history_prefix_empty_cycles_all() -> None:
    editor = LineEditor(history=["first", "second", "third"])
    editor.feed("\x1b[A")  # Up → "third"
    assert editor.line == "third"
    editor.feed("\x1b[A")  # Up → "second"
    assert editor.line == "second"


def test_history_prefix_cleared_on_typing() -> None:
    editor = LineEditor(history=["ls -la", "ls foo", "cat bar"])
    for ch in "ls":
        editor.feed(ch)
    editor.feed("\x1b[A")  # Up → "ls foo"
    assert editor.line == "ls foo"
    # Type a char — clears prefix, next Up should use new line as prefix
    editor.feed("x")
    assert editor.line == "ls foox"
    # Up now searches for "ls foox" — no match, line stays
    editor.feed("\x1b[A")
    assert editor.line == "ls foox"


def test_replace_word() -> None:
    editor = LineEditor()
    for ch in "cat rea":
        editor.feed(ch)
    # Replace "rea" (positions 4-7) with "readme.md"
    echo = editor.replace_word(4, 7, "readme.md")
    assert editor.line == "cat readme.md"
    assert editor.cursor == 13
    assert echo == "\b\b\b" + "readme.md"


def test_replace_word_shorter() -> None:
    editor = LineEditor()
    for ch in "cat readme.md":
        editor.feed(ch)
    # Replace "readme.md" (positions 4-13) with "r.md"
    echo = editor.replace_word(4, 13, "r.md")
    assert editor.line == "cat r.md"
    assert editor.cursor == 8
    assert echo == "\b" * 9 + "r.md" + " " * 5 + "\b" * 5


def test_replace_word_keeps_cursor_at_given_position() -> None:
    editor = LineEditor()
    for ch in "cat rea":
        editor.feed(ch)
    # User typed "rea" (cursor at 7); complete to "readme.md" but keep cursor
    # right after the typed prefix instead of at the end of the completion.
    echo = editor.replace_word(4, 7, "readme.md", cursor_pos=7)
    assert editor.line == "cat readme.md"
    assert editor.cursor == 7
    # Cursor ends up 6 positions before the end of the new tail ("dme.md")
    assert echo == "\b\b\b" + "readme.md" + "\b" * 6


def test_replace_word_cursor_pos_mid_line() -> None:
    editor = LineEditor()
    for ch in "cat rea foo":
        editor.feed(ch)
    # Replace "rea" (4-7) with "readme.md", cursor should land right after
    # the typed prefix (position 7) even though there's trailing text " foo".
    editor.replace_word(4, 7, "readme.md", cursor_pos=7)
    assert editor.line == "cat readme.md foo"
    assert editor.cursor == 7


def test_history_property_returns_copy() -> None:
    editor = LineEditor()
    editor.add_to_history("ls -la")
    editor.add_to_history("cd /tmp")
    assert editor.history == ["ls -la", "cd /tmp"]
    # Mutating the returned list must not affect internal state
    editor.history.append("mutated")
    assert editor.history == ["ls -la", "cd /tmp"]


def test_add_to_history_skips_lines_starting_with_space() -> None:
    editor = LineEditor()
    editor.add_to_history("ls -la")
    editor.add_to_history(" secret-command")
    assert editor.history == ["ls -la"]


def test_stash_at_end_preserves_history_and_clears_editor() -> None:
    editor = LineEditor(history=["pwd"])
    editor.feed("abc")

    snapshot = editor.stash()

    assert snapshot == LineEditorSnapshot(line="abc", cursor=3)
    assert editor.line == ""
    assert editor.cursor == 0
    assert editor.history == ["pwd"]


def test_stash_at_middle_preserves_cursor() -> None:
    editor = LineEditor()
    editor.feed("left right")
    editor.feed("\x1b[D" * 5)

    snapshot = editor.stash()

    assert snapshot == LineEditorSnapshot(line="left right", cursor=5)
    assert editor.line == ""
    assert editor.cursor == 0


def test_stash_at_beginning_preserves_cursor_zero() -> None:
    editor = LineEditor()
    editor.feed("hello")
    editor.feed("\x1b[D" * 5)

    snapshot = editor.stash()

    assert snapshot == LineEditorSnapshot(line="hello", cursor=0)
    assert editor.line == ""
    assert editor.cursor == 0


def test_restore_restores_editor_and_returns_echo_for_middle_cursor() -> None:
    editor = LineEditor()

    echo = editor.restore(LineEditorSnapshot(line="left right", cursor=5))

    assert echo == "left right" + "\b" * 5
    assert editor.line == "left right"
    assert editor.cursor == 5


def test_restore_returns_echo_for_cursor_beginning_and_end() -> None:
    editor = LineEditor()

    echo_beginning = editor.restore(LineEditorSnapshot(line="hello", cursor=0))
    assert echo_beginning == "hello" + "\b" * 5
    assert editor.line == "hello"
    assert editor.cursor == 0

    echo_end = editor.restore(LineEditorSnapshot(line="world", cursor=5))
    assert echo_end == "world"
    assert editor.line == "world"
    assert editor.cursor == 5


@pytest.mark.parametrize(
    "snapshot",
    [
        LineEditorSnapshot(line="abc", cursor=-1),
        LineEditorSnapshot(line="abc", cursor=4),
    ],
)
def test_restore_rejects_invalid_cursor(snapshot: LineEditorSnapshot) -> None:
    editor = LineEditor()
    editor.feed("seed")

    with pytest.raises(ValueError, match="cursor"):
        editor.restore(snapshot)

    assert editor.line == "seed"
    assert editor.cursor == 4


def test_stash_restore_empty_state() -> None:
    editor = LineEditor(history=["ls"])

    snapshot = editor.stash()

    assert snapshot == LineEditorSnapshot(line="", cursor=0)
    echo = editor.restore(snapshot)
    assert echo == ""
    assert editor.line == ""
    assert editor.cursor == 0
    assert editor.history == ["ls"]
