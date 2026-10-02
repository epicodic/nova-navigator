"""Brute-force reference for literal search: decode to `str`, compare token by token, map back to byte offsets.

This module is an oracle for the production matcher and imports no production code.
"""

from __future__ import annotations

type Token = str | None
"""One unit of text: a single character, or `None` for a line break (`\\r\\n`, `\\n` or `\\r`)."""


def ref_fold(char: str) -> str:
    """Return the simple fold of one character: `casefold()` if one character, else `lower()` if one, else itself."""
    folded = char.casefold()
    if len(folded) == 1:
        return folded
    lowered = char.lower()
    return lowered if len(lowered) == 1 else char


def _unit_width(text: str, i: int) -> int:
    return 2 if text[i] == "\r" and text[i + 1 : i + 2] == "\n" else 1


def _token(text: str, i: int, width: int) -> Token:
    return None if text[i] in "\r\n" else text[i : i + width]


def split_tokens(text: str) -> list[Token]:
    """Split `text` into tokens; every break is one `None`."""
    out: list[Token] = []
    i = 0
    while i < len(text):
        width = _unit_width(text, i)
        out.append(_token(text, i, width))
        i += width
    return out


def haystack_tokens(data: bytes) -> list[tuple[Token, int, int]]:
    """Return `(token, start byte, end byte)` for every unit of the document."""
    text = data.decode("utf-8", "surrogateescape")
    out: list[tuple[Token, int, int]] = []
    byte = 0
    i = 0
    while i < len(text):
        width = _unit_width(text, i)
        size = len(text[i : i + width].encode("utf-8", "surrogateescape"))
        out.append((_token(text, i, width), byte, byte + size))
        byte += size
        i += width
    return out


def _same(doc: Token, need: Token, *, case_sensitive: bool) -> bool:
    if doc is None or need is None:
        return doc is need
    return doc == need if case_sensitive else ref_fold(doc) == ref_fold(need)


def all_matches(data: bytes, needle: str, *, case_sensitive: bool) -> list[tuple[int, int]]:
    """Return the byte span of every (possibly overlapping) match, ordered by start. An empty needle never matches."""
    hay = haystack_tokens(data)
    pat = split_tokens(needle)
    count = len(pat)
    if count == 0:
        return []
    return [(hay[i][1], hay[i + count - 1][2]) for i in range(len(hay) - count + 1) if all(_same(hay[i + k][0], pat[k], case_sensitive=case_sensitive) for k in range(count))]


def boundaries(data: bytes) -> list[int]:
    """Every offset at which a search may start: token boundaries including 0 and the length."""
    return [0, *(end for _, _, end in haystack_tokens(data))]


def _latest(group: list[tuple[int, int]]) -> tuple[int, int]:
    return max(group, key=lambda m: (m[1], m[0]))


def reference_search(
    data: bytes,
    needle: str,
    *,
    case_sensitive: bool,
    backward: bool,
    wrap: bool,
    origin: int,
) -> tuple[int, int, bool] | None:
    """Return `(start, end, wrapped)` of the next match from `origin`, or `None`."""
    found = all_matches(data, needle, case_sensitive=case_sensitive)
    if not backward:
        first = [m for m in found if m[0] >= origin]
        second = [m for m in found if m[0] < origin]
        if first:
            return (*min(first), False)
        if wrap and second:
            return (*min(second), True)
        return None
    first = [m for m in found if m[1] <= origin]
    second = [m for m in found if m[1] > origin]
    if first:
        return (*_latest(first), False)
    if wrap and second:
        return (*_latest(second), True)
    return None
