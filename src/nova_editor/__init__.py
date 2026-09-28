"""Nova Editor - Large-file text editor widget and application.

This package provides a text editor widget built with Textual, designed for
editing large files with minimal overhead. It includes a vendored copy of
Textual's TextArea widget (as NovaTextArea) which will be extended with
custom functionality for handling very large files and long lines.

Public exports:
- NovaTextArea: The main editor widget.
"""

from nova_editor.widget._text_area import NovaTextArea

__all__ = ["NovaTextArea"]
