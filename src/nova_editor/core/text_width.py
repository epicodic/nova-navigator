"""Display-width and UTF-8 boundary helpers shared by the long-line index (REQ-4).

Tab stops follow `textual.expand_tabs` semantics without importing Textual; widths come from `rich.cells`.
"""

from __future__ import annotations

from rich.cells import cell_len

TAB_WIDTH = 4
SURROGATE_ESCAPE = "surrogateescape"

_CHUNK = 1024
_MAX_SEQUENCE = 4
_CONTINUATION_MASK = 0xC0
_CONTINUATION_TAG = 0x80
_LEAD_2_LIMIT = 0xE0
_LEAD_3_LIMIT = 0xF0
_MAX_RESYNC = 3


def _plain(text: str) -> bool:
    return text.isascii() and text.isprintable()


def advance_disp(text: str, disp: int, tab: int = TAB_WIDTH) -> int:
    """Return the display column after `text` when it starts at display column `disp`."""
    if "\t" not in text:
        return disp + (len(text) if _plain(text) else cell_len(text))
    parts = text.split("\t")
    last = len(parts) - 1
    for index, part in enumerate(parts):
        if part:
            disp += len(part) if _plain(part) else cell_len(part)
        if index != last:
            disp += tab - disp % tab
    return disp


def locate_cover(text: str, disp0: int, target: int, tab: int = TAB_WIDTH) -> tuple[int, bool]:
    """Return the index of the first character of `text` whose end display column exceeds `target`.

    `disp0` is the display column of `text[0]`. When no character covers `target`, the result is `(len(text), False)`.
    """
    size = len(text)
    if target < disp0:
        return 0, size > 0
    if _plain(text) and "\t" not in text:
        offset = target - disp0
        return (offset, True) if offset < size else (size, False)
    disp = disp0
    pos = 0
    while pos < size:
        chunk = text[pos : pos + _CHUNK]
        end = advance_disp(chunk, disp, tab)
        if end > target:
            for index, char in enumerate(chunk):
                disp += tab - disp % tab if char == "\t" else cell_len(char)
                if disp > target:
                    return pos + index, True
            return pos + len(chunk) - 1, True
        disp = end
        pos += _CHUNK
    return size, False


def safe_cut(data: bytes) -> int:
    """Return the length of `data` without a trailing incomplete UTF-8 sequence."""
    size = len(data)
    for back in range(1, min(_MAX_SEQUENCE, size) + 1):
        byte = data[-back]
        if byte & _CONTINUATION_MASK == _CONTINUATION_TAG:
            continue
        if byte >= _CONTINUATION_MASK:
            need = 2 if byte < _LEAD_2_LIMIT else 3 if byte < _LEAD_3_LIMIT else 4
            if need > back:
                return size - back
        return size
    return size


def resync(data: bytes) -> int:
    """Return how many leading continuation bytes to skip so that `data[k:]` starts on a character boundary."""
    skip = 0
    while skip < _MAX_RESYNC and skip < len(data) and data[skip] & _CONTINUATION_MASK == _CONTINUATION_TAG:
        skip += 1
    return skip


def utf8_len(text: str) -> int:
    """Return the number of bytes `text` occupies when encoded with `surrogateescape`."""
    return len(text) if text.isascii() else len(text.encode("utf-8", SURROGATE_ESCAPE))
