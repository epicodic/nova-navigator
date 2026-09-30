"""Tests for display-width and UTF-8 helpers."""

from __future__ import annotations

import pytest
from hypothesis import given, settings
from hypothesis import strategies as st
from rich.cells import cached_cell_len, cell_len

from nova_editor.core import text_width
from nova_editor.core.text_width import (
    advance_disp,
    locate_cover,
    resync,
    safe_cut,
    utf8_len,
)

ALPHABET = ["a", "b", " ", "\t", "é", "漢", "😀", "é", "́", "\udc80", "\udcff"]
texts = st.lists(st.sampled_from(ALPHABET), max_size=40).map("".join)


def reference_disp(text: str, disp: int, tab: int) -> int:
    for char in text:
        disp += tab - disp % tab if char == "\t" else cell_len(char)
    return disp


@given(text=texts, disp=st.integers(0, 9), tab=st.sampled_from([1, 2, 4, 8]))
@settings(deadline=None)
def test_advance_disp_matches_per_char_reference(text: str, disp: int, tab: int) -> None:
    assert advance_disp(text, disp, tab) == reference_disp(text, disp, tab)


@given(text=texts, disp0=st.integers(0, 9), target=st.integers(0, 80), tab=st.sampled_from([1, 4, 8]))
@settings(deadline=None)
def test_locate_cover_agrees_with_advance_disp(text: str, disp0: int, target: int, tab: int) -> None:
    index, found = locate_cover(text, disp0, target, tab)
    if found:
        assert advance_disp(text[:index], disp0, tab) <= target < advance_disp(text[: index + 1], disp0, tab) or disp0 > target
    else:
        assert advance_disp(text, disp0, tab) <= target or disp0 > target


def test_lone_surrogate_is_one_cell() -> None:
    assert advance_disp("\udc80", 0) == 1


def test_resync_skips_up_to_three_continuation_bytes() -> None:
    assert resync(b"\x80\x80abc") == 2
    assert resync(b"\x80\x80\x80\x80abc") == 3
    assert resync(b"abc") == 0
    assert resync(b"") == 0


def test_safe_cut_holds_back_partial_sequences() -> None:
    euro = "€".encode()  # e2 82 ac
    assert safe_cut(b"ab" + euro) == 5
    assert safe_cut(b"ab" + euro[:1]) == 2
    assert safe_cut(b"ab" + euro[:2]) == 2
    assert safe_cut(b"ab") == 2
    assert safe_cut(b"") == 0
    assert safe_cut(b"ab\x80\x80\x80\x80") == 6  # invalid trailing continuation bytes are not held back


@given(text=texts)
def test_utf8_len_is_the_surrogateescape_length(text: str) -> None:
    assert utf8_len(text) == len(text.encode("utf-8", "surrogateescape"))


def test_advance_disp_does_not_retain_large_pieces_in_the_rich_cache() -> None:
    cached_cell_len.cache_clear()
    for seed in "abcdef":
        piece = "é" * 65_535 + seed
        assert advance_disp(piece, 0) == len(piece)
        assert advance_disp("\t" + piece, 0) == 4 + len(piece)
    assert cached_cell_len.cache_info().currsize <= 2


@given(text=st.lists(st.sampled_from([*ALPHABET, "\x00", "\x1f", "\x7f"]), min_size=600, max_size=1500).map("".join), disp=st.integers(0, 9))
@settings(deadline=None, max_examples=30)
def test_advance_disp_of_long_mixed_text_matches_the_reference(text: str, disp: int) -> None:
    assert advance_disp(text, disp, 4) == reference_disp(text, disp, 4)


def test_advance_disp_measures_only_the_non_ascii_part_of_a_long_piece(monkeypatch: pytest.MonkeyPatch) -> None:
    """The per-character width lookup must not run over the ASCII bulk of a 65,536-character piece (GIL hold on the scan thread)."""
    seen: list[int] = []
    real = text_width.cell_len

    def spy(text: str) -> int:
        seen.append(len(text))
        return real(text)

    monkeypatch.setattr(text_width, "cell_len", spy)
    piece = ("abcdefghi " * 9 + "é漢") * 700
    assert advance_disp(piece, 0) == reference_disp(piece, 0, 4)
    assert max(seen) <= len(piece) // 10


def reference_cover(text: str, disp0: int, target: int, tab: int) -> tuple[int, bool]:
    disp = disp0
    for index, char in enumerate(text):
        disp += tab - disp % tab if char == "\t" else cell_len(char)
        if disp > target:
            return index, True
    return len(text), False


@given(
    text=st.lists(st.sampled_from(ALPHABET), min_size=100, max_size=3000).map("".join),
    disp0=st.integers(0, 9),
    fraction=st.floats(0, 1.1),
    tab=st.sampled_from([1, 4, 8]),
)
@settings(deadline=None, max_examples=60)
def test_locate_cover_of_long_text_matches_the_per_character_reference(text: str, disp0: int, fraction: float, tab: int) -> None:
    target = int(reference_disp(text, disp0, tab) * fraction)
    index, found = locate_cover(text, disp0, target, tab)
    expected_index, expected_found = reference_cover(text, disp0, target, tab)
    assert (found, index if found else len(text)) == (expected_found, expected_index)


def test_locate_cover_looks_up_the_width_of_at_most_one_group_of_characters(monkeypatch: pytest.MonkeyPatch) -> None:
    """A hit deep inside a 1024-character chunk must not fall back to a per-character loop over the whole chunk (UI thread cost per rendered row)."""
    calls: list[int] = []
    real = text_width.cell_len

    def spy(text: str) -> int:
        calls.append(len(text))
        return real(text)

    monkeypatch.setattr(text_width, "cell_len", spy)
    text = "é" * 4000
    assert locate_cover(text, 0, 900) == (900, True)
    assert sum(1 for size in calls if size == 1) <= text_width._GROUP
