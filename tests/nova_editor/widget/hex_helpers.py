"""Shared helpers of the hex view tests: a host app, documents over real files, read counting and forbidden row APIs."""

from __future__ import annotations

import os
from collections.abc import Iterator
from contextlib import contextmanager
from pathlib import Path
from unittest.mock import MagicMock, patch

import pytest
from textual.app import App, ComposeResult

from nova_editor.core import LineSnapshot, PreadSource, RowRange, SourceChanged
from nova_editor.core.byte_source import BLOCK_SIZE
from nova_editor.document import LazyDocument
from nova_editor.widget import HexView

SPARSE_SIZE = 8 * 1024**3
"""Length of the sparse file of the budget tests."""
MIB = 1024 * 1024


class HexHost(App[None]):
    """Minimal app that hosts one `HexView` and records its `Moved` messages."""

    def __init__(self, view: HexView) -> None:
        super().__init__()
        self.view = view
        """The hosted view."""
        self.moved: list[HexView.Moved] = []
        """The `Moved` messages of the view, in arrival order."""
        self.edited = 0
        """Count of the `Edited` messages of the view."""
        self.modes: list[bool] = []
        """The `insert` flag of every `ModeChanged` message of the view, in arrival order."""

    def compose(self) -> ComposeResult:
        yield self.view

    def on_hex_view_moved(self, message: HexView.Moved) -> None:
        self.moved.append(message)

    def on_hex_view_edited(self, message: HexView.Edited) -> None:
        self.edited += 1

    def on_hex_view_mode_changed(self, message: HexView.ModeChanged) -> None:
        self.modes.append(message.insert)


@contextmanager
def opened(source: Path | PreadSource, *, autostart: bool = True) -> Iterator[LazyDocument]:
    """Open a `LazyDocument` over `source` and close it (and wait for the closer) at the end."""
    document = LazyDocument.from_path(source) if isinstance(source, Path) else LazyDocument(source, autostart=autostart)
    try:
        yield document
    finally:
        document.close()
        assert document.wait_closed(10.0)


def write_file(tmp_path: Path, data: bytes, name: str = "data.bin") -> Path:
    """Write `data` to a file in `tmp_path` and return its path."""
    path = tmp_path / name
    path.write_bytes(data)
    return path


def row_texts(view: HexView) -> list[str]:
    """Return the text of every visible line of `view`."""
    return [view.render_line(y).text for y in range(view.scrollable_content_region.height)]


def visible_height(view: HexView) -> int:
    """Return the number of rows the view shows."""
    return view.scrollable_content_region.height


def make_sparse(tmp_path: Path) -> Path:
    """Create an 8 GiB sparse file of zero bytes; skip the test when the file system has no sparse files."""
    path = tmp_path / "sparse.bin"
    with open(path, "wb") as handle:
        handle.truncate(SPARSE_SIZE)
    if os.stat(path).st_blocks * 512 > 16 * MIB:
        path.unlink()
        pytest.skip("the file system does not support sparse files")
    return path


class CountingSource(PreadSource):
    """A `PreadSource` that records every `read` and can fail with `SourceChanged` after a number of reads."""

    def __init__(self, path: Path, *, fail_after: int | None = None) -> None:
        super().__init__(path)
        self.reads: list[tuple[int, int]] = []
        """`(offset, size)` of every read, in order."""
        self.fail_after = fail_after
        """Reads that succeed before every further read raises `SourceChanged`; `None` never fails."""

    def read(self, offset: int, size: int, *, cache: bool = True) -> bytes:
        if self.fail_after is not None and len(self.reads) >= self.fail_after:
            self.reads.append((offset, size))
            raise SourceChanged("file changed")
        self.reads.append((offset, size))
        return super().read(offset, size, cache=cache)

    def blocks(self, start: int = 0) -> int:
        """Return the number of source blocks the reads from index `start` on span."""
        total = 0
        for offset, size in self.reads[start:]:
            if size > 0:
                total += (offset + size - 1) // BLOCK_SIZE - offset // BLOCK_SIZE + 1
        return total


class _Forbidden(LazyDocument):
    """A `LazyDocument` whose row and line APIs fail the test (REQ-21: the hex view reads only length and bytes)."""

    def snapshot(self) -> LineSnapshot:
        raise AssertionError("snapshot")

    def row_at_offset(self, offset: int) -> tuple[int, RowRange] | None:
        raise AssertionError("row_at_offset")

    @property
    def line_count(self) -> int:
        raise AssertionError("line_count")

    def get_line(self, index: int) -> str:
        raise AssertionError("get_line")

    def byte_offset(self, row: int, column: int) -> int | None:
        raise AssertionError("byte_offset")

    def line_length(self, row: int) -> int | None:
        raise AssertionError("line_length")


def forbid(document: LazyDocument) -> None:
    """Make every row and line API of `document` raise `AssertionError`."""
    document.__class__ = _Forbidden


class ReadLog:
    """The `(offset, size)` of every `document.read_bytes` call while `logged_reads` is active."""

    def __init__(self, spy: MagicMock) -> None:
        self._spy = spy

    @property
    def calls(self) -> list[tuple[int, int]]:
        """`(offset, size)` of every call, in order."""
        return [(call.args[0], call.args[1]) for call in self._spy.call_args_list]


@contextmanager
def logged_reads(document: LazyDocument) -> Iterator[ReadLog]:
    """Record the `read_bytes` calls of `document` (they still read)."""
    with patch.object(document, "read_bytes", wraps=document.read_bytes) as spy:
        yield ReadLog(spy)
