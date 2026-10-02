"""Tree-sitter language loader, vendored from `textual/_tree_sitter.py` (Textual 8.2.8)."""

from __future__ import annotations

from importlib import import_module
from typing import TYPE_CHECKING

from textual import log

if TYPE_CHECKING:
    from tree_sitter import Language

try:
    _tree_sitter = import_module("tree_sitter")
except ImportError:
    _tree_sitter = None

TREE_SITTER = _tree_sitter is not None
"""Whether the `tree_sitter` package is installed."""

_LANGUAGE_CACHE: dict[str, Language] = {}


def get_language(language_name: str) -> Language | None:
    """Return the tree-sitter `Language` of `language_name`.

    Returns `None` when tree-sitter or that language module is not installed.
    """
    if _tree_sitter is None:
        return None
    if language_name in _LANGUAGE_CACHE:
        return _LANGUAGE_CACHE[language_name]
    try:
        module = import_module(f"tree_sitter_{language_name}")
    except ImportError:
        return None
    try:
        # xml is the one outlier of the `textual[syntax]` languages
        factory = module.language_xml if language_name == "xml" else module.language
        language = _tree_sitter.Language(factory())
    except (OSError, AttributeError):
        log.warning(f"Could not load language {language_name!r}.")
        return None
    _LANGUAGE_CACHE[language_name] = language
    return language
