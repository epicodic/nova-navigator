"""Static checks of the project documents."""

import re
from pathlib import Path

import pytest
from markdown_it import MarkdownIt
from markdown_it.token import Token

_ROOT = Path(__file__).parent.parent


def _read(name: str) -> str:
    return (_ROOT / name).read_text(encoding="utf-8")


@pytest.mark.parametrize("name", ["AGENTS.md", "src/CLAUDE.md"])
def test_the_old_default_directory_is_not_mentioned(name: str) -> None:
    assert "config/default" not in _read(name)


def test_development_setup_has_its_own_page() -> None:
    text = _read("docs/development.md")
    for command in ("uv run nn", "uv run pytest", "uv run qa", "uv run install_check"):
        assert command in text


_TARGET_ATTRIBUTES = {"link_open": "href", "image": "src"}


def _tokens(name: str) -> list[Token]:
    tokens = MarkdownIt("commonmark").enable("table").parse(_read(name))
    return [item for token in tokens for item in (token, *(token.children or []))]


def _targets(name: str) -> list[str]:
    return [str(token.attrGet(_TARGET_ATTRIBUTES[token.type])) for token in _tokens(name) if token.type in _TARGET_ATTRIBUTES]


def test_changelog_has_one_release_section_with_known_issues() -> None:
    text = _read("CHANGELOG.md")
    assert re.findall(r"^## .*$", text, flags=re.MULTILINE) == ["## 0.1.0"]
    assert "### Known issues" in text


def test_changelog_links_are_absolute() -> None:
    assert [target for target in _targets("CHANGELOG.md") if not target.startswith("https://")] == []
