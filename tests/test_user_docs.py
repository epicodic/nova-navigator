"""Static checks of the project documents."""

from pathlib import Path

import pytest

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
