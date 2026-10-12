"""Pilot tests of the text view working through the history that belongs to the document (S0003 ACT2 task 4)."""

from __future__ import annotations

from pathlib import Path
from unittest.mock import patch

import pytest

from nova_editor.widget import NovaTextArea
from tests.nova_editor.helpers_view import HostApp
from tests.nova_editor.save_widget_helpers import SaveHost, wait_saved

BODY = "".join(f"row {n}\n" for n in range(20))


@pytest.mark.asyncio
async def test_history_is_the_documents_history() -> None:
    area = NovaTextArea("one")
    host = HostApp(area)
    async with host.run_test() as pilot:
        await pilot.pause()
        assert area.history is area.document.history
        area.load_text("two")
        assert area.history is area.document.history
    assert NovaTextArea(max_checkpoints=3).history.max_checkpoints == 3


@pytest.mark.asyncio
async def test_max_checkpoints_survives_a_new_document() -> None:
    area = NovaTextArea("one", max_checkpoints=3)
    host = HostApp(area)
    async with host.run_test() as pilot:
        await pilot.pause()
        area.load_text("two")
        assert area.history.max_checkpoints == 3


@pytest.mark.asyncio
async def test_undo_and_redo_go_through_the_shared_history() -> None:
    area = NovaTextArea("abc")
    host = HostApp(area)
    async with host.run_test() as pilot:
        await pilot.pause()
        area.insert("x", (0, 0))
        history = area.document.history
        with patch.object(history, "undo", wraps=history.undo) as undo_spy:
            area.undo()
        assert undo_spy.call_count == 1
        with patch.object(history, "redo", wraps=history.redo) as redo_spy:
            area.redo()
        assert redo_spy.call_count == 1
        assert area.text == "xabc"


@pytest.mark.asyncio
async def test_undo_after_save_and_modified_follow_the_shared_flag(tmp_path: Path) -> None:
    path = tmp_path / "doc.txt"
    path.write_text(BODY)
    area = NovaTextArea.open(path)
    host = SaveHost(area)
    async with host.run_test() as pilot:
        await pilot.pause()
        area.focus()
        await pilot.press("x", "y")
        assert area.modified == area.document.history.modified is True
        assert area.save() is True
        await wait_saved(pilot, area)
        assert area.modified == area.document.history.modified is False
        area.undo()
        assert area.modified is True
        assert area.modified == area.document.history.modified
        area.redo()
        assert area.modified == area.document.history.modified is False


@pytest.mark.asyncio
async def test_references_roundtrip_through_the_history_api() -> None:
    area = NovaTextArea("hello world\nsecond")
    host = HostApp(area)
    async with host.run_test() as pilot:
        await pilot.pause()
        area.delete((0, 0), (0, 6))
        area.select_all()
        area.action_copy()
        record = area._clipboard_record
        assert record is not None
        reachable = area._reachable_contents()
        assert reachable == [*area.history.references(), record.content]
        area._apply_translated(list(reachable))
        assert area._reachable_contents() == reachable
