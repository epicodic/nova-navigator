"""`HexView.byte_at_cursor`: the byte under the cursor for the hex status line, read from the window (S0003 ACT7 commit 3, REQ-19, REQ-21)."""

from __future__ import annotations

from pathlib import Path

import pytest

from nova_editor.core import SourceChanged
from nova_editor.widget import HexView
from tests.nova_editor.widget.hex_helpers import CountingSource, HexHost, logged_reads, opened, write_file

SIZE = (80, 24)
PATTERN = bytes(range(256)) * 4096
"""1 MiB whose byte at offset `n` is `n % 256`."""


@pytest.mark.asyncio
async def test_byte_at_cursor_returns_the_byte(tmp_path: Path) -> None:
    with opened(write_file(tmp_path, PATTERN)) as document:
        view = HexView(lambda: document)
        async with HexHost(view).run_test(size=SIZE) as pilot:
            await pilot.pause()
            assert view.byte_at_cursor() == 0
            view.set_cursor(0x41)
            assert view.byte_at_cursor() == 0x41
            view.set_cursor(0xFF)
            assert view.byte_at_cursor() == 0xFF


@pytest.mark.asyncio
async def test_none_at_the_end_position(tmp_path: Path) -> None:
    with opened(write_file(tmp_path, b"abc")) as document:
        view = HexView(lambda: document)
        async with HexHost(view).run_test(size=SIZE) as pilot:
            await pilot.pause()
            view.set_cursor(3)
            assert view.cursor.offset == 3
            assert view.byte_at_cursor() is None


@pytest.mark.asyncio
async def test_none_for_an_empty_document(tmp_path: Path) -> None:
    with opened(write_file(tmp_path, b"")) as document:
        view = HexView(lambda: document)
        async with HexHost(view).run_test(size=SIZE) as pilot:
            await pilot.pause()
            assert view.byte_at_cursor() is None


@pytest.mark.asyncio
async def test_inside_the_window_costs_no_read(tmp_path: Path) -> None:
    with opened(write_file(tmp_path, PATTERN)) as document:
        view = HexView(lambda: document)
        async with HexHost(view).run_test(size=SIZE) as pilot:
            await pilot.pause()
            with logged_reads(document) as log:
                view.set_cursor(0x41)
                assert view.byte_at_cursor() == 0x41
                assert log.calls == []


@pytest.mark.asyncio
async def test_a_far_cursor_costs_one_window_read(tmp_path: Path) -> None:
    with opened(write_file(tmp_path, PATTERN)) as document:
        view = HexView(lambda: document)
        async with HexHost(view).run_test(size=SIZE) as pilot:
            await pilot.pause()
            far = 900_001
            with logged_reads(document) as log:
                view.set_cursor(far)
                assert view.byte_at_cursor() == far % 256
                assert len(log.calls) == 1
                limit = 3 * view.scrollable_content_region.height * view.hex_layout.bytes_per_row
                assert log.calls[0][1] <= limit
                assert view.byte_at_cursor() == far % 256
                assert len(log.calls) == 1


@pytest.mark.asyncio
async def test_a_source_change_gives_none_and_reports_once(tmp_path: Path) -> None:
    source = CountingSource(write_file(tmp_path, PATTERN), fail_after=None)
    with opened(source) as document:
        reports: list[SourceChanged] = []
        view = HexView(lambda: document)
        view.on_source_changed = reports.append
        async with HexHost(view).run_test(size=SIZE) as pilot:
            await pilot.pause()
            source.fail_after = len(source.reads)
            view.set_cursor(900_001)
            assert view.byte_at_cursor() is None
            assert view.byte_at_cursor() is None
            assert len(reports) == 1


@pytest.mark.asyncio
async def test_follows_an_edit_revision(tmp_path: Path) -> None:
    with opened(write_file(tmp_path, bytes(16))) as document:
        view = HexView(lambda: document)
        async with HexHost(view).run_test(size=SIZE) as pilot:
            await pilot.pause()
            view.focus()
            await pilot.press("4", "2")
            await pilot.pause()
            assert view.cursor.offset == 1
            assert view.byte_at_cursor() == 0
            view.set_cursor(0)
            assert view.byte_at_cursor() == 0x42
