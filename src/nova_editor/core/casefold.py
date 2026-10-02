"""Simple (1:1) case folding for literal search (ACT6 design 4.1).

Two characters match when their `fold1` is equal.
`fold1` is `str.casefold` when that gives one character, else `str.lower` when that gives one character, else the character itself.
The variants of a character are the inverse image of its `fold1`: every code point with the same `fold1`, including the character.
Full folding (`ß` = `ss`) is not applied; the match length would differ from the needle length.
"""

from __future__ import annotations

import sys
import threading

_FIRST_SURROGATE = 0xD800
_LAST_SURROGATE = 0xDFFF
_ASCII_EXTRA = {"k": "\u212a", "s": "\u017f"}
"""Non-ASCII members of the class of an ASCII letter (Kelvin sign, long s); `test_static_ascii_table_equals_the_full_scan` proves the table."""


def fold1(char: str) -> str:
    """Return the simple case fold of one character."""
    folded = char.casefold()
    if len(folded) == 1:
        return folded
    lowered = char.lower()
    return lowered if len(lowered) == 1 else char


def _build_ascii() -> dict[str, tuple[str, ...]]:
    table: dict[str, tuple[str, ...]] = {}
    for code in range(ord("a"), ord("z") + 1):
        lower = chr(code)
        members = {lower, lower.upper()}
        if lower in _ASCII_EXTRA:
            members.add(_ASCII_EXTRA[lower])
        group = tuple(sorted(members))
        for member in group:
            if member.isascii():
                table[member] = group
    return table


_ASCII = _build_ascii()


class _Holder:
    table: dict[str, tuple[str, ...]] | None = None


_FULL_LOCK = threading.Lock()


def ascii_table() -> dict[str, tuple[str, ...]]:
    """Return the static table: each ASCII letter, upper and lower case, mapped to its sorted class (a copy)."""
    return dict(_ASCII)


def build_variant_table() -> dict[str, tuple[str, ...]]:
    """Scan every code point and return the classes with more than one member, each member mapped to the sorted class.

    Surrogates are skipped (they cannot be encoded; the escaped bytes U+DC80 to U+DCFF match only themselves).
    Only code points with `fold1(c) != c` are collected, so the scan keeps a few thousand entries in memory (measured 0.22 s).
    """
    groups: dict[str, set[str]] = {}
    for code in range(sys.maxunicode + 1):
        if _FIRST_SURROGATE <= code <= _LAST_SURROGATE:
            continue
        char = chr(code)
        folded = fold1(char)
        if folded != char:
            groups.setdefault(folded, {folded}).add(char)
    table: dict[str, tuple[str, ...]] = {}
    for group in groups.values():
        ordered = tuple(sorted(group))
        for member in ordered:
            table[member] = ordered
    return table


def _full_table() -> dict[str, tuple[str, ...]]:
    found = _Holder.table
    if found is None:
        with _FULL_LOCK:
            if _Holder.table is None:
                _Holder.table = build_variant_table()
            found = _Holder.table
    return found


def variants(char: str) -> tuple[str, ...]:
    """Return the sorted class of `char` (always contains `char`).

    ASCII letters use the static table; any other character builds the full table once, on first use, under a lock.
    """
    found = _ASCII.get(char)
    if found is not None:
        return found
    if char.isascii():
        return (char,)
    ascii_group = _ASCII.get(fold1(char))
    if ascii_group is not None:
        return ascii_group
    return _full_table().get(char, (char,))
