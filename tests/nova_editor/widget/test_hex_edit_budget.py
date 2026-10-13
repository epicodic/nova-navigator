"""REQ-12 and REQ-21 for hex edits: a one-byte edit on an 8 GiB sparse file needs no scan and one bounded read (S0003 ACT5 commit 4).

Skipped when the file system has no sparse files.
"""

from __future__ import annotations

import time
from pathlib import Path
from unittest.mock import patch

import pytest

from nova_editor.widget import HexView
from nova_editor.widget.hex_view import HexCursor
from tests.nova_editor.widget.hex_helpers import SPARSE_SIZE, CountingSource, HexHost, ReadLog, make_sparse, row_texts, visible_height
from tests.nova_editor.widget.test_hex_view_budget import BPR, SIZE, budget_view

TIME_LIMIT = 1.0
"""Seconds one edit and its repaint may take."""


def check_edit_budget(view: HexView, source: CountingSource, log: ReadLog, calls_before: int, reads_before: int) -> None:
    """At most one `read_bytes` call of at most three screens since `calls_before`; the source sees that read plus the splice's neighbour bytes.

    The splice reads the byte before and the byte after the edit to take a CR/LF half along (a few single-byte reads), so the source sees
    a small constant number of tiny reads besides the repaint.
    """
    calls = log.calls[calls_before:]
    limit = 3 * visible_height(view) * view.hex_layout.bytes_per_row
    assert len(calls) <= 1, calls
    assert all(size <= limit for _, size in calls), calls
    reads = source.reads[reads_before:]
    assert all(size <= limit for _, size in reads), reads
    assert len(reads) <= 16, reads  # a constant number of tiny neighbour reads, never a scan
    assert sum(size for _, size in reads) <= limit + 64, reads


@pytest.mark.asyncio
async def test_one_byte_edits_at_the_end_of_an_unscanned_8_gib_file(tmp_path: Path) -> None:
    with budget_view(make_sparse(tmp_path)) as (document, source, log, view):
        host = HexHost(view)
        with patch.object(host, "bell") as bell, patch.object(host, "notify") as notify:
            async with host.run_test(size=SIZE) as pilot:
                await pilot.pause()
                row_texts(view)
                assert view.hex_layout.bytes_per_row == BPR

                async def operation(*keys: str) -> None:
                    calls_before, reads_before = len(log.calls), len(source.reads)
                    started = time.monotonic()
                    await pilot.press(*keys)
                    await pilot.pause()
                    row_texts(view)
                    assert time.monotonic() - started < TIME_LIMIT
                    check_edit_budget(view, source, log, calls_before, reads_before)
                    bell.assert_not_called()
                    notify.assert_not_called()

                await operation("ctrl+end")
                assert view.cursor == HexCursor(SPARSE_SIZE, 0, "hex")
                await operation("4")  # appends X0: the file grows by one byte
                assert document.length == SPARSE_SIZE + 1
                assert view.cursor == HexCursor(SPARSE_SIZE, 1, "hex")
                await operation("1")  # the low digit completes the new last byte
                assert document.length == SPARSE_SIZE + 1
                assert view.cursor == HexCursor(SPARSE_SIZE + 1, 0, "hex")
                await operation("left", "left")
                await operation("7")  # overwrites the high digit of the old last byte
                await operation("up", "home")
                await operation("insert")
                await operation("2")  # inserts 20 at the start of the row above the last one
                assert document.length == SPARSE_SIZE + 2
                await operation("delete")  # removes the byte under the cursor again
                assert document.length == SPARSE_SIZE + 1
                tail = document.read_bytes(SPARSE_SIZE - 1, 2)
                assert tail == bytes([0x00, 0x71])
                await operation("ctrl+z")  # undo the delete
                assert document.length == SPARSE_SIZE + 2
                await operation("ctrl+z")  # the insert
                await operation("ctrl+z")  # the overwrite digit (one batch each)
                assert document.length == SPARSE_SIZE + 1
                assert document.read_bytes(SPARSE_SIZE - 1, 2) == bytes([0x00, 0x41])
