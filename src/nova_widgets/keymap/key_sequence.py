"""Emacs-style key representation: Key, KeyChord, KeySequence, and KeyFormatStyle.

Terminology used throughout this module:
- A *key chord* is one or more keys pressed simultaneously, e.g. Ctrl+X or F5.
- A *key sequence* is an ordered series of key chords pressed one after another,
  e.g. Ctrl+X followed by Ctrl+S.

Note: Core types are defined in nova_widgets.key_types to avoid circular imports.
This module re-exports them for backward compatibility.
"""

from __future__ import annotations

from nova_widgets.key_types import Key, KeyChord, KeyFormatStyle, KeySequence

__all__ = [
    "Key",
    "KeyChord",
    "KeyFormatStyle",
    "KeySequence",
]
