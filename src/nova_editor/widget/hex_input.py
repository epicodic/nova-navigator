"""Pure input rules of the hex view (no Textual): the nibble state machine and the clipboard text forms."""

from __future__ import annotations

import re
from dataclasses import dataclass

from .hex_layout import char_text

__all__ = ["DigitPlan", "HexTextError", "char_text", "format_hex_pairs", "parse_hex_text", "plan_digit"]

_HEX_DIGITS = frozenset("0123456789abcdefABCDEF")
_TOKEN = re.compile(r"\S+")
_PREFIXES = ("0x", "0X")
_PREFIX_LENGTH = 2
_MAX_DIGIT = 15


@dataclass(frozen=True)
class DigitPlan:
    """The splice and the cursor that one typed hex digit produces."""

    start: int
    """First byte offset replaced."""
    end: int
    """End (exclusive) of the replaced range; equal to ``start`` for an insertion."""
    data: bytes
    """The bytes written in place of ``[start, end)``."""
    cursor_offset: int
    """Byte offset of the cursor after the edit."""
    cursor_nibble: int
    """Digit of the cursor after the edit: 0 (high) or 1 (low)."""
    completes: bool
    """Whether this low digit finishes a byte created by the previous high digit of the same run."""


def plan_digit(
    digit: int,
    offset: int,
    nibble: int,
    length: int,
    insert: bool,
    current: int | None,
    *,
    half: bool = False,
) -> DigitPlan:
    """Plan the edit for hex digit ``digit`` typed with the cursor on ``(offset, nibble)``.

    Args:
        digit: The typed digit value, 0 to 15.
        offset: Byte offset of the cursor.
        nibble: 0 when the cursor is on the high digit, 1 on the low digit.
        length: Document length in bytes.
        insert: Whether the view is in insert mode.
        current: The document byte at ``offset``; ``None`` only at the end position.
        half: Whether the byte at ``offset`` was created by the previous high digit and still waits for its
            low digit.

    Returns:
        The splice and the new cursor.

    Raises:
        ValueError: If ``digit`` is out of range or ``current`` is missing inside the document.
    """
    if not 0 <= digit <= _MAX_DIGIT:
        raise ValueError(f"hex digit out of range: {digit}")
    if offset >= length:
        return DigitPlan(length, length, bytes((digit << 4,)), length, 1, False)
    if current is None:
        raise ValueError("the current byte is required inside the document")
    if nibble == 0:
        if insert:
            return DigitPlan(offset, offset, bytes((digit << 4,)), offset, 1, False)
        return DigitPlan(offset, offset + 1, bytes((digit << 4 | current & 0x0F,)), offset, 1, False)
    return DigitPlan(offset, offset + 1, bytes((current & 0xF0 | digit,)), offset + 1, 0, half)


def format_hex_pairs(data: bytes) -> str:
    """Return ``data`` as upper-case hex pairs joined by single spaces (``DE AD BE EF``)."""
    return data.hex(" ").upper()


class HexTextError(ValueError):
    """Clipboard text that cannot be pasted into the hex pane; the message names the problem."""


def _shown(character: str) -> str:
    return character if character.isprintable() else f"U+{ord(character):04X}"


def parse_hex_text(text: str) -> bytes:
    """Parse clipboard text as hex digits.

    The text is split on whitespace; each token may start with ``0x`` or ``0X``; all remaining characters
    must be hex digits and their count must be even. Whitespace between digits is ignored, so ``d e`` is
    ``DE``.

    Raises:
        HexTextError: If the text is empty, holds a non-hex character, a ``0x`` without digits or an odd
            number of digits.
    """
    digits: list[str] = []
    for token in _TOKEN.finditer(text):
        body_start = token.start()
        if token.group().startswith(_PREFIXES):
            body_start += _PREFIX_LENGTH
            if body_start == token.end():
                raise HexTextError(f'Cannot paste into the hex pane: "0x" without digits at position {token.start() + 1}.')
        for position in range(body_start, token.end()):
            if text[position] not in _HEX_DIGITS:
                raise HexTextError(f'Cannot paste into the hex pane: "{_shown(text[position])}" at position {position + 1} of the clipboard text is not a hex digit.')
        digits.append(text[body_start : token.end()])
    if not digits:
        raise HexTextError("Nothing to paste: the clipboard is empty.")
    joined = "".join(digits)
    if len(joined) % 2:
        raise HexTextError(f"Cannot paste into the hex pane: the clipboard text holds an odd number of hex digits ({len(joined)}).")
    return bytes.fromhex(joined)
