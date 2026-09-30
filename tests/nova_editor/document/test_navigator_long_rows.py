"""Long-row branches of `DocumentNavigator`: display-column arithmetic, no whole-row access, no waiting (ACT3 design 4.5, 7)."""

from __future__ import annotations

import time
from bisect import bisect_right
from collections.abc import Iterator
from functools import cache
from pathlib import Path

import pytest

from nova_editor.core import ByteSource, PreadSource
from nova_editor.document._document import Location
from nova_editor.document._document_navigator import DocumentNavigator
from nova_editor.document._lazy_config import LazyConfig
from nova_editor.document._lazy_document import LazyDocument
from nova_editor.document._lazy_wrapped_document import GridOffsets, LazyWrappedDocument
from tests.nova_editor.helpers_view import LOWERED_OPTIONS, GateSource, make_mixed, oracle_display_column, oracle_row_text

_LONG_ROW = 12
_WIDTHS = (0, 20)
_TAB = 4


@cache
def _oracle_starts(text: str) -> list[int]:
    """Display column at which every character starts (one linear pass); the last item is the display width of the row."""
    starts = [0]
    for char in text:
        starts.append(starts[-1] + oracle_display_column(char, 1, _TAB) if char != "\t" else starts[-1] + _TAB - starts[-1] % _TAB)
    return starts


def _oracle_offsets(text: str, width: int) -> list[int]:
    """First column of every wrapped section after the first (grid wrap on display columns)."""
    if not width:
        return []
    offsets: list[int] = []
    seen = 0
    for column, start in enumerate(_oracle_starts(text)[:-1]):
        if start // width > seen:
            seen = start // width
            offsets.append(column)
    return offsets


def _oracle_cover(text: str, x: int) -> int:
    """Column of the character covering display column `x` (the row length beyond the end)."""
    starts = _oracle_starts(text)
    if x >= starts[-1]:
        return len(text)
    return bisect_right(starts, x, 0, len(text)) - 1


def _oracle_target(text: str, width: int, x: int, section: int) -> int:
    """Column at cell `x` of `section` (-1 for the last one), clamped to the section."""
    offsets = _oracle_offsets(text, width)
    if not width:
        return _oracle_cover(text, x)
    section = len(offsets) if section == -1 else section
    start = 0 if section == 0 else offsets[section - 1]
    base = _oracle_starts(text)[start] if section else 0
    column = _oracle_cover(text, base + x)
    if section < len(offsets):
        column = min(column, offsets[section] - 1)
    return max(column, start)


def _oracle_section_x(text: str, width: int, column: int) -> tuple[int, int]:
    offsets = _oracle_offsets(text, width)
    section = bisect_right(offsets, column)
    starts = _oracle_starts(text)
    start = 0 if section == 0 else offsets[section - 1]
    return section, starts[column] - (starts[start] if section else 0)


def _sample_columns(text: str, width: int) -> list[int]:
    columns = {0, 1, len(text) - 1, len(text)}
    columns.update(range(0, len(text), 37))
    for offset in _oracle_offsets(text, width):
        columns.update({offset - 2, offset - 1, offset, offset + 1})
    return sorted(column for column in columns if 0 <= column <= len(text))


class Rig:
    """A lazy document, its wrapped view and a navigator, with tripwires on every whole-row path."""

    def __init__(self, source: ByteSource, width: int) -> None:
        self.doc = LazyDocument(source, LazyConfig(**LOWERED_OPTIONS))
        assert self.doc.wait_indexed(10)
        self.wrapped = LazyWrappedDocument(self.doc, width, _TAB)
        self.nav = DocumentNavigator(self.wrapped)
        self.width = width
        self.sections_calls = 0
        self.iterations = 0

    def install_tripwires(self, monkeypatch: pytest.MonkeyPatch) -> None:
        original = LazyWrappedDocument.get_sections

        def no_long_sections(wrapped: LazyWrappedDocument, line_index: int) -> list[str]:
            if self.doc.is_long(line_index):
                self.sections_calls += 1
                msg = "get_sections called on a long row"
                raise AssertionError(msg)
            return original(wrapped, line_index)

        def no_iter(offsets: GridOffsets) -> Iterator[int]:
            del offsets
            self.iterations += 1
            msg = "GridOffsets iterated"
            raise AssertionError(msg)

        monkeypatch.setattr(LazyWrappedDocument, "get_sections", no_long_sections)
        monkeypatch.setattr(GridOffsets, "__iter__", no_iter)

    def check_bounded(self) -> None:
        assert self.doc.call_log.refusals == []
        assert self.doc.call_log.max_decoded_chars <= 8192
        assert self.sections_calls == 0
        assert self.iterations == 0


@pytest.fixture(params=_WIDTHS, ids=["nowrap", "wrap20"])
def rig(request: pytest.FixtureRequest, tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Iterator[tuple[Rig, str, bytes]]:
    path = make_mixed(tmp_path / "m.txt", long_chars=3000)
    data = path.read_bytes()
    built = Rig(PreadSource(path), request.param)
    assert built.doc.long_index(_LONG_ROW).join(10)
    built.install_tripwires(monkeypatch)
    yield built, oracle_row_text(data, _LONG_ROW), data
    built.check_bounded()
    built.doc.close()


def test_long_row_is_long(rig: tuple[Rig, str, bytes]) -> None:
    built, text, _data = rig
    assert built.doc.is_long(_LONG_ROW)
    assert not built.doc.is_long(_LONG_ROW - 1)
    assert built.doc.line_length(_LONG_ROW) == len(text)
    assert built.nav.end_is_known(_LONG_ROW)
    assert built.nav.end_is_known(0)


def test_above_and_below_keep_the_goal_display_column(rig: tuple[Rig, str, bytes]) -> None:
    built, text, data = rig
    width = built.width
    above_text = oracle_row_text(data, _LONG_ROW - 1)
    below_text = oracle_row_text(data, _LONG_ROW + 1)
    count = len(_oracle_offsets(text, width)) + 1
    for column in _sample_columns(text, width):
        section, x = _oracle_section_x(text, width, column)
        location = (_LONG_ROW, column)
        expected_above: Location
        expected_below: Location
        if section > 0:
            expected_above = (_LONG_ROW, _oracle_target(text, width, x, section - 1))
        else:
            expected_above = (_LONG_ROW - 1, _oracle_target(above_text, width, x, -1))
        if section < count - 1:
            expected_below = (_LONG_ROW, _oracle_target(text, width, x, section + 1))
        else:
            expected_below = (_LONG_ROW + 1, _oracle_target(below_text, width, x, 0))
        assert built.nav.get_location_above(location) == expected_above, (width, column)
        assert built.nav.get_location_below(location) == expected_below, (width, column)


def test_above_and_below_honour_the_remembered_x_offset(rig: tuple[Rig, str, bytes]) -> None:
    built, text, _data = rig
    built.nav.last_x_offset = 9
    location = (_LONG_ROW, 3)
    _section, x = _oracle_section_x(text, built.width, 3)
    assert x < 9
    if built.width:
        assert built.nav.get_location_below(location) == (_LONG_ROW, _oracle_target(text, built.width, 9, 1))
    else:
        assert built.nav.get_location_below(location) == (_LONG_ROW + 1, 0)
    assert built.nav.get_location_above(location)[0] == _LONG_ROW - 1


def test_end_and_home(rig: tuple[Rig, str, bytes]) -> None:
    built, text, _data = rig
    width = built.width
    offsets = _oracle_offsets(text, width)
    for column in _sample_columns(text, width):
        section = bisect_right(offsets, column)
        expected_end = offsets[section] - 1 if section < len(offsets) else len(text)
        expected_home = offsets[section - 1] if section else 0
        assert built.nav.get_location_end((_LONG_ROW, column)) == (_LONG_ROW, expected_end), column
        assert built.nav.get_location_home((_LONG_ROW, column)) == (_LONG_ROW, expected_home), column


def test_smart_home_skips_leading_blanks_without_wrapping(rig: tuple[Rig, str, bytes]) -> None:
    built, _text, _data = rig
    if built.width:
        pytest.skip("smart home applies to unwrapped rows")
    # the long row starts with 'a': the first non-blank is column 0
    assert built.nav.get_location_home((_LONG_ROW, 50), smart_home=True) == (_LONG_ROW, 0)
    assert built.nav.get_location_home((_LONG_ROW, 0), smart_home=True) == (_LONG_ROW, 0)


def test_wrapped_line_predicates(rig: tuple[Rig, str, bytes]) -> None:
    built, text, _data = rig
    width = built.width
    offsets = _oracle_offsets(text, width)
    for column in _sample_columns(text, width):
        location = (_LONG_ROW, column)
        assert built.nav.is_start_of_wrapped_line(location) == (column == 0 or column in offsets), column
        assert built.nav.is_end_of_document_line(location) == (column == len(text)), column
        assert built.nav.is_end_of_wrapped_line(location) == (column == len(text) or (column - 1) in offsets), column
        assert not built.nav.is_first_wrapped_line(location)  # not the first document line
        assert not built.nav.is_last_wrapped_line(location)  # not the last document line


def test_predicates_on_a_single_long_row(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    text = oracle_row_text(make_mixed(tmp_path / "m.txt", long_chars=3000).read_bytes(), _LONG_ROW)
    path = tmp_path / "one.txt"
    path.write_bytes(text.encode("utf-8", "surrogateescape"))
    for width in _WIDTHS:
        built = Rig(PreadSource(path), width)
        try:
            assert built.doc.long_index(0).join(10)
            built.install_tripwires(monkeypatch)
            offsets = _oracle_offsets(text, width)
            for column in _sample_columns(text, width):
                location = (0, column)
                section = bisect_right(offsets, column)
                assert built.nav.is_first_wrapped_line(location) == (section == 0), (width, column)
                assert built.nav.is_last_wrapped_line(location) == (section == len(offsets)), (width, column)
            assert built.nav.get_location_above((0, 5)) == (0, 0)
            last = len(text)
            assert built.nav.get_location_below((0, last)) == (0, last)
            assert built.nav.is_end_of_document((0, last))
            assert built.nav.get_location_right((0, last)) == (0, last)
            assert built.nav.clamp_reachable((5, 99)) == (0, 99)
            built.check_bounded()
        finally:
            built.doc.close()


def test_left_right_and_clamp(rig: tuple[Rig, str, bytes]) -> None:
    built, text, data = rig
    nav = built.nav
    assert nav.get_location_left((_LONG_ROW, 10)) == (_LONG_ROW, 9)
    assert nav.get_location_left((_LONG_ROW + 1, 0)) == (_LONG_ROW, len(text))
    assert nav.get_location_right((_LONG_ROW, 10)) == (_LONG_ROW, 11)
    assert nav.get_location_right((_LONG_ROW, len(text))) == (_LONG_ROW + 1, 0)
    assert nav.get_location_left((_LONG_ROW, 0)) == (_LONG_ROW - 1, len(oracle_row_text(data, _LONG_ROW - 1)))
    assert nav.clamp_reachable((_LONG_ROW, 10_000_000)) == (_LONG_ROW, len(text))
    assert nav.clamp_reachable((_LONG_ROW, -5)) == (_LONG_ROW, 0)
    assert nav.clamp_reachable((10_000, 3))[0] == built.doc.line_count - 1


def test_get_location_at_y_offset_moves_by_visual_rows(rig: tuple[Rig, str, bytes]) -> None:
    built, text, _data = rig
    width = built.width
    offsets = _oracle_offsets(text, width)
    for column in (0, 5, len(text) // 2):
        section, x = _oracle_section_x(text, width, column)
        target = built.nav.get_location_at_y_offset((_LONG_ROW, column), 1)
        if width and section < len(offsets):
            assert target == (_LONG_ROW, _oracle_target(text, width, x, section + 1))
        else:
            assert target[0] == _LONG_ROW + 1
    if width:
        assert built.nav.get_location_at_y_offset((_LONG_ROW, len(text) // 2), -1)[0] == _LONG_ROW
        assert built.nav.get_location_at_y_offset((_LONG_ROW, 0), 0) == (_LONG_ROW, 0)


class Frozen:
    """A long row whose scan cannot read anything: every scan dependent value is unknown."""

    def __init__(self, tmp_path: Path, width: int) -> None:
        path = tmp_path / "frozen.txt"
        path.write_bytes(b"first\n" + ("abc\t日é😀" * 300).encode() + b"\nlast\n")
        self.source = GateSource(path)
        self.rig = Rig(self.source, width)
        self.source.gate.clear()
        self.row = 1

    def close(self) -> None:
        self.source.gate.set()
        self.rig.doc.close()


@pytest.mark.parametrize("width", _WIDTHS, ids=["nowrap", "wrap20"])
def test_unknown_frontier_returns_without_waiting(tmp_path: Path, monkeypatch: pytest.MonkeyPatch, width: int) -> None:
    frozen = Frozen(tmp_path, width)
    built, nav, row = frozen.rig, frozen.rig.nav, frozen.row
    try:
        built.install_tripwires(monkeypatch)
        assert built.doc.is_long(row)
        started = time.perf_counter()
        assert not nav.end_is_known(row)
        assert built.doc.long_index(row).frontier().chars == 0
        location = (row, 40)
        # the end of an unscanned row is unknown: the sentinel is the given location
        assert nav.get_location_end(location) == location
        assert not nav.is_end_of_document_line(location)
        assert not nav.is_end_of_wrapped_line(location) or width
        # wrapped: the section start is not scanned yet, home stays put; unwrapped: the row start is always known
        assert nav.get_location_home(location) == (location if width else (row, 0))
        assert nav.get_location_home(location, smart_home=True)[0] == row
        assert nav.clamp_reachable((row, 999)) == (row, 999)
        assert nav.get_location_right(location) == (row, 41)
        assert nav.get_location_left(location) == (row, 39)
        assert nav.get_location_left((row + 1, 0))[0] == row
        assert nav.get_location_above(location)[0] in (row - 1, row)
        assert nav.get_location_below(location)[0] in (row, row + 1)
        assert row <= nav.get_location_at_y_offset(location, 1)[0] < built.doc.line_count
        assert 0 <= nav.get_location_at_y_offset(location, -1)[0] <= row
        assert not nav.is_start_of_wrapped_line(location)
        assert nav.is_first_wrapped_line(location) is False
        assert nav.is_last_wrapped_line(location) is False
        if not width:
            assert nav.get_location_below((row, 0)) == (row + 1, 0)
        # nothing waited for the frozen scan: every call above returned at once
        assert time.perf_counter() - started < 5.0
        assert built.doc.long_index(row).frontier().chars == 0
        built.check_bounded()
    finally:
        frozen.close()


def test_unknown_end_becomes_known_after_the_scan(tmp_path: Path) -> None:
    frozen = Frozen(tmp_path, 0)
    try:
        nav, row = frozen.rig.nav, frozen.row
        assert not nav.end_is_known(row)
        frozen.source.gate.set()
        assert frozen.rig.doc.long_index(row).join(10)
        assert nav.end_is_known(row)
        length = frozen.rig.doc.line_length(row)
        assert length == 300 * len("abc\t日é😀")
        assert nav.get_location_end((row, 40)) == (row, length)
        assert nav.is_end_of_document_line((row, length))
    finally:
        frozen.close()
