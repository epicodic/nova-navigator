"""Literal search over bytes: the needle compiler and the matcher (ACT6 design 4).

The needle is a `str`, encoded as UTF-8 with `surrogateescape`; a match is a byte range that starts and ends on a
character boundary of the document and treats a line break of the needle as exactly one document terminator.
The matcher works on one contiguous window of bytes; the windows, regions and the search loop come in the job part.

The compiled byte pattern of the pattern tier is an implementation device built from escaped bytes only: no user text
is ever interpreted as pattern syntax (DEC-29 note 1), and no pattern feature is exposed.
"""

from __future__ import annotations

import re
from itertools import pairwise

from nova_editor.core.casefold import fold1, variants

MAX_NEEDLE_BYTES = 1 << 20
"""Longest plain needle, in bytes."""
MAX_PATTERN_CHARS = 4096
"""Longest needle (in characters, a line break counting as one) that needs the pattern tier."""
CONTEXT_BEFORE = 3
"""Bytes of document context a window carries before its owned range (boundary check, CR before an LF)."""
CONTEXT_AFTER = 3
"""Bytes of document context a window carries after its owned range (boundary check, LF after a CR)."""

_ESCAPED = range(0xDC80, 0xDD00)
_BREAK_FORWARD = rb"(?:\r\n|(?<!\r)\n|\r(?!\n))"
_BREAK_REVERSED = rb"(?:\n\r|\n(?!\r)|(?<!\n)\r)"
_BREAK_LENGTH = 2
_ASCII_LIMIT = 0x80
_CONTINUATION = range(0x80, 0xC0)
_LEAD_2 = range(0xC2, 0xE0)
_LEAD_3 = range(0xE0, 0xF0)
_LEAD_4 = range(0xF0, 0xF5)
_TIERS = ("auto", "find", "ascii", "pattern")


class SearchError(ValueError):
    """The needle cannot be searched (empty, not encodable, or too long)."""


class SearchCancelled(Exception):
    """The search was cancelled."""


class SearchStale(Exception):
    """The document changed while the search ran."""


def _inside_character(window: bytes, index: int) -> bool:
    """True when byte `index` lies strictly inside a valid multi-byte UTF-8 character of `window`.

    That is a continuation byte whose lead byte is up to 3 bytes before and starts a valid sequence covering `index`.
    """
    if index <= 0 or index >= len(window) or window[index] not in _CONTINUATION:
        return False
    for back in (1, 2, 3):
        start = index - back
        if start < 0:
            return False
        lead = window[start]
        if lead in _CONTINUATION:
            continue
        size = 2 if lead in _LEAD_2 else 3 if lead in _LEAD_3 else 4 if lead in _LEAD_4 else 0
        if size <= back:
            return False
        try:
            window[start : start + size].decode("utf-8")
        except UnicodeDecodeError:
            return False
        return True
    return False


def _is_escaped_token(token: tuple[bytes, ...]) -> bool:
    """True for the token of an escaped byte (U+DC80 to U+DCFF): one encoding of one byte above 0x7F."""
    return len(token) == 1 and len(token[0]) == 1 and token[0][0] >= _ASCII_LIMIT


def _pattern(tokens: list[tuple[bytes, ...]], *, reverse: bool) -> re.Pattern[bytes]:
    parts: list[bytes] = []
    for token in reversed(tokens) if reverse else tokens:
        if not token:
            parts.append(_BREAK_REVERSED if reverse else _BREAK_FORWARD)
            continue
        encodings = [encoding[::-1] if reverse else encoding for encoding in token]
        if len(encodings) == 1:
            parts.append(re.escape(encodings[0]))
        else:
            parts.append(b"(?:" + b"|".join(re.escape(encoding) for encoding in encodings) + b")")
    return re.compile(b"".join(parts))


class Matcher:
    """Find a literal needle in a window of bytes; `find` and `rfind` take the owned range inside the window.

    A token is the tuple of encodings of one needle character; the line break is the empty tuple.
    """

    def __init__(
        self,
        tokens: list[tuple[bytes, ...]],
        *,
        case_sensitive: bool,
        ascii_fold: bytes | None,
        plain: bytes | None,
        forced: str,
    ) -> None:
        self._tokens = tokens
        self._case_sensitive = case_sensitive
        self._ascii_fold = ascii_fold
        self._plain = plain
        self._forced = forced
        self._has_break = any(not token for token in tokens)
        self._max_length = sum(max((len(e) for e in token), default=_BREAK_LENGTH) for token in tokens)
        self._first_escaped = _is_escaped_token(tokens[0])
        self._last_escaped = _is_escaped_token(tokens[-1])
        escaped = [_is_escaped_token(token) for token in tokens]
        self._escape_run = any(a and b for a, b in pairwise(escaped))
        self._forward_pattern: re.Pattern[bytes] | None = None
        self._reverse_pattern: re.Pattern[bytes] | None = None
        if self._needs_pattern_eagerly():
            self._forward_pattern = _pattern(tokens, reverse=False)
            self._reverse_pattern = _pattern(tokens, reverse=True)

    @property
    def max_length(self) -> int:
        """Bytes of the longest possible match (the sum over the tokens of the longest encoding)."""
        return self._max_length

    def find(self, window: bytes, lo: int, hi: int) -> tuple[int, int] | None:
        """Return the leftmost match `(start, end)` whose start lies in `[lo, hi)`; the match may extend past `hi`.

        `window` carries the context bytes around the owned range.
        """
        if lo >= hi:
            return None
        plain = self._plain_search(window)
        if plain is not None:
            hay, needle = plain
            pos = lo
            while pos < hi:
                start = hay.find(needle, pos, hi + len(needle) - 1)
                if start < 0:
                    return None
                end = start + len(needle)
                if self._edges_ok(window, start, end):
                    return start, end
                pos = start + 1
            return None
        if self._cannot_match(window):
            return None
        pattern = self._compiled(reverse=False)
        pos = lo
        while pos < hi:
            found = pattern.search(window, pos)
            if found is None or found.start() >= hi:
                return None
            if self._edges_ok(window, found.start(), found.end()):
                return found.start(), found.end()
            pos = found.start() + 1
        return None

    def rfind(self, window: bytes, lo: int, hi: int) -> tuple[int, int] | None:
        """Return the match with the greatest end among those whose end lies in `(lo, hi]`; it may start before `lo`."""
        if lo >= hi:
            return None
        plain = self._plain_search(window)
        if plain is not None:
            hay, needle = plain
            limit = hi
            while limit > lo:
                start = hay.rfind(needle, max(0, lo - len(needle) + 1), limit)
                if start < 0:
                    return None
                end = start + len(needle)
                if self._edges_ok(window, start, end):
                    return start, end
                limit = end - 1
            return None
        if self._cannot_match(window):
            return None
        pattern = self._compiled(reverse=True)
        reverse = window[::-1]
        size = len(window)
        pos = size - hi
        while True:
            found = pattern.search(reverse, pos)
            if found is None:
                return None
            end = size - found.start()
            start = size - found.end()
            if end <= lo:
                return None
            if self._edges_ok(window, start, end):
                return start, end
            pos = found.start() + 1

    def _needs_pattern_eagerly(self) -> bool:
        """True when no plain search can serve any window, so the pattern is compiled up front."""
        return (self._plain is None and self._ascii_fold is None) or self._forced == "pattern"

    def _plain_search(self, window: bytes) -> tuple[bytes, bytes] | None:
        """Return `(haystack, needle)` for the plain tiers that apply to `window`, or `None` for the pattern tier."""
        if self._forced == "pattern":
            return None
        if self._plain is not None:
            return window, self._plain
        if self._ascii_fold is not None and window.isascii():
            return window.lower(), self._ascii_fold
        return None

    def _cannot_match(self, window: bytes) -> bool:
        """True when a case-insensitive needle with a non-ASCII class cannot match a pure ASCII window."""
        return not self._case_sensitive and self._ascii_fold is None and not self._has_break and window.isascii()

    def _compiled(self, *, reverse: bool) -> re.Pattern[bytes]:
        if self._forward_pattern is None or self._reverse_pattern is None:
            self._forward_pattern = _pattern(self._tokens, reverse=False)
            self._reverse_pattern = _pattern(self._tokens, reverse=True)
        return self._reverse_pattern if reverse else self._forward_pattern

    def _edges_ok(self, window: bytes, start: int, end: int) -> bool:
        """False when the match is not made of whole characters of the decoding, one per needle token.

        Only escaped bytes can break that: at an edge (inside a valid multi-byte character) or in a run of two or more
        (the bytes of a valid character, which would match a character instead of invalid bytes).
        """
        if self._first_escaped and _inside_character(window, start):
            return False
        if self._last_escaped and _inside_character(window, end):
            return False
        if self._escape_run:
            text = window[start:end].decode("utf-8", "surrogateescape")
            return len(text) - text.count("\r\n") == len(self._tokens)
        return True


def _tokenize(needle: str, *, case_sensitive: bool) -> list[tuple[bytes, ...]]:
    tokens: list[tuple[bytes, ...]] = []
    index = 0
    while index < len(needle):
        char = needle[index]
        if char == "\r" and needle[index + 1 : index + 2] == "\n":
            tokens.append(())
            index += 2
            continue
        index += 1
        if char in "\r\n":
            tokens.append(())
        elif case_sensitive or ord(char) in _ESCAPED:
            tokens.append((char.encode("utf-8", "surrogateescape"),))
        else:
            tokens.append(tuple(member.encode("utf-8") for member in variants(char)))
    return tokens


def compile_matcher(needle: str, *, case_sensitive: bool, tier: str = "auto") -> Matcher:
    """Compile `needle` into a `Matcher`.

    `tier` is a test switch: `"auto"` picks per window, `"find"`, `"ascii"` and `"pattern"` force a tier; a forced tier
    that cannot apply to the needle raises `SearchError`.

    Raises:
        SearchError: the needle is empty, has a lone surrogate outside U+DC80 to U+DCFF, is too long, or `tier` is
            unknown or cannot apply.
    """
    if tier not in _TIERS:
        raise SearchError(f"unknown tier {tier!r}")
    if not needle:
        raise SearchError("empty needle")
    try:
        encoded = needle.encode("utf-8", "surrogateescape")
    except UnicodeEncodeError as error:
        raise SearchError("the needle contains a character that cannot be encoded") from error
    if len(encoded) > MAX_NEEDLE_BYTES:
        raise SearchError("the needle is too long")
    tokens = _tokenize(needle, case_sensitive=case_sensitive)
    has_break = any(not token for token in tokens)
    plain = encoded if case_sensitive and not has_break else None
    ascii_fold: bytes | None = None
    if not case_sensitive and not has_break:
        folded = "".join(fold1(char) for char in needle)
        if folded.isascii():
            ascii_fold = folded.encode()
    if (plain is None or tier == "pattern") and len(tokens) > MAX_PATTERN_CHARS:
        raise SearchError("the needle is too long for a case-insensitive or line break search")
    if tier == "find" and plain is None:
        raise SearchError("the find tier needs a case-sensitive needle without a line break")
    if tier == "ascii" and ascii_fold is None:
        raise SearchError("the ascii tier needs a case-insensitive needle whose folded form is ASCII")
    return Matcher(tokens, case_sensitive=case_sensitive, ascii_fold=ascii_fold, plain=plain, forced=tier)
