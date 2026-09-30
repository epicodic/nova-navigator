"""Display-width and UTF-8 boundary helpers shared by the long-line index (REQ-4).

Tab stops follow `textual.expand_tabs` semantics without importing Textual; widths come from `rich.cells`.
"""

from __future__ import annotations

import re

from rich.cells import cell_len

TAB_WIDTH = 4
SURROGATE_ESCAPE = "surrogateescape"

_CHUNK = 1024
MAX_SEQUENCE = 4  # the longest UTF-8 sequence in bytes
_CONTINUATION_MASK = 0xC0
_CONTINUATION_TAG = 0x80
_LEAD_2_LIMIT = 0xE0
_LEAD_3_LIMIT = 0xF0
_MAX_RESYNC = 3


_NON_ASCII = re.compile(r"[^\x00-\x7f]+")
_ASCII = re.compile(r"[\x00-\x7f]+")
_GROUP = 64  # characters skipped at once while searching a chunk
_SPLIT_MIN = 512  # below this rich's own cell_len is fast enough


def _plain(text: str) -> bool:
    return text.isascii() and text.isprintable()


def _cells(text: str) -> int:
    """Return the display width of `text` without tabs.

    A long text with some non-ASCII characters is split into its ASCII and non-ASCII runs, so that the per-character width
    lookup only runs over the non-ASCII part. Widths are additive per character, so the result equals `cell_len(text)`.
    Every step is a C call over a bounded piece, which keeps the time the scan thread holds the GIL short.
    """
    if len(text) < _SPLIT_MIN or text.isascii():
        return len(text) if _plain(text) else cell_len(text)
    ascii_part = _NON_ASCII.sub("", text)
    return (len(ascii_part) if ascii_part.isprintable() else cell_len(ascii_part)) + cell_len(_ASCII.sub("", text))


def advance_disp(text: str, disp: int, tab: int = TAB_WIDTH) -> int:
    """Return the display column after `text` when it starts at display column `disp`."""
    if "\t" not in text:
        return disp + _cells(text)
    parts = text.split("\t")
    last = len(parts) - 1
    for index, part in enumerate(parts):
        if part:
            disp += _cells(part)
        if index != last:
            disp += tab - disp % tab
    return disp


def _cover_in_chunk(chunk: str, disp: int, target: int, tab: int) -> int:
    """Return the index in `chunk` (which starts at display column `disp`) of the character whose end column exceeds `target`.

    The caller knows that the chunk ends beyond `target`. Small groups are skipped with `advance_disp`, so that the per-character
    width lookup only runs over one group.
    """
    start = 0
    while start + _GROUP < len(chunk):
        after = advance_disp(chunk[start : start + _GROUP], disp, tab)
        if after > target:
            break
        disp = after
        start += _GROUP
    for index in range(start, min(start + _GROUP, len(chunk))):
        char = chunk[index]
        disp += tab - disp % tab if char == "\t" else cell_len(char)
        if disp > target:
            return index
    return len(chunk) - 1


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
            return pos + _cover_in_chunk(chunk, disp, target, tab), True
        disp = end
        pos += _CHUNK
    return size, False


def safe_cut(data: bytes) -> int:
    """Return the length of `data` without a trailing incomplete UTF-8 sequence."""
    size = len(data)
    for back in range(1, min(MAX_SEQUENCE, size) + 1):
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
