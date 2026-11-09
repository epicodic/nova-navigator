"""Search and streaming save over a document whose original is not fully scanned and that holds unknown pieces (ACT1 ADR-2)."""

from __future__ import annotations

from collections.abc import Iterator
from pathlib import Path

import pytest

from nova_editor.core import Content
from nova_editor.core.search import SearchJob, SearchResult, SearchSettings, SearchSpec
from tests.nova_editor.core.search_reference import reference_search
from tests.nova_editor.document.helpers_save import SAVE_SETTINGS, run_save, save_result
from tests.nova_editor.document.test_lazy_byte_edit import DATA, Stepped

JOIN_SECONDS = 10.0


@pytest.fixture
def blind() -> Iterator[Stepped]:
    """A document with a half-scanned original after one blind insert near the end and one blind delete."""
    case = Stepped(DATA)
    try:
        case.step(4)
        assert case.doc.snapshot().scanned_bytes < len(DATA) - 150
        case.insert(len(DATA) - 20, b"needle\nZZ")
        removed = case.delete(len(DATA) - 120, len(DATA) - 90).removed
        assert removed is not None
        assert removed.unknown > 0
        # Stop the scan where it is and let uncached reads (search, save) through the gated source.
        case.doc._line_index.cancel()
        case.source._permits.release(10_000)
        case.doc._line_index.join(JOIN_SECONDS)
        yield case
    finally:
        case.source.stop()
        case.doc.close()
        assert case.doc.wait_closed(JOIN_SECONDS)


def _plan_bytes(case: Stepped) -> bytes:
    plan = case.doc.search_plan(0, case.doc.length)
    assert plan.length == len(case.ref)
    return b"".join(part.source.read(part.a, part.b - part.a) for part in plan.parts)


def test_search_plan_over_unscanned_pieces_equals_the_reference(blind: Stepped) -> None:
    assert _plan_bytes(blind) == blind.ref
    window = blind.doc.search_plan(len(DATA) - 130, 90)
    assert b"".join(p.source.read(p.a, p.b - p.a) for p in window.parts) == blind.ref[len(DATA) - 130 : len(DATA) - 40]


@pytest.mark.parametrize("backward", [False, True])
@pytest.mark.parametrize("needle", ["needle", "row 03", "ZZ", "absent"])
def test_search_job_over_unscanned_pieces_matches_the_reference(blind: Stepped, needle: str, backward: bool) -> None:
    for origin in (0, 60, len(blind.ref) // 2, len(blind.ref)):
        found = reference_search(blind.ref, needle, case_sensitive=True, backward=backward, wrap=True, origin=origin)
        expected = None if found is None else SearchResult(*found)
        spec = SearchSpec(needle, case_sensitive=True, backward=backward, wrap=True)
        assert SearchJob(blind.doc, spec, origin, SearchSettings(chunk=7)).run() == expected


def test_save_over_unscanned_pieces_writes_the_reference_bytes(blind: Stepped, tmp_path: Path) -> None:
    target = tmp_path / "out.bin"
    save_result(blind.doc, target)
    assert target.read_bytes() == blind.ref


def test_save_and_rebase_keeps_the_unknown_pieces_readable(blind: Stepped, tmp_path: Path) -> None:
    assert blind.doc.snapshot().scanned_bytes < blind.doc.length
    target = tmp_path / "out.bin"
    tail = blind.doc._table.byte_content(len(DATA) - 130)  # pieces of the history that still reference unknown ranges
    contents: list[Content] = [tail]
    run_save(blind.doc, target, contents, SAVE_SETTINGS)
    assert target.read_bytes() == blind.ref
    assert blind.doc.wait_rebased(JOIN_SECONDS)
    assert blind.content() == blind.ref
    assert blind.doc.read_bytes(len(DATA) - 130, 40) == blind.ref[len(DATA) - 130 : len(DATA) - 90]
    blind.insert(5, b"after")
    assert blind.content() == blind.ref
    save_result(blind.doc, target)
    assert target.read_bytes() == blind.ref
