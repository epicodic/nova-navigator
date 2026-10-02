"""Property tests: the matcher against the brute-force reference of `search_reference.py`."""

from __future__ import annotations

from itertools import pairwise

import pytest
from hypothesis import given, settings
from hypothesis import strategies as st

from nova_editor.core.byte_source import ByteSource
from nova_editor.core.memory_source import BytesSource
from nova_editor.core.search import SearchJob, SearchResult, SearchSettings, SearchSpec, compile_matcher
from tests.nova_editor.core.fake_planner import FakePlanner, whole_file_planner
from tests.nova_editor.core.search_reference import all_matches, boundaries, reference_search

EXAMPLES = 300
JOB_EXAMPLES = 200
LARGE_CHUNK = 1 << 20
MAX_CHUNK = 17
SWEEP_CHUNKS = range(1, 9)
SEED_SETTINGS = settings(max_examples=EXAMPLES, deadline=None, derandomize=True)
JOB_SETTINGS = settings(max_examples=JOB_EXAMPLES, deadline=None, derandomize=True)
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


def layout_planner(data: bytes, draw: st.DataObject) -> FakePlanner:
    """Cut `data` at drawn points and lay the pieces out over an original and an add buffer, in document order."""
    cuts = sorted(draw.draw(st.sets(st.integers(0, len(data)), max_size=8)) | {0, len(data)})
    buffers = [bytearray(), bytearray()]
    runs: list[tuple[int, int, int]] = []
    for lo, hi in pairwise(cuts):
        if lo == hi:
            continue
        src = draw.draw(st.integers(0, 1))
        start = len(buffers[src])
        buffers[src].extend(data[lo:hi])
        runs.append((src, start, start + (hi - lo)))
    sources: dict[int, ByteSource] = {0: BytesSource(bytes(buffers[0])), 1: BytesSource(bytes(buffers[1]))}
    return FakePlanner(runs, sources)


def _expected(data: bytes, spec: SearchSpec, origin: int) -> SearchResult | None:
    found = reference_search(data, spec.needle, case_sensitive=spec.case_sensitive, backward=spec.backward, wrap=spec.wrap, origin=origin)
    return None if found is None else SearchResult(*found)


@JOB_SETTINGS
@given(
    st.data(),
    needle_and_data(),
    st.booleans(),
    st.booleans(),
    st.booleans(),
    st.one_of(st.integers(1, MAX_CHUNK), st.just(LARGE_CHUNK)),
)
def test_job_matches_the_reference(draw: st.DataObject, case: tuple[str, bytes], case_sensitive: bool, backward: bool, wrap: bool, chunk: int) -> None:
    needle, data = case
    origin = draw.draw(st.sampled_from(boundaries(data)))
    planner = layout_planner(data, draw)
    spec = SearchSpec(needle, case_sensitive=case_sensitive, backward=backward, wrap=wrap)
    assert SearchJob(planner, spec, origin, SearchSettings(chunk=chunk)).run() == _expected(data, spec, origin)


@JOB_SETTINGS
@given(
    st.data(),
    needle_and_data(),
    st.booleans(),
    st.booleans(),
    st.booleans(),
    st.integers(1, MAX_CHUNK),
    st.sampled_from(["auto", "pattern"]),
)
def test_job_over_one_piece_and_the_pattern_tier(draw: st.DataObject, case: tuple[str, bytes], case_sensitive: bool, backward: bool, wrap: bool, chunk: int, tier: str) -> None:
    needle, data = case
    origin = draw.draw(st.sampled_from(boundaries(data)))
    spec = SearchSpec(needle, case_sensitive=case_sensitive, backward=backward, wrap=wrap)
    planner = whole_file_planner(BytesSource(data))
    assert SearchJob(planner, spec, origin, SearchSettings(chunk=chunk, tier=tier)).run() == _expected(data, spec, origin)


@pytest.mark.parametrize("wrap", [True, False])
@pytest.mark.parametrize("backward", [False, True])
@pytest.mark.parametrize(
    ("needle", "data"),
    [
        ("\n", b"aa\r\nbb"),
        ("\r", b"aa\r\nbb"),
        ("a\r\nb", b"aa\r\nbb"),
        (chr(0xE9), b"a" + chr(0xE9).encode() * 2 + b"b"),
        (chr(0xE9) + "b", b"a" + chr(0xE9).encode() * 2 + b"b"),
        (chr(0x1F600), b"x" + chr(0x1F600).encode() + chr(0x1F600).encode() + b"y"),
        (chr(0x1F600) + "y", b"x" + chr(0x1F600).encode() + chr(0x1F600).encode() + b"y"),
    ],
)
def test_window_boundary_sweep(needle: str, data: bytes, backward: bool, wrap: bool) -> None:
    spec = SearchSpec(needle, backward=backward, wrap=wrap)
    for chunk in SWEEP_CHUNKS:
        for origin in boundaries(data):
            got = SearchJob(whole_file_planner(BytesSource(data)), spec, origin, SearchSettings(chunk=chunk)).run()
            assert got == _expected(data, spec, origin), (chunk, origin)


@JOB_SETTINGS
@given(st.data(), needle_and_data(), st.booleans(), st.booleans(), st.integers(1, MAX_CHUNK))
def test_repeated_search_never_overlaps_the_previous_match(draw: st.DataObject, case: tuple[str, bytes], case_sensitive: bool, backward: bool, chunk: int) -> None:
    needle, data = case
    origin = draw.draw(st.sampled_from(boundaries(data)))
    spec = SearchSpec(needle, case_sensitive=case_sensitive, backward=backward)
    settings_ = SearchSettings(chunk=chunk)
    old = SearchJob(layout_planner(data, draw), spec, origin, settings_).run()
    if old is None:
        return
    again = old.start if backward else old.end
    new = SearchJob(layout_planner(data, draw), spec, again, settings_).run()
    assert new is not None
    if not new.wrapped:
        assert new.end <= old.start if backward else new.start >= old.end


@pytest.mark.parametrize("chunk", [1, 2, 3, LARGE_CHUNK])
def test_aa_in_aaaa_is_found_twice(chunk: int) -> None:
    settings_ = SearchSettings(chunk=chunk)
    spec = SearchSpec("aa", wrap=False)
    planner = whole_file_planner(BytesSource(b"aaaa"))
    first = SearchJob(planner, spec, 0, settings_).run()
    assert first == SearchResult(0, 2, False)
    second = SearchJob(planner, spec, 2, settings_).run()
    assert second == SearchResult(2, 4, False)
    assert SearchJob(planner, spec, 4, settings_).run() is None
    back = SearchSpec("aa", backward=True, wrap=False)
    assert SearchJob(planner, back, 4, settings_).run() == SearchResult(2, 4, False)
    assert SearchJob(planner, back, 2, settings_).run() == SearchResult(0, 2, False)
    assert SearchJob(planner, back, 0, settings_).run() is None
