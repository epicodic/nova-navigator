"""Core key representation types: Key, KeyChord, KeySequence, KeyFormatStyle.

This module is separate to avoid circular imports between menu._action and keymap.key_sequence.
"""

from __future__ import annotations

from dataclasses import dataclass
from enum import StrEnum

# ---------------------------------------------------------------------------
# Display style
# ---------------------------------------------------------------------------

_MODIFIER_ORDER: dict[str, int] = {"ctrl": 0, "alt": 1, "shift": 2, "meta": 3}
_MODIFIERS: frozenset[str] = frozenset(_MODIFIER_ORDER)

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


class KeyFormatStyle(StrEnum):
    """How key combinations are rendered in the UI."""

    CLASSIC = "classic"  # Ctrl+V
    EMACS = "emacs"  # C-v
    CARET = "caret"  # ^V


# ---------------------------------------------------------------------------
# Key
# ---------------------------------------------------------------------------


@dataclass(frozen=True)
class Key:
    """A single physical key, e.g. Key("ctrl"), Key("x"), Key("f5")."""

    name: str

    @staticmethod
    def parse(s: str) -> Key:
        """Parse a key name string into a Key.

        Args:
            s: Raw key name string, e.g. "ctrl", "x", "f5".

        Returns:
            A Key with a normalised (lowercased, stripped) name.
        """
        return Key(s.lower().strip())

    @property
    def is_modifier(self) -> bool:
        """True if this key is a modifier (ctrl, alt, shift, or meta)."""
        return self.name in _MODIFIERS

    def format(self, style: KeyFormatStyle) -> str:
        """Format this key for display.

        Args:
            style: Display style to apply.

        Returns:
            Human-readable key string.
        """
        if self.is_modifier:
            if style == KeyFormatStyle.EMACS:
                return _MODIFIER_EMACS.get(self.name, self.name)
            return _MODIFIER_CLASSIC.get(self.name, self.name.capitalize())
        # base key
        if style == KeyFormatStyle.EMACS:
            return self.name
        if style == KeyFormatStyle.CARET:
            if self.name.startswith("f") and self.name[1:].isdigit():
                return self.name.upper()
            return self.name.capitalize() if len(self.name) > 1 else self.name
        # CLASSIC
        return self.name.upper() if len(self.name) == 1 else self.name.capitalize()


# ---------------------------------------------------------------------------
# KeyChord
# ---------------------------------------------------------------------------


@dataclass(frozen=True)
class KeyChord:
    """One or more keys pressed simultaneously, e.g. Ctrl+X or F5.

    Keys are stored in canonical order: modifiers (ctrl < alt < shift < meta)
    followed by the base key.  This guarantees that
    ``KeyChord.parse("x+ctrl") == KeyChord.parse("ctrl+x")``.
    """

    keys: tuple[Key, ...]

    @staticmethod
    def parse(s: str) -> KeyChord:
        """Parse a Textual key string into a KeyChord.

        Args:
            s: Textual key notation for a single chord, e.g. "ctrl+x" or "f5".

        Returns:
            A KeyChord with keys in canonical order.
        """
        raw = [Key(part.lower().strip()) for part in s.strip().split("+") if part]
        modifiers = sorted(
            [k for k in raw if k.is_modifier],
            key=lambda k: _MODIFIER_ORDER.get(k.name, 99),
        )
        base_keys = [k for k in raw if not k.is_modifier]
        return KeyChord(tuple(modifiers + base_keys))

    def format(self, style: KeyFormatStyle) -> str:
        """Format this chord for display.

        Args:
            style: Display style to apply.

        Returns:
            Human-readable chord string, e.g. "Ctrl+X", "C-x", or "^X".
        """
        modifiers = [k for k in self.keys if k.is_modifier]
        base_keys = [k for k in self.keys if not k.is_modifier]
        base = base_keys[0].name if base_keys else ""

        if style == KeyFormatStyle.CLASSIC:
            mod_parts = [_MODIFIER_CLASSIC.get(k.name, k.name.capitalize()) for k in modifiers]
            display_key = base.upper() if len(base) == 1 else base.capitalize()
            if mod_parts:
                return "+".join(mod_parts) + "+" + display_key
            return display_key

        if style == KeyFormatStyle.EMACS:
            if not modifiers:
                return base
            prefix_parts = [_MODIFIER_EMACS.get(k.name, k.name) for k in modifiers]
            return "-".join(prefix_parts) + "-" + base

        # CARET
        if any(k.name == "ctrl" for k in modifiers):
            remaining = [k for k in modifiers if k.name != "ctrl"]
            prefix = "".join(_MODIFIER_CLASSIC.get(k.name, k.name.capitalize()) + "+" for k in remaining)
            return f"^{prefix}{base.upper()}"
        if modifiers:
            mod_parts = [_MODIFIER_CLASSIC.get(k.name, k.name.capitalize()) for k in modifiers]
            display_key = base.upper() if len(base) == 1 else base.capitalize()
            return "+".join(mod_parts) + "+" + display_key
        # bare key, no modifiers
        if base.startswith("f") and base[1:].isdigit():
            return base.upper()
        return base.capitalize() if len(base) > 1 else base

    def __str__(self) -> str:
        r"""Return the canonical Textual-notation string, e.g. "ctrl+x"."""
        return "+".join(k.name for k in self.keys)


# ---------------------------------------------------------------------------
# KeySequence
# ---------------------------------------------------------------------------


@dataclass(frozen=True)
class KeySequence:
    """An ordered sequence of key chords, e.g. Ctrl+X followed by Ctrl+S."""

    chords: tuple[KeyChord, ...]

    @staticmethod
    def parse(s: str) -> KeySequence:
        """Parse a Textual key sequence string into a KeySequence.

        Args:
            s: Textual key notation, e.g. "ctrl+x ctrl+s" or "f5".
               Chords are space-separated.

        Returns:
            A KeySequence containing the parsed chords.
        """
        return KeySequence(tuple(KeyChord.parse(part) for part in s.strip().split(" ") if part))

    def format(self, style: KeyFormatStyle) -> str:
        """Format this key sequence for display.

        Args:
            style: Display style to apply.

        Returns:
            Human-readable sequence string, e.g. "Ctrl+X Ctrl+S".
        """
        return " ".join(chord.format(style) for chord in self.chords)

    def suffix_after(self, prefix: KeySequence) -> KeySequence:
        """Return the sub-sequence following the first occurrence of prefix.

        Used in chord-pending mode to display only the keys that still need
        to be pressed, given a prefix sequence that has already been entered.
        For example, given the sequence ``Ctrl+K Ctrl+A Ctrl+B`` and prefix
        ``Ctrl+K Ctrl+A``, this returns ``Ctrl+B``.

        Args:
            prefix: The key sequence to search for as a contiguous prefix match.

        Returns:
            A new KeySequence starting after the first matching occurrence of prefix.
            Returns self unchanged if prefix is not found or is empty.
        """
        n = len(prefix.chords)
        if n == 0:
            return self
        for i in range(len(self.chords) - n + 1):
            if self.chords[i : i + n] == prefix.chords:
                return KeySequence(self.chords[i + n :])
        return self

    def __str__(self) -> str:
        r"""Return the canonical Textual-notation string, e.g. "ctrl+x ctrl+s"."""
        return " ".join(str(chord) for chord in self.chords)
