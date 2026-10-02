"""Unit tests for the literal matcher (ACT6 design 4)."""

from __future__ import annotations

from typing import cast

import pytest

from nova_editor.core.search import (
    CONTEXT_AFTER,
    CONTEXT_BEFORE,
    MAX_NEEDLE_BYTES,
    MAX_PATTERN_CHARS,
    SearchError,
    Tier,
    compile_matcher,
)

KELVIN = chr(0x212A)
LONG_S = chr(0x17F)
SHARP_S = chr(0xDF)
CAPITAL_SHARP_S = chr(0x1E9E)
E_ACUTE = chr(0xE9)
E_ACUTE_UPPER = chr(0xC9)
DOTTED_I = chr(0x130)
GRINNING = chr(0x1F600)


def forward(needle: str, data: bytes, *, case_sensitive: bool = True, tier: Tier = "auto") -> tuple[int, int] | None:
    return compile_matcher(needle, case_sensitive=case_sensitive, tier=tier).find(data, 0, len(data))


def backward(needle: str, data: bytes, *, case_sensitive: bool = True, tier: Tier = "auto") -> tuple[int, int] | None:
    return compile_matcher(needle, case_sensitive=case_sensitive, tier=tier).rfind(data, 0, len(data))


def test_plain_forward_and_backward() -> None:
    assert forward("ab", b"xabyab") == (1, 3)
    assert backward("ab", b"xabyab") == (4, 6)
    assert forward("zz", b"xabyab") is None
    assert backward("zz", b"xabyab") is None


def test_case_sensitive_does_not_fold() -> None:
    assert forward("k", b"K") is None
    assert forward("k", KELVIN.encode()) is None
    assert forward(E_ACUTE, E_ACUTE_UPPER.encode()) is None


def test_owned_range_forward_and_backward() -> None:
    matcher = compile_matcher("ab", case_sensitive=True)
    assert matcher.find(b"abxab", 1, 5) == (3, 5)  # owned starts [1, 5)
    assert matcher.find(b"abxab", 1, 3) is None
    assert matcher.find(b"abxab", 0, 1) == (0, 2)  # the match may extend past hi
    assert matcher.rfind(b"abxab", 0, 4) == (0, 2)  # owned ends (0, 4]
    assert matcher.rfind(b"abxab", 2, 5) == (3, 5)
    assert matcher.rfind(b"abxab", 0, 2) == (0, 2)  # the match may start before lo
    assert matcher.rfind(b"abxab", 2, 4) is None
    assert matcher.rfind(b"abxab", 1, 2) == (0, 2)  # the match may start before lo


def test_owned_range_is_empty_when_lo_reaches_hi() -> None:
    matcher = compile_matcher("ab", case_sensitive=False)
    assert matcher.find(b"abab", 2, 2) is None
    assert matcher.rfind(b"abab", 2, 2) is None


def test_case_insensitive_variants_and_kelvin() -> None:
    assert forward("k", b"xK", case_sensitive=False) == (1, 2)  # plain capital K
    assert forward("k", ("x" + KELVIN).encode(), case_sensitive=False) == (1, 4)  # Kelvin sign
    assert forward(KELVIN, b"xk", case_sensitive=False) == (1, 2)
    assert forward(KELVIN, b"xK", case_sensitive=False) == (1, 2)
    assert forward("K", b"xk", case_sensitive=False) == (1, 2)
    assert forward("s", ("x" + LONG_S).encode(), case_sensitive=False) == (1, 3)
    assert forward(E_ACUTE, E_ACUTE_UPPER.encode(), case_sensitive=False) == (0, 2)
    assert forward(E_ACUTE_UPPER, E_ACUTE.encode(), case_sensitive=False) == (0, 2)


def test_simple_folding_only() -> None:
    assert forward("ss", SHARP_S.encode(), case_sensitive=False) is None
    assert forward(SHARP_S, b"ss", case_sensitive=False) is None
    assert forward(SHARP_S, CAPITAL_SHARP_S.encode(), case_sensitive=False) == (0, 3)
    assert forward(DOTTED_I, b"i", case_sensitive=False) is None
    assert forward("i", DOTTED_I.encode(), case_sensitive=False) is None
    assert forward(DOTTED_I, DOTTED_I.encode(), case_sensitive=False) == (0, 2)


def test_case_insensitive_escaped_bytes_match_only_themselves() -> None:
    assert forward("\udca9", b"x\xa9", case_sensitive=False) == (1, 2)
    assert forward("\udca9", b"xa", case_sensitive=False) is None
    assert forward("\udcc3", E_ACUTE.encode(), case_sensitive=False) is None


def test_line_break_is_one_terminator() -> None:
    assert forward("\n", b"a\r\nb") == (1, 3)
    assert forward("\r\n", b"a\nb") == (1, 2)
    assert forward("\r", b"a\nb") == (1, 2)
    assert forward("\r", b"a\r\nb\rc") == (1, 3)  # a CR in the needle matches the whole CRLF first
    assert backward("\r", b"a\r\nb\rc") == (4, 5)
    assert forward("a\nb", b"a\r\nb") == (0, 4)
    assert backward("\n", b"a\r\nb\n") == (4, 5)
    assert backward("\r\n", b"a\r\nb\nc") == (4, 5)
    assert forward("\n\r", b"a\n\rb") == (1, 3)
    assert backward("\n\r", b"a\n\rb") == (1, 3)
    assert forward("\r", b"a\r") == (1, 2)
    assert forward("\n", b"a\n") == (1, 2)


def test_line_break_never_matches_half_of_a_crlf() -> None:
    for case_sensitive in (True, False):
        assert forward("\n\n", b"a\r\nb", case_sensitive=case_sensitive) is None
        assert forward("\r\r", b"a\r\nb", case_sensitive=case_sensitive) is None
        assert backward("\n\n", b"a\r\nb", case_sensitive=case_sensitive) is None
        assert forward("a\n\nb", b"a\r\nb", case_sensitive=case_sensitive) is None
        assert forward("\nb", b"a\r\nb", case_sensitive=case_sensitive) == (1, 4)
        assert forward("a\r", b"a\r\nb", case_sensitive=case_sensitive) == (0, 3)
        assert forward("\n", b"a\r\nb", case_sensitive=case_sensitive) == (1, 3)  # the whole CRLF, never the LF alone
        assert backward("\n", b"a\r\nb", case_sensitive=case_sensitive) == (1, 3)
    assert forward("\r\n", b"a\r\r\nb") == (1, 2)  # the lone CR is a terminator of its own
    assert backward("\r\n", b"a\r\r\nb") == (2, 4)


def test_line_break_lookbehind_sees_context_before_lo() -> None:
    """The LF of a CRLF is not a terminator of its own, even when the owned range starts between CR and LF."""
    data = b"a\r\nb"
    matcher = compile_matcher("\n", case_sensitive=True)
    assert matcher.find(data, 2, 4) is None
    assert matcher.find(data, 1, 4) == (1, 3)
    assert matcher.rfind(data, 2, 4) == (1, 3)  # the end 3 is owned; the match starts before lo
    assert matcher.rfind(data, 3, 4) is None  # end 3 is not in (3, 4]
    assert matcher.rfind(data, 0, 4) == (1, 3)


def test_line_break_lookbehind_at_lo_forward_pattern() -> None:
    data = b"a\r\nb"
    for case_sensitive in (True, False):
        matcher = compile_matcher("\n", case_sensitive=case_sensitive)
        assert matcher.find(data, 2, 4) is None


def test_line_break_lookahead_sees_context_after_hi() -> None:
    """A CR at the end of the owned range that is followed by an LF past it is half of a CRLF."""
    data = b"a\r\nb"
    for case_sensitive in (True, False):
        matcher = compile_matcher("\r", case_sensitive=case_sensitive)
        assert matcher.find(data, 0, 2) == (1, 3)  # the whole CRLF, owned by its start
        assert matcher.rfind(data, 0, 2) is None  # the CR at 1 ends the match at 3, which is past hi
        assert matcher.rfind(data, 0, 3) == (1, 3)
        two = compile_matcher("a\r", case_sensitive=case_sensitive)
        assert two.rfind(data, 0, 2) is None
        assert two.rfind(data, 0, 3) == (0, 3)


def test_escaped_byte_boundary() -> None:
    assert forward("\udca9", E_ACUTE.encode()) is None
    assert forward("\udca9", b"x\xa9") == (1, 2)
    assert forward("\udcc3", E_ACUTE.encode()) is None
    assert forward("\udcf0", GRINNING.encode()) is None  # a 4-byte lead at the end of the match (J1)
    assert backward("\udca9", b"\xc3\xa9x\xa9") == (3, 4)
    assert forward("\udca9", b"\xc3\xa9\xa9") == (2, 3)  # a stray continuation byte after a complete character
    # Escapes match invalid bytes only: a needle of the two escaped bytes of a valid character does not match it.
    assert forward("\udcc3\udca9", E_ACUTE.encode()) is None
    assert forward("a\udcc3\udca9", b"a" + E_ACUTE.encode()) is None
    assert forward("\udcc3\udca9", b"\xc3\xc3\xa9\xa9") is None
    assert forward("\udcc3\udca9", b"\xc3\x28\xa9\xc3\xc3") is None
    assert forward("\udce2\udc82", b"\xe2\x82") == (0, 2)  # a truncated sequence is two invalid bytes
    assert forward("\udce2\udc82", "\u20ac".encode()) is None


def test_escaped_byte_boundary_keeps_searching_after_a_rejected_candidate() -> None:
    data = E_ACUTE.encode() + b"\xa9"
    assert forward("\udca9", data) == (2, 3)
    assert backward("\udca9", data) == (2, 3)
    data = b"\xa9" + E_ACUTE.encode()
    assert forward("\udca9", data) == (0, 1)
    assert backward("\udca9", data) == (0, 1)


def test_escaped_byte_boundary_in_the_pattern_tier() -> None:
    assert forward("\udca9", E_ACUTE.encode(), case_sensitive=False) is None
    assert forward("\udca9", b"\xc3\xa9\xa9", case_sensitive=False) == (2, 3)
    assert backward("\udca9", b"\xc3\xa9x\xa9", case_sensitive=False) == (3, 4)
    assert forward("\udcf0", GRINNING.encode(), case_sensitive=False) is None
    assert forward("\udcf0", b"\xf0x", case_sensitive=False) == (0, 1)


def test_pattern_syntax_is_literal() -> None:
    needle = ".*[](|\\+?^$"
    for case_sensitive in (True, False):
        assert forward(needle, b"zz" + needle.encode() + b"zz", case_sensitive=case_sensitive) == (2, 2 + len(needle))
        assert backward(needle, b"zz" + needle.encode() + b"zz", case_sensitive=case_sensitive) == (2, 2 + len(needle))
        assert forward(needle, b"zzzz", case_sensitive=case_sensitive) is None
    assert forward(needle + "\n", b"zz" + needle.encode() + b"\nzz") == (2, 2 + len(needle) + 1)


def test_limits() -> None:
    for needle in ("", "\ud800", "a" * (MAX_NEEDLE_BYTES + 1)):
        with pytest.raises(SearchError):
            compile_matcher(needle, case_sensitive=True)
    with pytest.raises(SearchError):
        compile_matcher("a" * (MAX_PATTERN_CHARS + 1), case_sensitive=False)
    compile_matcher("a" * MAX_PATTERN_CHARS, case_sensitive=False)


def test_limits_in_every_mode() -> None:
    with pytest.raises(SearchError):
        compile_matcher("", case_sensitive=False)
    with pytest.raises(SearchError):
        compile_matcher("a\ud800", case_sensitive=False)
    with pytest.raises(SearchError):
        compile_matcher("\udbff", case_sensitive=True)
    with pytest.raises(SearchError):
        compile_matcher("a" * (MAX_NEEDLE_BYTES + 1), case_sensitive=False)
    # A plain needle may be as long as MAX_NEEDLE_BYTES; only a needle needing a pattern is limited to MAX_PATTERN_CHARS.
    compile_matcher("a" * MAX_NEEDLE_BYTES, case_sensitive=True)
    with pytest.raises(SearchError):
        compile_matcher("a\n" * MAX_PATTERN_CHARS, case_sensitive=True)  # a break needs the pattern
    compile_matcher("\n" * MAX_PATTERN_CHARS, case_sensitive=True)
    with pytest.raises(SearchError):
        compile_matcher("\n" * (MAX_PATTERN_CHARS + 1), case_sensitive=True)


def test_crlf_counts_as_one_pattern_character() -> None:
    compile_matcher("\r\n" * MAX_PATTERN_CHARS, case_sensitive=True)


def test_forced_tier_that_cannot_apply_is_refused() -> None:
    with pytest.raises(SearchError):
        compile_matcher("a", case_sensitive=False, tier="find")
    with pytest.raises(SearchError):
        compile_matcher("a\nb", case_sensitive=True, tier="find")
    with pytest.raises(SearchError):
        compile_matcher("a", case_sensitive=True, tier="ascii")
    with pytest.raises(SearchError):
        compile_matcher(E_ACUTE, case_sensitive=False, tier="ascii")
    with pytest.raises(SearchError):
        compile_matcher("a\n", case_sensitive=False, tier="ascii")
    with pytest.raises(SearchError):
        compile_matcher("a", case_sensitive=True, tier=cast("Tier", "nonsense"))
    for tier in ("find", "pattern", "auto"):
        compile_matcher("a", case_sensitive=True, tier=tier)
    for tier in ("ascii", "pattern", "auto"):
        compile_matcher("a", case_sensitive=False, tier=tier)


def test_max_length() -> None:
    assert compile_matcher("ab", case_sensitive=True).max_length == 2
    assert compile_matcher("k", case_sensitive=False).max_length == 3
    assert compile_matcher("a\nb", case_sensitive=True).max_length == 4
    assert compile_matcher("a\r\nb", case_sensitive=True).max_length == 4
    assert compile_matcher("\r\n\r", case_sensitive=False).max_length == 4
    assert compile_matcher(E_ACUTE, case_sensitive=True).max_length == 2
    assert compile_matcher(E_ACUTE, case_sensitive=False).max_length == 2
    assert compile_matcher(KELVIN, case_sensitive=True).max_length == 3
    assert compile_matcher("s", case_sensitive=False).max_length == 2
    assert compile_matcher("\udca9", case_sensitive=False).max_length == 1
    assert compile_matcher(GRINNING + "a", case_sensitive=True).max_length == 5


def test_context_constants() -> None:
    assert CONTEXT_BEFORE == 3
    assert CONTEXT_AFTER == 3


def test_tiers_agree_on_ascii() -> None:
    data = b"Hello WORLD hello world\r\nHeLLo"
    for case_sensitive, tiers in ((True, ("find", "pattern", "auto")), (False, ("ascii", "pattern", "auto"))):
        forward_results = {tier: forward("hello", data, case_sensitive=case_sensitive, tier=tier) for tier in tiers}
        backward_results = {tier: backward("hello", data, case_sensitive=case_sensitive, tier=tier) for tier in tiers}
        assert len(set(forward_results.values())) == 1
        assert len(set(backward_results.values())) == 1
    assert forward("hello", data, case_sensitive=False) == (0, 5)
    assert backward("hello", data, case_sensitive=False) == (len(data) - 5, len(data))
    assert forward("hello", data) == (12, 17)


def test_ascii_tier_on_a_window_with_non_ascii_bytes_uses_the_pattern() -> None:
    data = "Café HELLO".encode()
    assert forward("hello", data, case_sensitive=False, tier="ascii") == (6, 11)
    assert backward("hello", data, case_sensitive=False, tier="ascii") == (6, 11)
    assert forward("hello", data, case_sensitive=False) == (6, 11)


def test_ascii_needle_with_kelvin_matches_in_a_non_ascii_window() -> None:
    data = ("x" + KELVIN + E_ACUTE).encode()
    assert forward("k", data, case_sensitive=False) == (1, 4)
    assert forward(KELVIN, b"K", case_sensitive=False) == (0, 1)


def test_non_ascii_needle_cannot_match_an_ascii_window() -> None:
    assert forward(E_ACUTE, b"e E eE", case_sensitive=False) is None
    assert backward(E_ACUTE, b"e E eE", case_sensitive=False) is None
