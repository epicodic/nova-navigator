"""Deprecated: use nova_widgets.keymap.key_sequence instead."""

from nova_widgets.keymap.key_sequence import KeyFormatStyle as KeyDisplayStyle
from nova_widgets.keymap.key_sequence import KeySequence


def format_key(key: str, style: KeyDisplayStyle) -> str:
    """Deprecated: use KeySequence.parse(key).format(style) instead."""
    return KeySequence.parse(key).format(style)
