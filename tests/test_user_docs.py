"""Static checks of the project documents."""

import re
import tomllib
from pathlib import Path

import pytest
from markdown_it import MarkdownIt
from markdown_it.token import Token

_ROOT = Path(__file__).parent.parent


def _read(name: str) -> str:
    return (_ROOT / name).read_text(encoding="utf-8")


@pytest.mark.parametrize("name", ["AGENTS.md", "src/CLAUDE.md", "README.md"])
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


_REPOSITORY_FILES = "https://github.com/epicodic/nova-navigator/blob/main/"


def test_the_readme_is_the_package_description() -> None:
    assert tomllib.loads(_read("pyproject.toml"))["project"]["readme"] == "README.md"


def test_readme_links_and_images_are_absolute() -> None:
    assert [target for target in _targets("README.md") if not target.startswith("https://")] == []


def test_readme_has_no_raw_html() -> None:
    assert [token.content for token in _tokens("README.md") if token.type in ("html_block", "html_inline")] == []


def test_readme_links_to_repository_files_that_exist() -> None:
    paths = [target.removeprefix(_REPOSITORY_FILES).split("#")[0] for target in _targets("README.md") if target.startswith(_REPOSITORY_FILES)]
    assert paths
    assert [path for path in paths if not (_ROOT / path).is_file()] == []


@pytest.mark.parametrize(
    "text",
    [
        "uv tool install nova-navigator",
        "pipx install nova-navigator",
        "uv tool install 'nova-navigator[azure]'",
        "pipx install 'nova-navigator[azure]'",
        "Ubuntu 24.04",
        "26.04",
        "3.12",
        "3.13",
        "3.14",
        "zsh",
        "`F1`",
        "`F3`",
        "docs/development.md",
        "CHANGELOG.md",
    ],
)
def test_readme_names(text: str) -> None:
    assert text in _read("README.md")


def test_readme_needs_no_source_checkout() -> None:
    text = _read("README.md")
    assert "uv sync" not in text
    assert "uv run" not in text


def test_readme_does_not_require_zsh() -> None:
    text = _read("README.md")
    assert "/usr/bin/zsh" not in text
    assert "apt install zsh" not in text
    assert "$SHELL" in text


def test_changelog_drops_the_zsh_requirement() -> None:
    assert "needs zsh" not in _read("CHANGELOG.md")
