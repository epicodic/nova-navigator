"""Deterministic job tests of the context bytes a window reads around its owned range (J1)."""

from __future__ import annotations

import pytest

from nova_editor.core.memory_source import BytesSource
from nova_editor.core.search import SearchJob, SearchResult, SearchSettings, SearchSpec
from tests.nova_editor.core.fake_planner import whole_file_planner
from tests.nova_editor.core.search_reference import boundaries, reference_search

CHUNKS = [1, 2, 3, 4, 5, 1 << 20]
LEAD_2 = b"\xc3\xa9"
LEAD_3 = b"\xe2\x82\xac"
LEAD_4 = b"\xf0\x9f\x98\x80"

# (needle, document, whether the needle is expected to be found at all)
AFTER_CASES = [
    pytest.param("\r", b"abc\r\ndef", True, id="cr-before-lf"),
    pytest.param("\r", b"abc\r\r\ndef", True, id="cr-before-crlf-is-lone"),
    pytest.param("\r", b"abc\rdef", True, id="cr-stray"),
    pytest.param("\udcf0", b"xx" + LEAD_4 + b"yy", False, id="lead4-inside-char"),
    pytest.param("\udce2", b"xx" + LEAD_3 + b"yy", False, id="lead3-inside-char"),
    pytest.param("\udcc3", b"xx" + LEAD_2 + b"yy", False, id="lead2-inside-char"),
    pytest.param("\udcf0", b"xx\xf0yy", True, id="lead4-stray"),
    pytest.param("\udce2", b"xx\xe2yy", True, id="lead3-stray"),
    pytest.param("\udcc3", b"xx\xc3yy", True, id="lead2-stray"),
    pytest.param("x\udcf0", b"x" + LEAD_4 + b"x\xf0z", True, id="lead4-second-occurrence-stray"),
]
BEFORE_CASES = [
    pytest.param("\udca9", b"xx" + LEAD_2 + b"yy", False, id="continuation-after-lead2"),
    pytest.param("\udcac", b"xx" + LEAD_3 + b"yy", False, id="continuation-after-lead3"),
    pytest.param("\udc80", b"xx" + LEAD_4 + b"yy", False, id="continuation-after-lead4"),
    pytest.param("\udca9", b"xx\xa9yy", True, id="continuation-stray"),
    pytest.param("\n", b"ab\r\ncd", True, id="lf-after-cr"),
    pytest.param("\n", b"ab\r\n\r\ncd\r\n", True, id="several-crlf"),
    pytest.param("\n\n", b"a\r\n\nb\n\r\nc", True, id="mixed-breaks"),
]


def _run(data: bytes, needle: str, *, chunk: int, case_sensitive: bool, backward: bool, origin: int, wrap: bool) -> SearchResult | None:
    spec = SearchSpec(needle, case_sensitive=case_sensitive, backward=backward, wrap=wrap)
    return SearchJob(whole_file_planner(BytesSource(data)), spec, origin, SearchSettings(chunk=chunk)).run()


def _check(needle: str, data: bytes) -> int:
    """Compare the job with the reference over every chunk, mode, direction and character-boundary origin."""
    found_any = 0
    for chunk in CHUNKS:
        for case_sensitive in (True, False):
            for backward in (False, True):
                for origin in boundaries(data):
                    for wrap in (False, True):
                        want = reference_search(data, needle, case_sensitive=case_sensitive, backward=backward, wrap=wrap, origin=origin)
                        got = _run(data, needle, chunk=chunk, case_sensitive=case_sensitive, backward=backward, origin=origin, wrap=wrap)
                        expected = None if want is None else SearchResult(*want)
                        assert got == expected, (chunk, case_sensitive, backward, origin, wrap)
                        found_any += got is not None
    return found_any


@pytest.mark.parametrize(("needle", "data", "expect_found"), AFTER_CASES)
def test_end_inside_a_character_needs_the_context_after(needle: str, data: bytes, expect_found: bool) -> None:
    found = _check(needle, data)
    assert (found > 0) is expect_found


@pytest.mark.parametrize(("needle", "data", "expect_found"), BEFORE_CASES)
def test_start_needs_the_context_before(needle: str, data: bytes, expect_found: bool) -> None:
    found = _check(needle, data)
    assert (found > 0) is expect_found


@pytest.mark.parametrize("backward", [False, True])
@pytest.mark.parametrize("chunk", CHUNKS)
def test_cr_that_is_the_last_byte_of_an_owned_window_matches_the_whole_crlf(chunk: int, backward: bool) -> None:
    """A CRLF is one break, so the needle `\\r` matches both bytes even when the window owns only the CR (chunk 4)."""
    data = b"abc\r\ndef"
    for case_sensitive in (True, False):
        for origin in boundaries(data):
            for wrap in (False, True):
                want = reference_search(data, "\r", case_sensitive=case_sensitive, backward=backward, wrap=wrap, origin=origin)
                got = _run(data, "\r", chunk=chunk, case_sensitive=case_sensitive, backward=backward, origin=origin, wrap=wrap)
                assert got == (None if want is None else SearchResult(*want)), (case_sensitive, origin, wrap)
                if got is not None:
                    assert (got.start, got.end) == (3, 5)
