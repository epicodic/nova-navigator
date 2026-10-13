"""Closing the widget while an in-place save runs (review findings F2 and F5); every file lives in `tmp_path`."""

from __future__ import annotations

import threading
from pathlib import Path

import pytest

from nova_editor.document._lazy_document import LazyDocument
from nova_editor.widget import NovaTextArea
from tests.nova_editor.save_widget_helpers import SETTINGS, Gate, SaveHost
from tests.nova_editor.test_shared_history_widget import hex_edit

BODY = "".join(f"row {n} {'x' * (n % 9)}\n" for n in range(60)).encode()
NEW = b"y" * 25  # four chunks of seven bytes


@pytest.fixture(autouse=True)
def small_chunks(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(NovaTextArea, "save_settings", SETTINGS)


@pytest.fixture
def thread_errors(monkeypatch: pytest.MonkeyPatch) -> list[threading.ExceptHookArgs]:
    found: list[threading.ExceptHookArgs] = []
    monkeypatch.setattr(threading, "excepthook", found.append)
    return found


def _patched(data: bytes) -> bytes:
    return NEW + data[len(NEW) :]


@pytest.mark.asyncio
@pytest.mark.parametrize("gated_pwrite", [1, 2], ids=["before_first_pwrite", "after_first_pwrite"])
async def test_close_during_a_multi_chunk_patch_completes_it_and_reports_partial(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, thread_errors: list[threading.ExceptHookArgs], gated_pwrite: int
) -> None:
    gate = Gate()
    monkeypatch.setattr(NovaTextArea, "save_io", gate.inplace_io("pwrite", at=gated_pwrite))
    path = tmp_path / "doc.txt"
    path.write_bytes(BODY)
    area = NovaTextArea.open(path)
    host = SaveHost(area)
    async with host.run_test() as pilot:
        await pilot.pause()
        document = area.document
        assert isinstance(document, LazyDocument)
        hex_edit(document, 0, len(NEW), NEW)
        assert area.save() is True
        assert gate.reached.wait(10)
        area.close()
        assert not document.wait_closed(0.2), "the source must stay open while the writer runs"
        gate.release()
        assert document.wait_closed(10)
        await pilot.pause(0.1)
        (message,) = host.terminals()
        assert isinstance(message, NovaTextArea.SaveFailed), message
        assert message.partial is True
        assert not [thread for thread in threading.enumerate() if thread.name == "nova-save" and thread.is_alive()]
    assert path.read_bytes() == _patched(BODY)  # fully patched, never left half done
    assert thread_errors == []


@pytest.mark.asyncio
async def test_close_before_the_save_thread_built_its_job_leaves_no_thread_error(tmp_path: Path, monkeypatch: pytest.MonkeyPatch, thread_errors: list[threading.ExceptHookArgs]) -> None:
    held = threading.Event()
    entered = threading.Event()
    real_plan = LazyDocument.plan_in_place

    def slow_plan(self: LazyDocument) -> object:
        entered.set()
        assert held.wait(10)
        return real_plan(self)

    monkeypatch.setattr(LazyDocument, "plan_in_place", slow_plan)
    path = tmp_path / "doc.txt"
    path.write_bytes(BODY)
    area = NovaTextArea.open(path)
    host = SaveHost(area)
    async with host.run_test() as pilot:
        await pilot.pause()
        document = area.document
        assert isinstance(document, LazyDocument)
        hex_edit(document, 0, len(NEW), NEW)
        assert area.save() is True
        assert entered.wait(10)
        area.close()
        held.set()
        assert document.wait_closed(10)
        await pilot.pause(0.1)
        assert [type(message) for message in host.terminals()] == [NovaTextArea.SaveCancelled]
    assert path.read_bytes() == BODY
    assert thread_errors == []
