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
        self._echo: str = ""
        self._escape_buf: str = ""

    @property
    def line(self) -> str:
        """Current line buffer content."""
        return "".join(self._buf)

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
        self._echo = ""
        self._escape_buf = ""

    def add_to_history(self, line: str) -> None:
        """Add a completed line to history."""
        if line.strip():
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
                self._echo += "\b \b"
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
        self._echo += ch
        return LineEditorEvent.CHAR

    def _process_escape(self) -> LineEditorEvent:
        """Process accumulated escape sequence buffer."""
        buf = self._escape_buf

        # Need at least \x1b[ + one char
        if len(buf) < _ESCAPE_SEQ_MIN_LEN:
            return LineEditorEvent.CHAR

        if buf == "\x1b[A":  # Up arrow
            self._escape_buf = ""
            self._history_up()
            return LineEditorEvent.CHAR

        if buf == "\x1b[B":  # Down arrow
            self._escape_buf = ""
            self._history_down()
            return LineEditorEvent.CHAR

        if buf == "\x1b[C":  # Right arrow
            self._escape_buf = ""
            if self._cursor < len(self._buf):
                self._cursor += 1
                self._echo += "\x1b[C"
            return LineEditorEvent.CHAR

        if buf == "\x1b[D":  # Left arrow
            self._escape_buf = ""
            if self._cursor > 0:
                self._cursor -= 1
                self._echo += "\x1b[D"
            return LineEditorEvent.CHAR

        # Unknown escape — discard
        self._escape_buf = ""
        return LineEditorEvent.CHAR

    def _history_up(self) -> None:
        """Navigate to previous history entry."""
        if self._history_index <= 0:
            return
        if self._history_index == len(self._history):
            self._saved_line = self.line
        self._history_index -= 1
        self._set_line(self._history[self._history_index])

    def _history_down(self) -> None:
        """Navigate to next history entry."""
        if self._history_index >= len(self._history):
            return
        self._history_index += 1
        if self._history_index == len(self._history):
            self._set_line(self._saved_line)
        else:
            self._set_line(self._history[self._history_index])

    def _set_line(self, text: str) -> None:
        """Replace the current line buffer with text and update echo."""
        clear = "\b \b" * self._cursor
        self._buf = list(text)
        self._cursor = len(self._buf)
        self._echo += clear + text
