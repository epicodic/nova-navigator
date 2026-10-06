from pathlib import Path

import pytest

from nova_editor.languages import language_for_path
from nova_editor.widget._tree_sitter import get_language


@pytest.mark.parametrize(
    ("name", "expected"),
    [("a.py", "python"), ("A.PY", "python"), ("x.yml", "yaml"), (".bashrc", "bash"), ("notes.txt", None), ("Makefile", None)],
)
def test_language_for_path(name: str, expected: str | None) -> None:
    assert language_for_path(Path(name)) == expected


def test_language_for_no_path() -> None:
    assert language_for_path(None) is None


def test_mapped_languages_are_installed() -> None:
    from nova_editor import languages

    for language in set(languages._SUFFIX_LANGUAGES.values()) | set(languages._NAME_LANGUAGES.values()):
        assert get_language(language) is not None, language
