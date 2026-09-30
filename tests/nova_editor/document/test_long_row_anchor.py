"""`LongRowAnchorIndex` against a plain-string reference, and its behaviour before the scan reached a byte."""

from __future__ import annotations

from pathlib import Path

import pytest

from nova_editor.core import ByteSource
from nova_editor.document._cursor_anchor import AnchorIndex, CursorMachine, CursorState, EstimateLengthIndex
from nova_editor.document._lazy_config import LazyConfig
from nova_editor.document._lazy_document import LazyDocument
from nova_editor.document._long_row_anchor import LongRowAnchorIndex
from tests.nova_editor.helpers_view import LOWERED_OPTIONS, GateSource

_UNIT = "ab\t日本語 é é 😀 x".encode()
_CONTENTS = {
    "cjk": "日本語のテキスト、漢字かな交じり文。" * 60,
    "combining": "éäô " * 120,
    "emoji": "😀x🎉y👍🏽z " * 80,
    "tab": "a\tbb\tccc\tdddd\t" * 60,
    "mixed": (_UNIT.decode() + " ") * 40,
}
_STEPS = (-1_000_000, -33, -9, -8, -3, -1, 0, 1, 2, 8, 33, 1_000_000, 1 << 62)


def _as_source(source: GateSource) -> ByteSource:
    return source


def _write(tmp_path: Path, content: bytes) -> Path:
    path = tmp_path / "row.txt"
    path.write_bytes(b"first\n" + content + b"\nlast\n")
    return path


def _open(path: Path) -> tuple[LazyDocument, LongRowAnchorIndex]:
    doc = LazyDocument.from_path(path, LazyConfig(**LOWERED_OPTIONS))
    assert doc.wait_indexed(10)
    assert doc.is_long(1)
    return doc, doc.anchor_index(1)


def _reference(content: bytes) -> tuple[str, list[int]]:
    """The text and the byte offset of every character boundary (`boundaries[i]` starts character i)."""
    text = content.decode("utf-8", "surrogateescape")
    boundaries = [0]
    for char in text:
        boundaries.append(boundaries[-1] + len(char.encode("utf-8", "surrogateescape")))
    return text, boundaries


def _contents() -> list[tuple[str, bytes]]:
    found = [(name, text.encode()) for name, text in _CONTENTS.items()]
    found.append(("invalid", (_UNIT * 10) + b"\xff\xfe" + (_UNIT * 10) + b"\x80" + b"\xe6\x97" + (_UNIT * 8)))
    return found


@pytest.mark.parametrize(("name", "content"), _contents(), ids=[name for name, _ in _contents()])
def test_adapter_matches_plain_string_reference(tmp_path: Path, name: str, content: bytes) -> None:
    assert len(content) > 512, name
    doc, anchor = _open(_write(tmp_path, content))
    try:
        assert doc.long_index(1).join(10)
        text, boundaries = _reference(content)
        protocol: AnchorIndex = anchor  # statically checked: the adapter implements the protocol
        assert protocol is anchor
        assert isinstance(anchor, EstimateLengthIndex)
        assert anchor.complete()
        assert anchor.frontier_byte() == len(content) == anchor.row_end_rel
        assert anchor.estimate_length() == len(text)
        for column, byte in enumerate(boundaries):
            assert anchor.exact_column(byte) == column
        # any byte, also inside a character, maps to the column of its character and never decreases
        previous = 0
        for byte in range(len(content) + 1):
            approx = anchor.approx_column(byte)
            assert approx >= previous
            previous = approx
            assert anchor.exact_column(byte) == approx
        for column in range(0, len(boundaries), 7):
            for chars in _STEPS:
                target = min(max(column + chars, 0), len(text))
                assert anchor.step(boundaries[column], chars) == boundaries[target], (name, column, chars)
        assert anchor.step(0, -5) == 0
        assert anchor.step(len(content), 5) == len(content)
        assert anchor.step(len(content) + 100, -1) == boundaries[-2]
        assert doc.call_log.max_decoded_chars <= 8192
        assert doc.call_log.refusals == []
    finally:
        doc.close()


def test_step_lands_on_boundaries_from_inside_a_character(tmp_path: Path) -> None:
    content = "日本語 é 😀 ".encode() * 60
    doc, anchor = _open(_write(tmp_path, content))
    try:
        _text, boundaries = _reference(content)
        valid = set(boundaries)
        for byte in range(len(content) + 1):
            for chars in (-2, -1, 1, 2):
                assert anchor.step(byte, chars) in valid
    finally:
        doc.close()


def test_exact_column_is_none_beyond_the_frontier_and_nothing_waits(tmp_path: Path) -> None:
    content = _UNIT * 40
    path = _write(tmp_path, content)
    source = GateSource(path)
    doc = LazyDocument(_as_source(source), LazyConfig(**LOWERED_OPTIONS))
    try:
        assert doc.wait_indexed(10)
        source.gate.clear()  # the scan of the long row cannot read anything from now on
        anchor = doc.anchor_index(1)
        _text, boundaries = _reference(content)
        assert not anchor.complete()
        assert anchor.frontier_byte() == 0
        assert anchor.exact_column(0) == 0
        assert anchor.exact_column(boundaries[10]) is None
        assert anchor.approx_column(boundaries[10]) >= 0  # an estimate, never a wait
        assert anchor.approx_column(boundaries[20]) >= anchor.approx_column(boundaries[10])
        assert anchor.step(boundaries[10], 3) == boundaries[13]
        assert anchor.step(boundaries[10], -3) == boundaries[7]
        assert anchor.estimate_length() >= 0
        machine = CursorMachine(1, anchor, boundaries[10])
        assert machine.state is CursorState.PROVISIONAL
        source.gate.set()
        assert doc.long_index(1).join(10)
        assert anchor.exact_column(boundaries[10]) == 10
        assert machine.on_frontier()
        assert machine.state is CursorState.RESOLVED
    finally:
        source.gate.set()
        doc.close()
