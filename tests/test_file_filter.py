from typing import cast
from unittest.mock import MagicMock

from nova_navigator.file_filter import FilenamePatternFilter
from nova_navigator.vfs import VPath

# ── from_pattern_string ───────────────────────────────────────────────────────


def test_from_pattern_string_single() -> None:
    f = FilenamePatternFilter.from_pattern_string("*.txt")
    assert f.patterns == ["*.txt"]


def test_from_pattern_string_multi() -> None:
    f = FilenamePatternFilter.from_pattern_string("*.txt;*.md")
    assert f.patterns == ["*.txt", "*.md"]


def test_from_pattern_string_strips_whitespace() -> None:
    f = FilenamePatternFilter.from_pattern_string(" *.txt ; *.md ")
    assert f.patterns == ["*.txt", "*.md"]


def test_from_pattern_string_empty_becomes_wildcard() -> None:
    f = FilenamePatternFilter.from_pattern_string("")
    assert f.patterns == ["*"]


def test_from_pattern_string_whitespace_only_becomes_wildcard() -> None:
    f = FilenamePatternFilter.from_pattern_string("   ")
    assert f.patterns == ["*"]


def test_from_pattern_string_star() -> None:
    f = FilenamePatternFilter.from_pattern_string("*")
    assert f.patterns == ["*"]


# ── pattern_string round-trip ─────────────────────────────────────────────────


def test_pattern_string_single() -> None:
    f = FilenamePatternFilter(patterns=["*.txt"])
    assert f.pattern_string == "*.txt"


def test_pattern_string_multi() -> None:
    f = FilenamePatternFilter(patterns=["*.txt", "*.md"])
    assert f.pattern_string == "*.txt;*.md"


# ── matches ───────────────────────────────────────────────────────────────────


def _vpath(name: str) -> VPath:
    """Return a minimal VPath stand-in with the given name."""
    vp = MagicMock(spec=VPath)
    vp.name = name
    return cast("VPath", vp)


def test_matches_single_pattern_true() -> None:
    f = FilenamePatternFilter(patterns=["*.txt"])
    assert f.matches(_vpath("hello.txt")) is True


def test_matches_single_pattern_false() -> None:
    f = FilenamePatternFilter(patterns=["*.txt"])
    assert f.matches(_vpath("hello.py")) is False


def test_matches_multi_pattern_any_match() -> None:
    f = FilenamePatternFilter(patterns=["*.txt", "*.md"])
    assert f.matches(_vpath("readme.md")) is True


def test_matches_wildcard_matches_everything() -> None:
    f = FilenamePatternFilter(patterns=["*"])
    assert f.matches(_vpath("anything.xyz")) is True


def test_matches_case_sensitive() -> None:
    f = FilenamePatternFilter(patterns=["*.TXT"])
    assert f.matches(_vpath("hello.txt")) is False
    assert f.matches(_vpath("hello.TXT")) is True
