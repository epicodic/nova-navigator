"""Property tests: the matcher against the brute-force reference of `search_reference.py`."""

from __future__ import annotations

import pytest
from hypothesis import given, settings
from hypothesis import strategies as st

from nova_editor.core.search import compile_matcher
from tests.nova_editor.core.search_reference import all_matches

EXAMPLES = 300
SEED_SETTINGS = settings(max_examples=EXAMPLES, deadline=None, derandomize=True)
KELVIN = chr(0x212A)
LONG_S = chr(0x17F)
SWEEP_DOCUMENT = b"a\r\n\xc3\xa9\xa9\xe2\x82\xac\r\nc"
SWEEP_NEEDLES = ["\n", "\r", "\r\n", "\udca9", "\udcc3\udca9", "\udce2", "\xe9", "a\n", "\udca9\udce2", "\n\udcc3", "c"]
PIECES: list[bytes] = [
    b"a",
    b"A",
    b"k",
    b"K",
    KELVIN.encode(),
    b"s",
    b"S",
    LONG_S.encode(),
    chr(0xE9).encode(),
    chr(0xC9).encode(),
    chr(0xDF).encode(),
    chr(0x1E9E).encode(),
    chr(0x130).encode(),
    chr(0x131).encode(),
    b"i",
    b"I",
    chr(0x3C3).encode(),
    chr(0x3C2).encode(),
    chr(0x3A3).encode(),
    chr(0xB5).encode(),
    chr(0x3BC).encode(),
    chr(0x1F600).encode(),
    b"\r",
    b"\n",
    b" ",
    b"\x80",
    b"\xa9",
    b"\xc3",
    b"\xe2\x82",
    b"\xf0\x9f",
]
NEEDLE_CHARS = (
    "aAkK"
    + KELVIN
    + "sS"
    + LONG_S
    + chr(0xE9)
    + chr(0xC9)
    + chr(0xDF)
    + chr(0x1E9E)
    + chr(0x130)
    + chr(0x131)
    + "iI"
    + chr(0x3C3)
    + chr(0x3C2)
    + chr(0x3A3)
    + chr(0xB5)
    + chr(0x3BC)
    + chr(0x1F600)
    + "\r\n \udc80\udca9\udcc3\udce2\udcf0"
)
ASCII_PIECES: list[bytes] = [b"a", b"A", b"k", b"K", b"s", b"S", b"i", b"I", b"b", b"\r", b"\n", b" ", b"0"]
ASCII_NEEDLE_CHARS = "aAkKsSiIb\r\n 0"
documents = st.lists(st.sampled_from(PIECES), max_size=24).map(b"".join)
needles = st.text(alphabet=NEEDLE_CHARS, min_size=1, max_size=4)
ascii_documents = st.lists(st.sampled_from(ASCII_PIECES), max_size=24).map(b"".join)
ascii_needles = st.text(alphabet=ASCII_NEEDLE_CHARS, min_size=1, max_size=4)


@st.composite
def needle_and_data(draw: st.DrawFn) -> tuple[str, bytes]:
    data = draw(documents)
    text = data.decode("utf-8", "surrogateescape")
    if text and draw(st.booleans()):
        start = draw(st.integers(0, len(text) - 1))
        stop = draw(st.integers(start + 1, min(len(text), start + 5)))
        return text[start:stop], data
    return draw(needles), data


def _last(matches: list[tuple[int, int]]) -> tuple[int, int] | None:
    return max(matches, key=lambda m: (m[1], m[0])) if matches else None


def _first(matches: list[tuple[int, int]]) -> tuple[int, int] | None:
    return min(matches) if matches else None


@SEED_SETTINGS
@given(needle_and_data(), st.booleans())
def test_matcher_matches_the_reference(case: tuple[str, bytes], case_sensitive: bool) -> None:
    needle, data = case
    matcher = compile_matcher(needle, case_sensitive=case_sensitive)
    expected = all_matches(data, needle, case_sensitive=case_sensitive)
    assert matcher.find(data, 0, len(data)) == _first(expected)
    assert matcher.rfind(data, 0, len(data)) == _last(expected)


@SEED_SETTINGS
@given(needle_and_data(), st.booleans())
def test_pattern_tier_backward_is_the_greatest_end_of_the_forward_matches(case: tuple[str, bytes], case_sensitive: bool) -> None:
    """Design 4.3: the reversed pattern on the reversed window returns the match with the greatest end."""
    needle, data = case
    matcher = compile_matcher(needle, case_sensitive=case_sensitive, tier="pattern")
    expected = all_matches(data, needle, case_sensitive=case_sensitive)
    assert matcher.rfind(data, 0, len(data)) == _last(expected)
    assert matcher.find(data, 0, len(data)) == _first(expected)


@SEED_SETTINGS
@given(ascii_documents, ascii_needles, st.booleans())
def test_forced_tiers_agree_on_ascii(data: bytes, needle: str, case_sensitive: bool) -> None:
    has_break = "\r" in needle or "\n" in needle
    tiers = ["pattern", "auto"]
    if case_sensitive and not has_break:
        tiers.append("find")
    if not case_sensitive and not has_break:
        tiers.append("ascii")
    expected = all_matches(data, needle, case_sensitive=case_sensitive)
    for tier in tiers:
        matcher = compile_matcher(needle, case_sensitive=case_sensitive, tier=tier)
        assert matcher.find(data, 0, len(data)) == _first(expected), tier
        assert matcher.rfind(data, 0, len(data)) == _last(expected), tier


@pytest.mark.parametrize("case_sensitive", [True, False])
@pytest.mark.parametrize("needle", SWEEP_NEEDLES)
def test_owned_range_sweep_over_every_split(needle: str, case_sensitive: bool) -> None:
    data = SWEEP_DOCUMENT
    assert len(data) == 12
    matcher = compile_matcher(needle, case_sensitive=case_sensitive)
    found = all_matches(data, needle, case_sensitive=case_sensitive)
    for lo in range(len(data) + 1):
        for hi in range(lo, len(data) + 1):
            forward_expected = _first([m for m in found if lo <= m[0] < hi])
            backward_expected = _last([m for m in found if lo < m[1] <= hi])
            assert matcher.find(data, lo, hi) == forward_expected, (lo, hi)
            assert matcher.rfind(data, lo, hi) == backward_expected, (lo, hi)
