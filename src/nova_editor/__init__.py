"""Nova Editor - Large-file text editor widget and application.

This package provides a text editor widget built with Textual, designed for
editing large files with minimal overhead. It includes a vendored copy of
Textual's TextArea widget (as NovaTextArea) which will be extended with
custom functionality for handling very large files and long lines.

Public exports (loaded lazily on first access):
- NovaTextArea: The main editor widget (`NovaTextArea.open` opens a file lazily and read-only).
- LazyConfig: Thresholds of the lazily opened document.
- DEFAULT_HIGHLIGHT_LIMIT: Default size limit for highlighting a lazily opened file.
"""

from __future__ import annotations

import importlib
from typing import TYPE_CHECKING, Any

if TYPE_CHECKING:
    from nova_editor.document._lazy_config import LazyConfig
    from nova_editor.widget._text_area import DEFAULT_HIGHLIGHT_LIMIT, NovaTextArea

__all__ = ["DEFAULT_HIGHLIGHT_LIMIT", "LazyConfig", "NovaTextArea"]

_EXPORTS = {
    "LazyConfig": "nova_editor.document._lazy_config",
    "NovaTextArea": "nova_editor.widget._text_area",
    "DEFAULT_HIGHLIGHT_LIMIT": "nova_editor.widget._text_area",
}


def __getattr__(name: str) -> Any:
    """Import a public name on first access (PEP 562), so that `import nova_editor.core` does not load Textual."""
    module = _EXPORTS.get(name)
    if module is None:
        message = f"module {__name__!r} has no attribute {name!r}"
        raise AttributeError(message)
    value = getattr(importlib.import_module(module), name)
    globals()[name] = value
    return value


def __dir__() -> list[str]:
    """List the lazily exported names beside the loaded ones."""
    return sorted({*globals(), *__all__})
