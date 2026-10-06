"""Maps a file path to the tree-sitter language used for syntax highlighting."""

from __future__ import annotations

from pathlib import Path

_SUFFIX_LANGUAGES: dict[str, str] = {
    ".py": "python",
    ".pyi": "python",
    ".json": "json",
    ".md": "markdown",
    ".markdown": "markdown",
    ".yaml": "yaml",
    ".yml": "yaml",
    ".toml": "toml",
    ".html": "html",
    ".htm": "html",
    ".css": "css",
    ".js": "javascript",
    ".mjs": "javascript",
    ".cjs": "javascript",
    ".xml": "xml",
    ".sql": "sql",
    ".sh": "bash",
    ".bash": "bash",
    ".rs": "rust",
    ".go": "go",
    ".java": "java",
}

_NAME_LANGUAGES: dict[str, str] = {
    ".bashrc": "bash",
    ".bash_profile": "bash",
    ".bash_aliases": "bash",
}


def language_for_path(path: Path | None) -> str | None:
    """Return the highlight language for `path`, or `None` when it is unknown."""
    if path is None:
        return None
    name = path.name.lower()
    return _NAME_LANGUAGES.get(name) or _SUFFIX_LANGUAGES.get(Path(name).suffix)
