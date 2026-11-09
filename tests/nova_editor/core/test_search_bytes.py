"""Tests of the byte needle: `ByteMatcher` behind `SearchJob`, checked against `bytes.find` and `bytes.rfind`."""

from __future__ import annotations

from itertools import pairwise
from pathlib import Path

import pytest
from hypothesis import given, settings
from hypothesis import strategies as st

from nova_editor.core.byte_source import BLOCK_SIZE, PreadSource
from nova_editor.core.memory_source import BytesSource
from nova_editor.core.search import (
    MAX_NEEDLE_BYTES,
    SearchCancelled,
    SearchError,
    SearchJob,
    SearchProgress,
    SearchResult,
    SearchSettings,
    SearchSpec,
    SearchStale,
    compile_matcher,
)
from nova_editor.document import LazyDocument
from tests.nova_editor.core.fake_planner import FakePlanner, edited_planner, whole_file_planner
from tests.nova_editor.document.helpers_save import content_of


def reference(data: bytes, needle: bytes, origin: int, *, backward: bool, wrap: bool) -> SearchResult | None:
    """The result `SearchJob` must return, from `bytes.find` and `bytes.rfind`."""
    length = len(data)
    origin = max(0, min(origin, length))
    size = len(needle)
    if not backward:
        start = data.find(needle, origin)
        if start >= 0:
            return SearchResult(start, start + size, False)
        if wrap and origin > 0:
            start = data.find(needle, 0, min(length, origin + size - 1))
            if 0 <= start < origin:
                return SearchResult(start, start + size, True)
        return None
    start = data.rfind(needle, 0, origin)
    if start >= 0:
        return SearchResult(start, start + size, False)
    if wrap and origin < length:
        start = data.rfind(needle, max(0, origin - size + 1), length)
        if start >= 0 and start + size > origin:
            return SearchResult(start, start + size, True)
    return None


def run(
    planner: FakePlanner,
    needle: bytes,
    *,
    origin: int = 0,
    backward: bool = False,
    wrap: bool = True,
    chunk: int = 16,
    case_sensitive: bool = True,
) -> SearchResult | None:
    spec = SearchSpec(needle, case_sensitive=case_sensitive, backward=backward, wrap=wrap)
    return SearchJob(planner, spec, origin, SearchSettings(chunk=chunk)).run()


def planner_of(data: bytes) -> FakePlanner:
    return whole_file_planner(BytesSource(data))


def test_a_byte_needle_finds_the_first_match_forward() -> None:
    assert run(planner_of(b"xxabxxabxx"), b"ab") == SearchResult(2, 4, False)
    assert run(planner_of(b"xxabxxabxx"), b"ab", origin=3) == SearchResult(6, 8, False)


def test_a_byte_needle_backward_finds_the_last_match_before_the_origin() -> None:
    data = b"xxabxxabxx"
    assert run(planner_of(data), b"ab", origin=10, backward=True) == SearchResult(6, 8, False)
    assert run(planner_of(data), b"ab", origin=7, backward=True) == SearchResult(2, 4, False)


@pytest.mark.parametrize("position", range(19))
def test_a_match_across_a_window_boundary_is_found_once(position: int) -> None:
    data = b"." * position + b"XY" + b"." * (18 - position)
    planner = planner_of(data)
    assert run(planner, b"XY", chunk=4) == SearchResult(position, position + 2, False)
    assert run(planner, b"XY", chunk=4, origin=position + 1) == SearchResult(position, position + 2, True)
    assert run(planner, b"XY", chunk=4, origin=len(data), backward=True) == SearchResult(position, position + 2, False)
    assert run(planner, b"XY", chunk=4, origin=position + 1, backward=True) == SearchResult(position, position + 2, True)


@pytest.mark.parametrize("needle", [b"aXYZ", b"ZWb", b"aXYZWb"])
def test_a_match_across_a_piece_boundary(needle: bytes) -> None:
    original = BytesSource(b"aaaaabbbbb")
    add = BytesSource(b"XYZW")
    planner = edited_planner([(0, 0, 5), (1, 0, 4), (0, 5, 10)], original, add)
    data = b"aaaaa" + b"XYZW" + b"bbbbb"
    for chunk in (1, 3, 64):
        found = run(planner, needle, chunk=chunk)
        assert found == reference(data, needle, 0, backward=False, wrap=True)
        assert found is not None
        back = run(planner, needle, chunk=chunk, origin=len(data), backward=True)
        assert back == reference(data, needle, len(data), backward=True, wrap=True)


@pytest.mark.parametrize("chunk", [BLOCK_SIZE, 100_000, 1 << 20])
def test_a_match_across_a_source_block_boundary(tmp_path: Path, chunk: int) -> None:
    data = bytearray(3 * BLOCK_SIZE)
    data[BLOCK_SIZE - 2 : BLOCK_SIZE + 2] = b"\xde\xad\xbe\xef"
    path = tmp_path / "blocks.bin"
    path.write_bytes(data)
    source = PreadSource(path)
    try:
        planner = whole_file_planner(source)
        needle = b"\xde\xad\xbe\xef"
        assert run(planner, needle, chunk=chunk) == SearchResult(BLOCK_SIZE - 2, BLOCK_SIZE + 2, False)
        assert run(planner, needle, chunk=chunk, origin=len(data), backward=True) == SearchResult(BLOCK_SIZE - 2, BLOCK_SIZE + 2, False)
    finally:
        source.close()


@pytest.mark.parametrize("backward", [False, True])
def test_overlapping_matches_all_found_by_successive_jobs(backward: bool) -> None:
    data = b"aaaa"
    planner = planner_of(data)
    if not backward:
        starts = [run(planner, b"aa", origin=origin, wrap=False) for origin in range(4)]
        assert starts == [SearchResult(0, 2, False), SearchResult(1, 3, False), SearchResult(2, 4, False), None]
    else:
        ends = [run(planner, b"aa", origin=origin, backward=True, wrap=False) for origin in range(4, -1, -1)]
        assert ends == [SearchResult(2, 4, False), SearchResult(1, 3, False), SearchResult(0, 2, False), None, None]


@pytest.mark.parametrize(
    ("data", "needle", "expected"),
    [
        (b"x\xc3\xa9y", b"\xa9", SearchResult(2, 3, False)),
        (b"x\xc3\xa9y", b"\xc3", SearchResult(1, 2, False)),
        (b"ab\r\ncd", b"\r", SearchResult(2, 3, False)),
        (b"ab\r\ncd", b"\n", SearchResult(3, 4, False)),
        (b"ab\xffcd\xfe", b"\xff", SearchResult(2, 3, False)),
        (b"ab\xff\xfecd", b"\xff\xfe", SearchResult(2, 4, False)),
    ],
)
def test_a_needle_that_splits_a_utf8_character_or_crlf_matches(data: bytes, needle: bytes, expected: SearchResult) -> None:
    for chunk in (1, 2, 64):
        assert run(planner_of(data), needle, chunk=chunk) == expected


def test_case_sensitive_flag_is_ignored_for_bytes() -> None:
    planner = planner_of(b"xxABxxab")
    assert run(planner, b"ab", case_sensitive=False) == SearchResult(6, 8, False)
    assert run(planner, b"ab", case_sensitive=True) == SearchResult(6, 8, False)
    assert run(planner, b"AB", case_sensitive=False) == SearchResult(2, 4, False)


def test_wrap_forward_and_backward_reports_wrapped() -> None:
    planner = planner_of(b"xxabxxxxxx")
    assert run(planner, b"ab", origin=5) == SearchResult(2, 4, True)
    assert run(planner, b"ab", origin=2, backward=True) == SearchResult(2, 4, True)
    assert run(planner, b"ab", origin=3) == SearchResult(2, 4, True)
    assert run(planner, b"ab", origin=3, backward=True) == SearchResult(2, 4, True)


def test_no_wrap_reports_none() -> None:
    planner = planner_of(b"xxabxxxxxx")
    assert run(planner, b"ab", origin=5, wrap=False) is None
    assert run(planner, b"ab", origin=2, backward=True, wrap=False) is None


def test_not_found_returns_none() -> None:
    assert run(planner_of(b"abcabc"), b"zz") is None
    assert run(planner_of(b"abcabc"), b"zz", backward=True, origin=6) is None


def test_needle_longer_than_document() -> None:
    assert run(planner_of(b"abc"), b"abcd", chunk=1) is None
    assert run(planner_of(b"abc"), b"abcd", chunk=1, backward=True, origin=3) is None


def test_empty_document() -> None:
    planner = planner_of(b"")
    assert run(planner, b"a") is None
    assert planner.plan_calls == [(0, 0)]


def test_progress_is_monotonic_and_ends_at_total() -> None:
    data = b"x" * 100
    reports: list[SearchProgress] = []
    job = SearchJob(
        planner_of(data),
        SearchSpec(b"ab"),
        40,
        SearchSettings(chunk=7, progress_interval=0.0),
        progress=reports.append,
    )
    assert job.run() is None
    assert reports
    dones = [report.done for report in reports]
    assert all(a < b for a, b in pairwise(dones))
    assert {report.total for report in reports} == {100}
    assert dones[-1] == 100
    assert {report.phase for report in reports} == {"forward", "wrapped"}


def test_cancel_after_k_units_stops_planning() -> None:
    planner = planner_of(b"x" * 100)
    job = SearchJob(planner, SearchSpec(b"ab"), 0, SearchSettings(chunk=10))

    def on_plan(count: int) -> None:
        if count == 4:
            job.cancel()

    planner.on_plan = on_plan
    with pytest.raises(SearchCancelled):
        job.run()
    assert len(planner.plan_calls) == 4


def test_cancel_before_the_first_unit() -> None:
    planner = planner_of(b"x" * 100)
    job = SearchJob(planner, SearchSpec(b"ab"), 0)
    job.cancel()
    with pytest.raises(SearchCancelled):
        job.run()
    assert planner.plan_calls == []


def test_a_revision_change_raises_stale() -> None:
    planner = planner_of(b"x" * 100)

    def on_plan(count: int) -> None:
        if count == 2:
            planner.revision += 1

    planner.on_plan = on_plan
    with pytest.raises(SearchStale):
        SearchJob(planner, SearchSpec(b"ab"), 0, SearchSettings(chunk=10)).run()


def test_unscanned_pieces_of_a_real_lazy_document(tmp_path: Path) -> None:
    path = tmp_path / "doc.bin"
    path.write_bytes((b"abc\nacb" * 700)[:5000] + b"NEEDLE" + (b"cab\nbca" * 100)[:500])
    doc = LazyDocument.from_path(path, autostart=False)
    try:
        doc.splice_bytes(100, 103, content_of(doc, b"<<EDIT>>"))
        doc.splice_bytes(2000, 2000, content_of(doc, b"NEE"))
        whole = doc.read_bytes(0, doc.length)
        for needle in (b"NEEDLE", b"NEENEEDLE", b"<<EDIT>>", b"c<<E", b"T>>a", b"zzz"):
            for origin in (0, 1000, 2001, doc.length):
                for backward in (False, True):
                    spec = SearchSpec(needle, backward=backward)
                    got = SearchJob(doc, spec, origin, SearchSettings(chunk=300)).run()
                    assert got == reference(whole, needle, origin, backward=backward, wrap=True), (needle, origin, backward)
    finally:
        doc.close()


ALPHABET = st.sampled_from([0x61, 0x62, 0x0D, 0x0A, 0xC3, 0xA9])


@settings(max_examples=400, deadline=None, derandomize=True)
@given(
    data=st.lists(ALPHABET, max_size=60).map(bytes),
    needle=st.lists(ALPHABET, min_size=1, max_size=4).map(bytes),
    origin=st.integers(0, 62),
    backward=st.booleans(),
    wrap=st.booleans(),
    chunk=st.integers(1, 16),
)
def test_fuzz_against_bytes_find_and_rfind(data: bytes, needle: bytes, origin: int, backward: bool, wrap: bool, chunk: int) -> None:
    got = run(planner_of(data), needle, origin=origin, backward=backward, wrap=wrap, chunk=chunk)
    assert got == reference(data, needle, origin, backward=backward, wrap=wrap)


def test_invalid_byte_needles() -> None:
    planner = planner_of(b"abc")
    for needle in (b"", bytes(MAX_NEEDLE_BYTES + 1)):
        with pytest.raises(SearchError):
            SearchJob(planner, SearchSpec(needle), 0)
    assert planner.plan_calls == []


def test_a_byte_matcher_is_not_compiled_into_a_pattern() -> None:
    matcher = compile_matcher(b"abc", case_sensitive=False)
    assert matcher.may_use_pattern is False
    assert matcher.max_length == 3
    assert matcher.token_count == 3
    planner = planner_of(b"x" * 100)
    job = SearchJob(planner, SearchSpec(b"abc"), 0, SearchSettings(chunk=30, pattern_work_budget=1, min_pattern_span=1))
    assert job.run() is None
    sizes = sorted({limit for _, limit in planner.plan_calls if limit})
    assert max(sizes) <= 30 + 3 + 2 + 3
    assert len([call for call in planner.plan_calls if call[1]]) == 4
