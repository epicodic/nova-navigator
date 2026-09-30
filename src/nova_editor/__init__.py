"""Nova Editor - Large-file text editor widget and application.

This package provides a text editor widget built with Textual, designed for
editing large files with minimal overhead. It includes a vendored copy of
Textual's TextArea widget (as NovaTextArea) which will be extended with
custom functionality for handling very large files and long lines.

Public exports:
- NovaTextArea: The main editor widget (`NovaTextArea.open` opens a file lazily and read-only).
- LazyConfig: Thresholds of the lazily opened document.
- DEFAULT_HIGHLIGHT_LIMIT: Default size limit for highlighting a lazily opened file.
"""

from nova_editor.document._lazy_config import LazyConfig
from nova_editor.widget._text_area import DEFAULT_HIGHLIGHT_LIMIT, NovaTextArea

__all__ = ["DEFAULT_HIGHLIGHT_LIMIT", "LazyConfig", "NovaTextArea"]
