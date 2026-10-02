"""Hand-written cases that pin the semantics of the brute-force search oracle."""

from __future__ import annotations

from tests.nova_editor.core.search_reference import (
    all_matches,
    boundaries,
    haystack_tokens,
    ref_fold,
    reference_search,
    split_tokens,
)

KELVIN = chr(0x212A)
SHARP_S = chr(0xDF)
CAPITAL_SHARP_S = chr(0x1E9E)
E_ACUTE = chr(0xE9)
DOTTED_I = chr(0x130)
FINAL_SIGMA = chr(0x3C2)
SIGMA = chr(0x3C3)
EURO = chr(0x20AC)
ESCAPED_A9 = "\udca9"


def test_crlf_is_one_break() -> None:
    assert all_matches(b"a\r\nb", "\n", case_sensitive=True) == [(1, 3)]
    assert all_matches(b"a\r\nb", "\r\n", case_sensitive=True) == [(1, 3)]
    assert all_matches(b"a\nb", "\r\n", case_sensitive=True) == [(1, 2)]
    assert all_matches(b"a\rb", "\n", case_sensitive=True) == [(1, 2)]


def test_break_matches_only_a_break() -> None:
    assert all_matches(b"a\nb", "a\nb", case_sensitive=True) == [(0, 3)]
    assert all_matches(b"a\nb", "a b", case_sensitive=True) == []
    assert all_matches(b"a b", "a\nb", case_sensitive=True) == []
    assert all_matches(b"a\nb", "a\nb", case_sensitive=False) == [(0, 3)]
    assert all_matches(b"a b", "a\nb", case_sensitive=False) == []
    assert all_matches(b"\x0b\x0c", "\n", case_sensitive=True) == []


def test_crlf_in_document_is_not_two_breaks() -> None:
    assert all_matches(b"a\r\n\r\nb", "\n\n", case_sensitive=True) == [(1, 5)]
    assert all_matches(b"a\r\nb", "\r\r", case_sensitive=True) == []
    assert all_matches(b"a\n\rb", "\n\r", case_sensitive=True) == [(1, 3)]


def test_escaped_byte_does_not_match_inside_a_character() -> None:
    assert all_matches(E_ACUTE.encode(), ESCAPED_A9, case_sensitive=True) == []
    assert all_matches(b"x\xa9", ESCAPED_A9, case_sensitive=True) == [(1, 2)]
    assert all_matches(b"x\xa9", ESCAPED_A9, case_sensitive=False) == [(1, 2)]


def test_valid_character_does_not_match_its_escaped_bytes() -> None:
    assert all_matches(b"\xc3\xa9", E_ACUTE, case_sensitive=True) == [(0, 2)]
    assert all_matches(b"\xc3", E_ACUTE, case_sensitive=True) == []


def test_invalid_sequences_are_one_unit_per_byte() -> None:
    assert [(t, a, b) for t, a, b in haystack_tokens(b"\xf0\x9f\x98")] == [
        ("\udcf0", 0, 1),
        ("\udc9f", 1, 2),
        ("\udc98", 2, 3),
    ]
    assert all_matches(b"\xf0\x9f\x98", "\udcf0\udc9f", case_sensitive=True) == [(0, 2)]
    assert all_matches("\U0001f600".encode(), "\udcf0", case_sensitive=True) == []


def test_multibyte_spans_are_byte_offsets() -> None:
    data = ("a" + E_ACUTE + EURO + "\U0001f600b").encode()
    assert [(t, a, b) for t, a, b in haystack_tokens(data)][1:] == [
        (E_ACUTE, 1, 3),
        (EURO, 3, 6),
        ("\U0001f600", 6, 10),
        ("b", 10, 11),
    ]
    assert all_matches(data, EURO + "\U0001f600", case_sensitive=True) == [(3, 10)]
    assert boundaries(data) == [0, 1, 3, 6, 10, 11]


def test_case_sensitive_is_exact() -> None:
    assert all_matches(b"aA", "a", case_sensitive=True) == [(0, 1)]
    assert all_matches(KELVIN.encode(), "k", case_sensitive=True) == []
    assert all_matches(b"K", KELVIN, case_sensitive=True) == []


def test_case_insensitive_uses_simple_fold() -> None:
    assert all_matches(b"K", "k", case_sensitive=False) == [(0, 1)]
    assert all_matches(KELVIN.encode(), "k", case_sensitive=False) == [(0, 3)]
    assert all_matches(b"k", KELVIN, case_sensitive=False) == [(0, 1)]
    assert all_matches(b"K", KELVIN, case_sensitive=False) == [(0, 1)]
    assert all_matches(SHARP_S.encode(), "ss", case_sensitive=False) == []
    assert all_matches(b"ss", SHARP_S, case_sensitive=False) == []
    assert all_matches(b"aBc", "AbC", case_sensitive=False) == [(0, 3)]


def test_ref_fold_rules() -> None:
    assert ref_fold("A") == "a"
    assert ref_fold(KELVIN) == "k"
    assert ref_fold(FINAL_SIGMA) == SIGMA
    assert ref_fold(SHARP_S) == SHARP_S  # casefold is "ss": two characters, lower is one, same character
    assert ref_fold(CAPITAL_SHARP_S) == SHARP_S  # casefold is "ss", so the single-character lower() is used
    assert ref_fold(DOTTED_I) == DOTTED_I  # casefold and lower are both two characters: unchanged
    assert ref_fold("\udca9") == "\udca9"
    assert ref_fold("1") == "1"


def test_fold_falls_back_to_lower_when_casefold_is_longer() -> None:
    assert all_matches(CAPITAL_SHARP_S.encode(), SHARP_S, case_sensitive=False) == [(0, 3)]
    assert all_matches(SHARP_S.encode(), CAPITAL_SHARP_S, case_sensitive=False) == [(0, 2)]
    assert all_matches(DOTTED_I.encode(), "i", case_sensitive=False) == []
    assert all_matches(DOTTED_I.encode(), DOTTED_I, case_sensitive=False) == [(0, 2)]
    assert all_matches(FINAL_SIGMA.encode(), SIGMA, case_sensitive=False) == [(0, 2)]


def test_empty_needle_has_no_matches() -> None:
    assert all_matches(b"abc", "", case_sensitive=True) == []
    assert all_matches(b"", "", case_sensitive=False) == []
    assert all_matches(b"", "a", case_sensitive=True) == []


def test_forward_wrap_and_backward_wrap() -> None:
    data = b"ab ab ab"
    assert reference_search(data, "ab", case_sensitive=True, backward=False, wrap=True, origin=3) == (3, 5, False)
    assert reference_search(data, "ab", case_sensitive=True, backward=False, wrap=True, origin=7) == (0, 2, True)
    assert reference_search(data, "ab", case_sensitive=True, backward=False, wrap=False, origin=7) is None
    assert reference_search(data, "ab", case_sensitive=True, backward=True, wrap=True, origin=8) == (6, 8, False)
    assert reference_search(data, "ab", case_sensitive=True, backward=True, wrap=True, origin=1) == (6, 8, True)
    assert reference_search(data, "ab", case_sensitive=True, backward=True, wrap=False, origin=1) is None


def test_forward_start_is_inclusive_and_backward_end_is_inclusive() -> None:
    data = b"ab ab"
    assert reference_search(data, "ab", case_sensitive=True, backward=False, wrap=False, origin=0) == (0, 2, False)
    assert reference_search(data, "ab", case_sensitive=True, backward=False, wrap=False, origin=1) == (3, 5, False)
    assert reference_search(data, "ab", case_sensitive=True, backward=True, wrap=False, origin=2) == (0, 2, False)
    assert reference_search(data, "ab", case_sensitive=True, backward=True, wrap=False, origin=4) == (0, 2, False)
    assert reference_search(data, "ab", case_sensitive=True, backward=True, wrap=False, origin=5) == (3, 5, False)


def test_wrap_can_find_the_match_at_the_origin_side() -> None:
    data = b"ab"
    assert reference_search(data, "ab", case_sensitive=True, backward=False, wrap=True, origin=1) == (0, 2, True)
    assert reference_search(data, "ab", case_sensitive=True, backward=True, wrap=True, origin=1) == (0, 2, True)
    assert reference_search(data, "zz", case_sensitive=True, backward=False, wrap=True, origin=1) is None


def test_overlapping_matches_are_all_listed() -> None:
    assert all_matches(b"aaaa", "aa", case_sensitive=True) == [(0, 2), (1, 3), (2, 4)]
    assert [t[0] for t in haystack_tokens(b"a\r\n")] == ["a", None]


def test_backward_prefers_the_greatest_end_among_overlaps() -> None:
    data = b"aaaa"
    assert reference_search(data, "aa", case_sensitive=True, backward=True, wrap=False, origin=3) == (1, 3, False)
    assert reference_search(data, "aa", case_sensitive=True, backward=True, wrap=False, origin=2) == (0, 2, False)
    assert reference_search(data, "aa", case_sensitive=True, backward=True, wrap=True, origin=1) == (2, 4, True)


def test_split_tokens_and_boundaries() -> None:
    assert split_tokens("a\r\n\rb\n") == ["a", None, None, "b", None]
    assert boundaries(b"a\r\nb") == [0, 1, 3, 4]
    assert boundaries(b"") == [0]
    assert haystack_tokens(b"") == []
