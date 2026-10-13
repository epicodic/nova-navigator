"""REQ-21 for hex search and offset goto: goto and byte search near the ends of an 8 GiB sparse file stay within budget (S0003 ACT6 commit 4).

Skipped when the file system has no sparse files.
"""

from __future__ import annotations

import ast
import threading
import time
from pathlib import Path

import pytest

from nova_editor import goto_popup
from nova_editor.core.byte_source import BLOCK_SIZE
from nova_editor.core.search import CHUNK, SearchCancelled, SearchJob, SearchProgress, SearchResult, SearchSpec
from nova_editor.goto_popup import GotoTarget, hex_goto
from nova_editor.widget import hex_view
from nova_editor.widget.hex_view import HexCursor
from tests.nova_editor.widget.hex_helpers import SPARSE_SIZE, CountingSource, HexHost, forbid, make_sparse, opened, row_texts
from tests.nova_editor.widget.test_hex_view_budget import FORBIDDEN, SIZE, budget_view, check_budget, cursor_visible

NEEDLE = bytes.fromhex("DEADBEEF")
AT = SPARSE_SIZE - 100
"""Where the needle sits: 100 bytes before the end."""
CHUNK_BLOCKS = CHUNK // BLOCK_SIZE


def sparse_with_needle(tmp_path: Path) -> Path:
    path = make_sparse(tmp_path)
    with open(path, "r+b") as handle:
        handle.seek(AT)
        handle.write(NEEDLE)
    return path


@pytest.mark.asyncio
@pytest.mark.parametrize("where", ["start", "middle", "last_byte", "length"])
async def test_goto_to_any_offset_including_the_length_reads_one_window(tmp_path: Path, where: str) -> None:
    offsets = {"start": 0, "middle": SPARSE_SIZE // 2, "last_byte": SPARSE_SIZE - 1, "length": SPARSE_SIZE}
    offset = offsets[where]
    with budget_view(make_sparse(tmp_path)) as (document, source, log, view):
        async with HexHost(view).run_test(size=SIZE) as pilot:
            await pilot.pause()
            row_texts(view)
            calls_before, reads_before = len(log.calls), len(source.reads)
            placed = hex_goto(GotoTarget("byte", offset), document.length)
            assert isinstance(placed, int)
            assert placed == offset
            view.goto(placed)
            await pilot.pause()
            row_texts(view)
            assert view.cursor == HexCursor(offset, 0, "hex")
            assert cursor_visible(view)
            check_budget(view, source, log, calls_before, reads_before)


def test_goto_beyond_the_length_is_rejected_without_reading(tmp_path: Path) -> None:
    with budget_view(make_sparse(tmp_path)) as (document, source, log, _):
        reads_before = len(source.reads)
        message = hex_goto(GotoTarget("byte", SPARSE_SIZE + 1), document.length)
        assert message == f"byte offset {SPARSE_SIZE + 1} is beyond the end of the file ({SPARSE_SIZE} bytes)"
        assert len(source.reads) == reads_before
        assert log.calls == []


def test_forward_byte_search_near_the_end_reads_two_windows_at_most(tmp_path: Path) -> None:
    source = CountingSource(sparse_with_needle(tmp_path))
    with opened(source, autostart=False) as document:
        forbid(document)
        before = len(source.reads)
        started = time.monotonic()
        result = SearchJob(document, SearchSpec(NEEDLE, wrap=False), SPARSE_SIZE - 1000).run()
        assert result == SearchResult(AT, AT + len(NEEDLE), False)
        assert time.monotonic() - started < 2.0
        assert source.blocks(before) <= 2 * CHUNK_BLOCKS


def test_backward_byte_search_from_the_end_reads_one_unit(tmp_path: Path) -> None:
    source = CountingSource(sparse_with_needle(tmp_path))
    with opened(source, autostart=False) as document:
        forbid(document)
        before = len(source.reads)
        result = SearchJob(document, SearchSpec(NEEDLE, backward=True, wrap=False), SPARSE_SIZE).run()
        assert result == SearchResult(AT, AT + len(NEEDLE), False)
        assert source.blocks(before) <= CHUNK_BLOCKS + 1


def test_cancel_of_a_running_byte_search_on_the_sparse_file(tmp_path: Path) -> None:
    source = CountingSource(make_sparse(tmp_path))
    with opened(source, autostart=False) as document:
        forbid(document)
        before = len(source.reads)
        seen = threading.Event()
        holder: list[SearchJob] = []

        def progress(_info: SearchProgress) -> None:
            seen.set()
            holder[0].cancel()

        job = SearchJob(document, SearchSpec(NEEDLE, wrap=False), 0, progress=progress)
        holder.append(job)
        with pytest.raises(SearchCancelled):
            job.run()
        assert seen.is_set()
        assert source.blocks(before) <= 4 * CHUNK_BLOCKS


def test_the_hex_search_and_goto_sources_use_no_row_apis() -> None:
    for module in (hex_view, goto_popup):
        assert module.__file__ is not None
        tree = ast.parse(Path(module.__file__).read_text())
        for node in ast.walk(tree):
            if isinstance(node, ast.Attribute):
                assert node.attr not in FORBIDDEN, (module.__name__, node.attr, node.lineno)
            if isinstance(node, ast.Name):
                assert node.id not in FORBIDDEN, (module.__name__, node.id, node.lineno)
