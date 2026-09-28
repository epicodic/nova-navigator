"""Tests for LocalCopiesDialog."""

from __future__ import annotations

import tempfile
from pathlib import Path

import pytest
from textual.app import App, ComposeResult

from nova_navigator.dialogs.local_copies_dialog import LocalCopiesDialog
from nova_navigator.local_copies import CopyEntry, CopyStatus, LocalCopyManager
from nova_navigator.response import Response
from nova_navigator.scheduler import Job
from nova_navigator.vfs.filesystems.local import LocalFilesystem
from nova_navigator.vfs.local_copy import Baseline, LocalCopy, SourceFingerprint
from nova_navigator.vfs.vpath import VPath
from nova_widgets import Button, DataTable

_fs = LocalFilesystem.singleton()


async def _noop_start_job(_job: Job) -> None:
    """No-op job starter — the fake manager below never starts real jobs."""


def _fake_copy(name: str, *, read_only: bool = False) -> LocalCopy:
    source = VPath(f"/home/user/{name}", _fs)
    baseline = Baseline(SourceFingerprint(0, 0.0, None), "digest")
    path = Path(tempfile.gettempdir()) / "nn-local-copies-dialog-test" / name
    return LocalCopy(source, path, baseline, read_only=read_only, pass_through=True)


class _FakeLocalCopyManager(LocalCopyManager):
    """Fake manager: fixed entries, records sync_now/close/discard calls instead of running them."""

    def __init__(self, entries: list[CopyEntry]) -> None:
        super().__init__(Path(tempfile.gettempdir()) / "nn-local-copies-dialog-test-root", _noop_start_job)
        for entry in entries:
            self._entries[entry.key] = entry
        self.sync_now_calls: list[CopyEntry] = []
        self.close_calls: list[CopyEntry] = []
        self.discard_calls: list[CopyEntry] = []

    async def sync_now(self, entry: CopyEntry) -> None:
        self.sync_now_calls.append(entry)

    async def close(self, entry: CopyEntry) -> None:
        self.close_calls.append(entry)

    async def discard(self, entry: CopyEntry) -> None:
        self.discard_calls.append(entry)
        self._entries.pop(entry.key, None)


def _three_entries() -> list[CopyEntry]:
    return [
        CopyEntry(copy=_fake_copy("synced.txt"), status=CopyStatus.SYNCED),
        CopyEntry(copy=_fake_copy("conflict.txt"), status=CopyStatus.CONFLICT, error="Source changed."),
        CopyEntry(copy=_fake_copy("readonly.pdf", read_only=True), status=CopyStatus.READ_ONLY),
    ]


def _make_app(dialog: LocalCopiesDialog) -> type[App[None]]:
    class _App(App[None]):
        def compose(self) -> ComposeResult:
            return iter([])

        async def on_mount(self) -> None:
            await self.push_screen(dialog)

    return _App


# ── rows ─────────────────────────────────────────────────────────────────────


@pytest.mark.asyncio
async def test_rows_show_file_source_status_error() -> None:
    entries = _three_entries()
    manager = _FakeLocalCopyManager(entries)
    reopen_calls: list[CopyEntry] = []

    async def reopen(entry: CopyEntry) -> None:
        reopen_calls.append(entry)

    dialog = LocalCopiesDialog(manager, reopen=reopen)
    app = _make_app(dialog)()
    async with app.run_test(size=(120, 40)) as pilot:
        await pilot.pause()
        table = app.screen.query_one("#local_copies_table", DataTable)
        assert table.row_count == 3
        rows = [table.get_row_at(i) for i in range(3)]
        assert rows[0] == ["synced.txt", entries[0].copy.source.uri, "synced", ""]
        assert rows[1] == ["conflict.txt", entries[1].copy.source.uri, "conflict", "Source changed."]
        assert rows[2] == ["readonly.pdf", entries[2].copy.source.uri, "read-only", ""]


# ── sync now ─────────────────────────────────────────────────────────────────


@pytest.mark.asyncio
async def test_sync_now_calls_manager_for_selected_row() -> None:
    entries = _three_entries()
    manager = _FakeLocalCopyManager(entries)

    async def reopen(_entry: CopyEntry) -> None:
        pass

    dialog = LocalCopiesDialog(manager, reopen=reopen)
    app = _make_app(dialog)()
    async with app.run_test(size=(120, 40)) as pilot:
        await pilot.pause()
        table = app.screen.query_one("#local_copies_table", DataTable)
        table.move_cursor(row=1)  # conflict row
        await pilot.pause()
        await pilot.click(app.screen.query_one("#sync_now", Button))
        await pilot.pause()
        assert manager.sync_now_calls == [entries[1]]


@pytest.mark.asyncio
async def test_sync_now_disabled_for_read_only_row() -> None:
    entries = _three_entries()
    manager = _FakeLocalCopyManager(entries)

    async def reopen(_entry: CopyEntry) -> None:
        pass

    dialog = LocalCopiesDialog(manager, reopen=reopen)
    app = _make_app(dialog)()
    async with app.run_test(size=(120, 40)) as pilot:
        await pilot.pause()
        table = app.screen.query_one("#local_copies_table", DataTable)
        table.move_cursor(row=2)  # read-only row
        await pilot.pause()
        sync_button = app.screen.query_one("#sync_now", Button)
        assert sync_button.disabled is True


# ── discard ──────────────────────────────────────────────────────────────────


@pytest.mark.asyncio
async def test_discard_on_modified_entry_asks_then_only_discards_after_confirm() -> None:
    entry = CopyEntry(copy=_fake_copy("draft.txt"), status=CopyStatus.MODIFIED)
    manager = _FakeLocalCopyManager([entry])

    async def reopen(_entry: CopyEntry) -> None:
        pass

    dialog = LocalCopiesDialog(manager, reopen=reopen)
    app = _make_app(dialog)()
    async with app.run_test(size=(120, 40)) as pilot:
        await pilot.pause()
        table = app.screen.query_one("#local_copies_table", DataTable)
        table.move_cursor(row=0)
        await pilot.pause()

        # First attempt: cancel the confirmation — nothing discarded.
        await pilot.click(app.screen.query_one("#discard", Button))
        await pilot.pause()
        cancel_button = app.screen.query_one(f"#{Response.CANCEL.name}", Button)
        await pilot.click(cancel_button)
        await pilot.pause()
        assert manager.discard_calls == []
        assert table.row_count == 1

        # Second attempt: confirm — the entry is discarded.
        await pilot.click(app.screen.query_one("#discard", Button))
        await pilot.pause()
        ok_button = app.screen.query_one(f"#{Response.OK.name}", Button)
        await pilot.click(ok_button)
        await pilot.pause()
        assert manager.discard_calls == [entry]
        assert table.row_count == 0


# ── reopen ───────────────────────────────────────────────────────────────────


@pytest.mark.asyncio
async def test_reopen_calls_reopen_callback() -> None:
    entries = _three_entries()
    manager = _FakeLocalCopyManager(entries)
    reopen_calls: list[CopyEntry] = []

    async def reopen(entry: CopyEntry) -> None:
        reopen_calls.append(entry)

    dialog = LocalCopiesDialog(manager, reopen=reopen)
    app = _make_app(dialog)()
    async with app.run_test(size=(120, 40)) as pilot:
        await pilot.pause()
        table = app.screen.query_one("#local_copies_table", DataTable)
        table.move_cursor(row=0)
        await pilot.pause()
        await pilot.click(app.screen.query_one("#reopen", Button))
        await pilot.pause()
        assert reopen_calls == [entries[0]]


# ── empty state ──────────────────────────────────────────────────────────────


@pytest.mark.asyncio
async def test_empty_state_shows_label_and_disables_actions() -> None:
    manager = _FakeLocalCopyManager([])

    async def reopen(_entry: CopyEntry) -> None:
        pass

    dialog = LocalCopiesDialog(manager, reopen=reopen)
    app = _make_app(dialog)()
    async with app.run_test(size=(120, 40)) as pilot:
        await pilot.pause()
        table = app.screen.query_one("#local_copies_table", DataTable)
        label = app.screen.query_one("#local_copies_empty")
        assert table.display is False
        assert label.display is True
        assert "No local copies." in str(label.render())
        assert app.screen.query_one("#sync_now", Button).disabled is True
        assert app.screen.query_one("#reopen", Button).disabled is True
        assert app.screen.query_one("#close_copy", Button).disabled is True
        assert app.screen.query_one("#discard", Button).disabled is True
