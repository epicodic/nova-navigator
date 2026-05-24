"""Key combination display formatting."""

from __future__ import annotations

from enum import StrEnum


class KeyDisplayStyle(StrEnum):
    """How key combinations are rendered in the UI."""

    CLASSIC = "classic"  # Ctrl+V
    EMACS = "emacs"  # C-v
    CARET = "caret"  # ^V


_MODIFIER_CLASSIC: dict[str, str] = {
    "ctrl": "Ctrl",
    "alt": "Alt",
    "shift": "Shift",
    "meta": "Meta",
}

_MODIFIER_EMACS: dict[str, str] = {
    "ctrl": "C",
    "alt": "M",
    "shift": "S",
    "meta": "s",
}


def format_key(key: str, style: KeyDisplayStyle) -> str:
    """Format a Textual key string for display.

    Args:
        key: Textual key notation, e.g. "ctrl+v" or "ctrl+x ctrl+s".
        style: Display style to apply.

    Returns:
        Human-readable key string.
    """
    chords = key.strip().split(" ")
    return " ".join(_format_chord(chord, style) for chord in chords)


def _format_chord(chord: str, style: KeyDisplayStyle) -> str:
    parts = chord.split("+")
    key_char = parts[-1]
    modifiers = [p.lower() for p in parts[:-1]]

    if style == KeyDisplayStyle.CLASSIC:
        return _format_classic(key_char, modifiers)
    if style == KeyDisplayStyle.EMACS:
        return _format_emacs(key_char, modifiers)
    return _format_caret(key_char, modifiers)


def _format_classic(key_char: str, modifiers: list[str]) -> str:
    mod_parts = [_MODIFIER_CLASSIC.get(m, m.capitalize()) for m in modifiers]
    display_key = key_char.upper() if len(key_char) == 1 else key_char.capitalize()
    if mod_parts:
        return "+".join(mod_parts) + "+" + display_key
    return display_key


def _format_emacs(key_char: str, modifiers: list[str]) -> str:
    if not modifiers:
        return key_char
    prefix_parts = [_MODIFIER_EMACS.get(m, m) for m in modifiers]
    return "-".join(prefix_parts) + "-" + key_char


def _format_caret(key_char: str, modifiers: list[str]) -> str:
    if "ctrl" in modifiers:
        remaining = [m for m in modifiers if m != "ctrl"]
        prefix = "".join(_MODIFIER_CLASSIC.get(m, m.capitalize()) + "+" for m in remaining)
        return f"^{prefix}{key_char.upper()}"
    if modifiers:
        # Fall back to classic for non-ctrl modifiers
        mod_parts = [_MODIFIER_CLASSIC.get(m, m.capitalize()) for m in modifiers]
        display_key = key_char.upper() if len(key_char) == 1 else key_char.capitalize()
        return "+".join(mod_parts) + "+" + display_key
    # Function keys and bare keys
    if key_char.startswith("f") and key_char[1:].isdigit():
        return key_char.upper()
    return key_char.capitalize() if len(key_char) > 1 else key_char
