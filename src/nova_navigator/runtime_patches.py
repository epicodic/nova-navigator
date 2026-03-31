from __future__ import annotations

from collections.abc import Callable, Iterable
from typing import Any, cast

from textual import _xterm_parser, events

_ESC = "\x1b"
_ESC_PREFIXED_SINGLE_KEY_SEQUENCE_LENGTH = 2
_SPECIAL_ALT_KEYS: dict[str, str] = {
    "\r": "enter",
    "\t": "tab",
    " ": "space",
}


def apply_runtime_patches() -> None:
    """Apply process-wide runtime patches required before app startup."""
    _patch_xterm_parser_key_overrides()


# ----------------------------- XTermParser Alt+<Key> Patch -----------------------------

type _SequenceToKeyEvents = Callable[[_xterm_parser.XTermParser, str, bool], Iterable[events.Key]]

_ORIGINAL_SEQUENCE_TO_KEY_EVENTS: _SequenceToKeyEvents = _xterm_parser.XTermParser._sequence_to_key_events


def _patch_xterm_parser_key_overrides() -> None:
    """Teach Textual's XTerm parser custom key overrides for known escape sequences."""
    if _xterm_parser.XTermParser._sequence_to_key_events is _patched_sequence_to_key_events:
        return

    _xterm_parser.XTermParser._sequence_to_key_events = cast("Any", _patched_sequence_to_key_events)


def _patched_sequence_to_key_events(
    self: _xterm_parser.XTermParser,
    sequence: str,
    alt: bool = False,
) -> Iterable[events.Key]:
    # Some terminals send Alt+Enter in CSI-u form.
    if sequence == "\x1b[13;3u":
        yield events.Key("alt+enter", None)
        return

    key_name = _parse_alt_prefixed_sequence(sequence)
    if key_name is not None:
        yield events.Key(key_name, None)
        return

    yield from _ORIGINAL_SEQUENCE_TO_KEY_EVENTS(self, sequence, alt)


def _parse_alt_prefixed_sequence(sequence: str) -> str | None:
    """Return Textual key name for simple ``ESC + key`` sequences, else ``None``.

    We only remap two-byte sequences so CSI/SS3 escapes (arrows, function keys)
    continue through Textual's normal parser unchanged.
    """
    if not (len(sequence) == _ESC_PREFIXED_SINGLE_KEY_SEQUENCE_LENGTH and sequence[0] == _ESC):
        return None

    raw = sequence[1]
    key = _SPECIAL_ALT_KEYS.get(raw)
    if key is None and raw.islower():
        key = raw
    if key is None:
        return None
    return f"alt+{key}"
