"""Text editor widget (Textual).

This subpackage contains the Textual widget implementation for the editor.
It is a vendored copy of Textual's TextArea (renamed to NovaTextArea), which
will be extended with custom functionality while remaining a drop-in replacement.
"""

from nova_editor.widget._text_area import (
    DEFAULT_HIGHLIGHT_LIMIT,
    ExternalCheck,
    LanguageDoesNotExist,
    NovaTextArea,
    TextAreaLanguage,
    ThemeDoesNotExist,
)
from nova_editor.widget.hex_view import HexView

__all__ = [
    "DEFAULT_HIGHLIGHT_LIMIT",
    "ExternalCheck",
    "HexView",
    "LanguageDoesNotExist",
    "NovaTextArea",
    "TextAreaLanguage",
    "ThemeDoesNotExist",
]
