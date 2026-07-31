"""Line editor for VFS shell — handles character-level input."""

from __future__ import annotations

from enum import Enum, auto


class LineEditorEvent(Enum):
    """Events produced by LineEditor.feed()."""

    CHAR = auto()
    COMPLETE_LINE = auto()
    INTERRUPT = auto()
    EOF = auto()
    TAB = auto()


_ESCAPE_SEQ_MIN_LEN = 3


class LineEditor:
    """Character-by-character line editor with history support.

    Handles basic line editing: backspace, Ctrl+U (kill line),
    Ctrl+W (kill word), Ctrl+C (interrupt), Ctrl+D (EOF on empty),
    and arrow-key history navigation.
    """

    def __init__(self, history: list[str] | None = None) -> None:
        self._buf: list[str] = []
        self._cursor: int = 0
        self._history: list[str] = list(history) if history else []
        self._history_index: int = len(self._history)
        self._saved_line: str = ""
        self._search_prefix: str | None = None
        self._echo: str = ""
        self._escape_buf: str = ""

    @property
    def line(self) -> str:
        """Current line buffer content."""
        return "".join(self._buf)

    @property
    def history(self) -> list[str]:
        """Copy of the recorded command history, oldest first."""
        return list(self._history)

    @property
    def echo(self) -> str:
        """Characters to echo back after the last feed() call."""
        return self._echo

    def reset(self) -> None:
        """Clear the buffer for a new prompt."""
        self._buf.clear()
        self._cursor = 0
        self._history_index = len(self._history)
        self._saved_line = ""
        self._search_prefix = None
        self._echo = ""
        self._escape_buf = ""

    def add_to_history(self, line: str) -> None:
        """Add a completed line to history, unless it starts with a space."""
        if line.strip() and not line.startswith(" "):
            self._history.append(line)
        self._history_index = len(self._history)

    def feed(self, data: str) -> LineEditorEvent:
        r"""Process input character(s). Returns the resulting event.

        Iterates over each character in data so that multi-character escape
        sequences (e.g. '\x1b[A') can be passed as a single string or fed
        one character at a time.
        """
        self._echo = ""
        last_event = LineEditorEvent.CHAR
        for ch in data:
            last_event = self._feed_one(ch)
        return last_event

    def _feed_one(self, ch: str) -> LineEditorEvent:
        """Process a single character. Updates self._echo (appends)."""
        # Handle escape sequences
        if self._escape_buf:
            self._escape_buf += ch
            return self._process_escape()

        if ch == "\x1b":
            self._escape_buf = "\x1b"
            return LineEditorEvent.CHAR

        # Any non-arrow key clears the history prefix search
        self._search_prefix = None

        # Ctrl+C — interrupt
        if ch == "\x03":
            self._echo += "^C"
            return LineEditorEvent.INTERRUPT

        # Ctrl+D — EOF on empty line
        if ch == "\x04":
            if not self._buf:
                return LineEditorEvent.EOF
            return LineEditorEvent.CHAR

        # Enter
        if ch in ("\r", "\n"):
            self._echo += "\r\n"
            return LineEditorEvent.COMPLETE_LINE

        # Tab
        if ch == "\t":
            return LineEditorEvent.TAB

        # Backspace / DEL
        if ch in ("\x7f", "\x08"):
            if self._cursor > 0:
                self._buf.pop(self._cursor - 1)
                self._cursor -= 1
                # Rewrite the tail so the terminal shifts it left instead of
                # leaving the deleted character's slot untouched, then erase
                # the now-stale trailing character and restore the cursor.
                tail = "".join(self._buf[self._cursor :])
                self._echo += "\b" + tail + " " + "\b" * (len(tail) + 1)
            return LineEditorEvent.CHAR

        # Ctrl+U — kill line
        if ch == "\x15":
            if self._buf:
                self._echo += "\b \b" * self._cursor
                self._buf.clear()
                self._cursor = 0
            return LineEditorEvent.CHAR

        # Ctrl+W — kill word
        if ch == "\x17":
            if self._cursor > 0:
                i = self._cursor - 1
                # Skip trailing spaces
                while i > 0 and self._buf[i - 1] == " ":
                    i -= 1
                # Skip word characters
                while i > 0 and self._buf[i - 1] != " ":
                    i -= 1
                deleted = self._cursor - i
                self._buf[i : self._cursor] = []
                self._cursor = i
                self._echo += "\b \b" * deleted
            return LineEditorEvent.CHAR

        # Regular character
        self._buf.insert(self._cursor, ch)
        self._cursor += 1
        # If inserting before existing content, rewrite the tail so the
        # terminal shifts it right instead of overwriting it, then move the
        # terminal cursor back to just after the inserted character.
        tail = "".join(self._buf[self._cursor - 1 :])
        self._echo += tail + "\b" * (len(tail) - 1)
        return LineEditorEvent.CHAR

    def _process_escape(self) -> LineEditorEvent:
        """Process accumulated escape sequence buffer."""
        buf = self._escape_buf

        if len(buf) < 2:
            return LineEditorEvent.CHAR

        if buf[1] == "O":
            # SS3 sequence: ESC O <byte> — always exactly 3 bytes.
            if len(buf) < _ESCAPE_SEQ_MIN_LEN:
                return LineEditorEvent.CHAR
        elif buf[1] == "[":
            # CSI sequence: ESC [ <params> <final-byte>. Parameter bytes are
            # digits or ';' (e.g. Delete = ESC [ 3 ~); keep buffering until a
            # non-parameter final byte is seen.
            last = buf[-1]
            if len(buf) < _ESCAPE_SEQ_MIN_LEN or last.isdigit() or last == ";":
                return LineEditorEvent.CHAR
        else:
            # Unrecognized escape prefix — discard immediately.
            self._escape_buf = ""
            return LineEditorEvent.CHAR

        self._escape_buf = ""

        # Arrow keys are sent as CSI (\x1b[A) in normal cursor-key mode or
        # SS3 (\x1bOA) in application cursor-key mode — accept both.
        if buf in ("\x1b[A", "\x1bOA"):  # Up arrow
            self._history_up()
            return LineEditorEvent.CHAR

        if buf in ("\x1b[B", "\x1bOB"):  # Down arrow
            self._history_down()
            return LineEditorEvent.CHAR

        if buf in ("\x1b[C", "\x1bOC"):  # Right arrow
            if self._cursor < len(self._buf):
                self._cursor += 1
                self._echo += "\x1b[C"
            return LineEditorEvent.CHAR

        if buf in ("\x1b[D", "\x1bOD"):  # Left arrow
            if self._cursor > 0:
                self._cursor -= 1
                self._echo += "\x1b[D"
            return LineEditorEvent.CHAR

        if buf == "\x1b[3~":  # Delete (forward delete)
            if self._cursor < len(self._buf):
                del self._buf[self._cursor]
                # Rewrite the tail so it shifts left, then erase the
                # now-stale trailing character and restore the cursor.
                tail = "".join(self._buf[self._cursor :])
                self._echo += tail + " " + "\b" * (len(tail) + 1)
            return LineEditorEvent.CHAR

        # Unknown escape — discard
        return LineEditorEvent.CHAR

    def _history_up(self) -> None:
        """Navigate to previous history entry matching the current prefix."""
        if self._history_index <= 0:
            return
        # On first Up in a sequence, save the current line and lock in prefix
        if self._search_prefix is None:
            self._saved_line = self.line
            self._search_prefix = self.line
            self._history_index = len(self._history)

        prefix = self._search_prefix
        # Search backwards for a matching entry
        idx = self._history_index - 1
        while idx >= 0:
            if self._history[idx].startswith(prefix):
                self._history_index = idx
                self._set_line(self._history[idx])
                return
            idx -= 1

    def _history_down(self) -> None:
        """Navigate to next history entry matching the current prefix."""
        if self._search_prefix is None or self._history_index >= len(self._history):
            return

        prefix = self._search_prefix
        # Search forwards for a matching entry
        idx = self._history_index + 1
        while idx < len(self._history):
            if self._history[idx].startswith(prefix):
                self._history_index = idx
                self._set_line(self._history[idx])
                return
            idx += 1

        # No more matches — restore saved line
        self._history_index = len(self._history)
        self._set_line(self._saved_line)

    def _set_line(self, text: str) -> None:
        """Replace the current line buffer with text and update echo."""
        clear = "\b \b" * self._cursor
        self._buf = list(text)
        self._cursor = len(self._buf)
        self._echo += clear + text

    @property
    def cursor(self) -> int:
        """Current cursor position in the buffer."""
        return self._cursor

    def replace_word(self, start: int, end: int, replacement: str, cursor_pos: int | None = None) -> str:
        """Replace buffer[start:end] with replacement, updating cursor.

        By default, positions the cursor at the end of *replacement*. Pass
        *cursor_pos* (an absolute buffer index) to place the cursor elsewhere
        instead — e.g. to keep it where the user stopped typing, before the
        auto-completed suffix.

        Returns the echo text needed to erase the old word and write the new
        one on the terminal. Does not affect ``self.echo`` (unrelated to feed()).
        """
        echo = ""
        # Erase from cursor back to start
        back_count = self._cursor - start
        echo += "\b" * back_count
        # Calculate the old visual length from start onwards
        old_tail_len = len(self._buf) - start
        # Replace the slice
        self._buf[start:end] = list(replacement)
        # Rewrite from start position
        new_tail = "".join(self._buf[start:])
        # Overwrite old chars, pad with spaces if new is shorter
        pad = max(0, old_tail_len - len(new_tail))
        echo += new_tail + " " * pad + "\b" * pad
        # Position cursor at end of replacement, unless overridden
        self._cursor = start + len(replacement) if cursor_pos is None else cursor_pos
        # Move cursor back if there's content after the cursor's final position
        trailing = len(self._buf) - self._cursor
        if trailing > 0:
            echo += "\b" * trailing
        return echo
