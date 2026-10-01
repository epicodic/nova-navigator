"""`str` reference model of an editable document: rows and columns in characters, with the newline rule of ACT4 design 7.3.

The model keeps the whole text as one `str` (invalid bytes are the characters U+DC80 to U+DCFF) and re-normalises it through
UTF-8 after every edit, so characters that merge at a junction behave as they do in the byte document.
"""

from __future__ import annotations

import re

Location = tuple[int, int]

_BREAK = re.compile(r"\r\n|\r|\n")
_BYTE_BREAK = re.compile(rb"\r\n|\r|\n")
_SURROGATE = "surrogateescape"


def encode(text: str) -> bytes:
    """Return the bytes of `text`."""
    return text.encode("utf-8", _SURROGATE)


def decode(data: bytes) -> str:
    """Return the text of `data` with invalid bytes as U+DC80 to U+DCFF."""
    return data.decode("utf-8", _SURROGATE)


def choose_terminator(row_terminator: str, document_newline: str, left: str, right: str) -> str:
    """Return the terminator `T` for inserted newlines.

    The row's own terminator (else the document newline), then LF, CRLF, CR; `T` must not start with LF after a CR and must not end with CR before an LF.
    """
    first = row_terminator or document_newline
    for candidate in (first, "\n", "\r\n", "\r"):
        if left == "\r" and candidate.startswith("\n"):
            continue
        if right == "\n" and candidate.endswith("\r"):
            continue
        return candidate
    raise AssertionError("unreachable: CRLF is always allowed")


class RefText:
    """Editable text with rows, columns and the same normalisation as `LazyDocument.replace_range`."""

    def __init__(self, text: str) -> None:
        self.text = decode(encode(text))

    # -- structure ---------------------------------------------------------------------------
    def spans(self) -> list[tuple[int, int, int]]:
        """Return `(start, content_end, end)` of every row, in characters."""
        result: list[tuple[int, int, int]] = []
        start = 0
        for match in _BREAK.finditer(self.text):
            result.append((start, match.start(), match.end()))
            start = match.end()
        result.append((start, len(self.text), len(self.text)))
        return result

    @property
    def line_count(self) -> int:
        return len(self.spans())

    def row(self, index: int) -> str:
        start, content_end, _ = self.spans()[index]
        return self.text[start:content_end]

    @property
    def rows(self) -> list[str]:
        return [self.text[a:b] for a, b, _ in self.spans()]

    @property
    def newline(self) -> str:
        first = self.spans()[0]
        return self.text[first[1] : first[2]] or "\n"

    @property
    def end(self) -> Location:
        rows = self.rows
        return (len(rows) - 1, len(rows[-1]))

    def data(self) -> bytes:
        return encode(self.text)

    def offset(self, location: Location) -> int:
        """Return the character index of `location`; the column is clamped to the row."""
        row, column = location
        start, content_end, _ = self.spans()[row]
        return start + min(max(column, 0), content_end - start)

    def get_text_range(self, start: Location, end: Location) -> str:
        """Text between two locations with the rows joined by the document newline (as `LazyDocument.get_text_range`)."""
        if start == end:
            return ""
        (top_row, top_col), (bottom_row, bottom_col) = sorted((start, end))
        rows = self.rows
        if top_row == bottom_row:
            return rows[top_row][top_col:bottom_col]
        parts = [rows[top_row][top_col:], *rows[top_row + 1 : bottom_row], rows[bottom_row][:bottom_col]]
        return self.newline.join(parts)

    # -- editing -----------------------------------------------------------------------------
    def normalised(self, start: Location, end: Location, text: str) -> str:
        """Return `text` with every newline replaced by the terminator chosen for an insert at `start`."""
        top, bottom = sorted((start, end))
        s, e = self.offset(top), self.offset(bottom)
        span = self.spans()[top[0]]
        left = self.text[s - 1] if s > 0 else ""
        right = self.text[e] if e < len(self.text) else ""
        terminator = choose_terminator(self.text[span[1] : span[2]], self.newline, left, right)
        return _BREAK.sub(lambda _: terminator, text)

    def replace_range(self, start: Location, end: Location, text: str) -> Location:
        """Replace the range and return the end location of the inserted text."""
        top, bottom = sorted((start, end))
        s, e = self.offset(top), self.offset(bottom)
        inserted = self.normalised(start, end, text)
        end_byte = len(encode(self.text[:s])) + len(encode(inserted))
        self.text = decode(encode(self.text[:s] + inserted + self.text[e:]))
        return self._location_of_byte(end_byte)

    def _location_of_byte(self, end_byte: int) -> Location:
        """Row and column of a byte offset: the column counts the characters that start before it; inside a CRLF the location is after the LF."""
        data = self.data()
        if end_byte > 0 and data[end_byte - 1 : end_byte + 1] == b"\r\n":
            end_byte += 1
        row = 0
        row_start = 0
        for match in _BYTE_BREAK.finditer(data):
            if match.end() > end_byte:
                break
            row += 1
            row_start = match.end()
        used = 0
        column = 0
        for char in self.rows[row]:
            if row_start + used >= end_byte:
                break
            used += len(encode(char))
            column += 1
        return (row, column)
