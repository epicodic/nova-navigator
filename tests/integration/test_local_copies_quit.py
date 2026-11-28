"""GUI tests for the quit prompt shown when local copies have unsynced changes (Task 16).

``NovaNavigator.request_quit()`` is exercised through the real running app (see
``app_ctx`` in conftest.py) with ``app.local_copies`` swapped for a small fake that
scripts ``unsynced()`` and records ``sync_now()``/``shutdown()`` calls -- building a
whole separate minimal ``App`` subclass would only duplicate ``NovaNavigatorCore``'s
setup (config, icons, scheme registration) that the real fixture already exercises,
without testing anything ``request_quit()`` itself does differently.

Quitting is triggered by calling ``MainScreen._action_quit()`` directly, which is
exactly what the Ctrl+Q binding, the "Quit" menu item, and any other action lookup of
``app.quit`` funnel into (see ``nova_navigator.py``) -- it just schedules
``app.request_quit()`` as a worker.
"""

from __future__ import annotations

import asyncio
import tempfile
import threading
from collections.abc import AsyncIterator
from dataclasses import dataclass
from pathlib import Path

import pytest
import pytest_asyncio
from textual import widgets
from textual.pilot import Pilot

from nova_navigator import nova_navigator as nova_navigator_module
from nova_navigator.config import loader
from nova_navigator.dialogs import WriteBackRunningDialog
from nova_navigator.local_copies import CopyEntry, CopyStatus, LocalCopyManager
from nova_navigator.nova_navigator import NovaNavigator
from nova_navigator.scheduler import Job
from nova_navigator.vfs.filesystems.local import LocalFilesystem
from nova_navigator.vfs.local_copy import Baseline, LocalCopy, ProgressCallback, SourceFingerprint
from nova_navigator.vfs.vpath import VPath
from nova_widgets import MessageBox
from tests._utils.local_copy_helpers import SchemeFs, overwrite
from tests.integration.conftest import AppCtx, poll_until

_ROOT = Path(tempfile.gettempdir()) / "nn-local-copies-quit-test"


async def _no_jobs(_job: Job) -> None:
    """The fake manager never starts jobs."""


def _entry() -> CopyEntry:
    source = VPath("/home/user/report.txt", LocalFilesystem.singleton())
    baseline = Baseline(SourceFingerprint(0, 0.0, None), "digest")
    copy = LocalCopy(source, _ROOT / "report.txt", baseline, read_only=False, pass_through=True)
    return CopyEntry(copy=copy, status=CopyStatus.MODIFIED)


class _FakeLocalCopyManager(LocalCopyManager):
    """LocalCopyManager with a scripted unsynced() list that records sync_now()/shutdown() calls."""

    def __init__(self, pending: list[CopyEntry], *, sync_succeeds: bool = True) -> None:
        super().__init__(_ROOT, _no_jobs)
        self._pending = pending
        self._sync_succeeds = sync_succeeds
        self.synced: list[CopyEntry] = []
        self.shutdown_calls: list[bool] = []

    def unsynced(self) -> list[CopyEntry]:
        if self.synced and self._sync_succeeds:
            return []
        return self._pending

    async def sync_now(self, entry: CopyEntry) -> None:
        self.synced.append(entry)

    async def shutdown(self, remove_files: bool = True) -> None:
        self.shutdown_calls.append(remove_files)


@pytest.mark.asyncio
@pytest.mark.integration
async def test_quit_with_no_unsynced_copies_exits_directly(app_ctx: AppCtx) -> None:
    fake = _FakeLocalCopyManager(pending=[])
    app_ctx.app.local_copies = fake

    app_ctx.screen._action_quit()
    await app_ctx.pilot.pause()

    assert not app_ctx.app.is_running
    assert fake.shutdown_calls == [True]


@pytest.mark.asyncio
@pytest.mark.integration
async def test_quit_prompt_cancel_keeps_app_running(app_ctx: AppCtx) -> None:
    fake = _FakeLocalCopyManager(pending=[_entry()])
    app_ctx.app.local_copies = fake

    app_ctx.screen._action_quit()
    await app_ctx.pilot.pause()
    assert app_ctx.app.screen.query_one("#CANCEL", widgets.Button) is not None

    await app_ctx.pilot.press("escape")
    await app_ctx.pilot.pause()

    assert app_ctx.app.is_running
    assert fake.shutdown_calls == []


@pytest.mark.asyncio
@pytest.mark.integration
async def test_quit_prompt_keep_files_shuts_down_without_removing_files(app_ctx: AppCtx) -> None:
    fake = _FakeLocalCopyManager(pending=[_entry()])
    app_ctx.app.local_copies = fake

    app_ctx.screen._action_quit()
    await app_ctx.pilot.pause()

    await app_ctx.pilot.click("#IGNORE")
    await app_ctx.pilot.pause()

    assert not app_ctx.app.is_running
    assert fake.shutdown_calls == [False]


@pytest.mark.asyncio
@pytest.mark.integration
async def test_quit_prompt_sync_and_quit_shuts_down_and_removes_files(app_ctx: AppCtx) -> None:
    pending = _entry()
    fake = _FakeLocalCopyManager(pending=[pending])
    app_ctx.app.local_copies = fake

    app_ctx.screen._action_quit()
    await app_ctx.pilot.pause()

    await app_ctx.pilot.click("#SAVE")
    await app_ctx.pilot.pause()

    assert fake.synced == [pending]
    assert not app_ctx.app.is_running
    assert fake.shutdown_calls == [True]


@pytest.mark.asyncio
@pytest.mark.integration
async def test_quit_prompt_sync_failure_keeps_app_running(app_ctx: AppCtx) -> None:
    pending = _entry()
    fake = _FakeLocalCopyManager(pending=[pending], sync_succeeds=False)
    app_ctx.app.local_copies = fake

    app_ctx.screen._action_quit()
    await app_ctx.pilot.pause()

    await app_ctx.pilot.click("#SAVE")
    await app_ctx.pilot.pause()

    assert fake.synced == [pending]
    assert app_ctx.app.is_running
    assert fake.shutdown_calls == []


async def _press_all(app_ctx: AppCtx, keys: list[str]) -> None:
    for key in keys:
        await app_ctx.pilot.press(key)
        await app_ctx.pilot.pause()


def _quit_prompts(app: NovaNavigator) -> list[MessageBox]:
    return [screen for screen in app.screen_stack if isinstance(screen, MessageBox)]


def _assert_asking(app: NovaNavigator, fake: _FakeLocalCopyManager) -> None:
    assert app.is_running
    assert fake.shutdown_calls == []
    assert len(_quit_prompts(app)) == 1
    assert len(app.screen.query("#IGNORE")) == 1


_STATES = {
    "settings dialog": ["ctrl+f1"],
    "local copies dialog": ["ctrl+e"],
    "help": ["f1"],
    "new directory dialog": ["f7"],
    "quit prompt itself": ["ctrl+q"],
    "pending ctrl+s": ["ctrl+s"],
    "pending ctrl+t": ["ctrl+t"],
}


@pytest.mark.asyncio
@pytest.mark.integration
@pytest.mark.parametrize("keys", _STATES.values(), ids=_STATES.keys())
async def test_ctrl_q_asks_in_every_state(app_ctx: AppCtx, keys: list[str]) -> None:
    fake = _FakeLocalCopyManager(pending=[_entry()])
    app_ctx.app.local_copies = fake
    await _press_all(app_ctx, keys)

    await _press_all(app_ctx, ["ctrl+q"])

    _assert_asking(app_ctx.app, fake)


@pytest.mark.asyncio
@pytest.mark.integration
@pytest.mark.parametrize("binding", ["ctrl+k", "ctrl+x ctrl+c"])
@pytest.mark.parametrize("keys", [["ctrl+q"], ["ctrl+f1", "ctrl+q"]], ids=["panel", "dialog"])
async def test_ctrl_q_asks_with_quit_rebound(tmp_path: Path, monkeypatch: pytest.MonkeyPatch, binding: str, keys: list[str]) -> None:
    config = tmp_path / "config"
    config.mkdir()
    (config / "keybindings.toml").write_text(f'[bindings]\n"app.quit" = "{binding}"\n')
    monkeypatch.setattr(loader, "_APP_CONFIG_DIR", config)
    app = NovaNavigator(config_dir=config)
    async with app.run_test(size=(120, 40)) as pilot:
        await pilot.pause()
        fake = _FakeLocalCopyManager(pending=[_entry()])
        app.local_copies = fake

        for key in keys:
            await pilot.press(key)
            await pilot.pause()

        _assert_asking(app, fake)


@pytest.mark.asyncio
@pytest.mark.integration
async def test_cancel_over_a_dialog_returns_to_that_dialog(app_ctx: AppCtx) -> None:
    fake = _FakeLocalCopyManager(pending=[_entry()])
    app_ctx.app.local_copies = fake
    await _press_all(app_ctx, ["ctrl+f1"])
    dialog = app_ctx.app.screen

    await _press_all(app_ctx, ["ctrl+q", "escape"])

    assert app_ctx.app.is_running
    assert app_ctx.app.screen is dialog
    assert fake.shutdown_calls == []

    await _press_all(app_ctx, ["ctrl+q"])
    await app_ctx.pilot.click("#IGNORE")
    await app_ctx.pilot.pause()

    assert not app_ctx.app.is_running
    assert fake.shutdown_calls == [False]


@pytest.mark.asyncio
@pytest.mark.integration
async def test_ctrl_q_with_a_dialog_open_and_nothing_unsynced_exits(app_ctx: AppCtx) -> None:
    fake = _FakeLocalCopyManager(pending=[])
    app_ctx.app.local_copies = fake
    await _press_all(app_ctx, ["ctrl+f1"])

    await app_ctx.pilot.press("ctrl+q")
    await poll_until(app_ctx.pilot, lambda: not app_ctx.app.is_running)

    assert not app_ctx.app.is_running
    assert fake.shutdown_calls == [True]


class _BlockedSync(_FakeLocalCopyManager):
    """A fake whose sync waits for ``gate`` and records a cancellation."""

    def __init__(self, pending: list[CopyEntry]) -> None:
        super().__init__(pending)
        self.gate = asyncio.Event()
        self.cancelled = False

    async def sync_now(self, entry: CopyEntry) -> None:
        try:
            await self.gate.wait()
        except asyncio.CancelledError:
            self.cancelled = True
            raise
        await super().sync_now(entry)


@pytest.mark.asyncio
@pytest.mark.integration
async def test_ctrl_q_during_sync_and_quit_does_not_cancel_the_sync(app_ctx: AppCtx) -> None:
    fake = _BlockedSync(pending=[_entry()])
    app_ctx.app.local_copies = fake
    await _press_all(app_ctx, ["ctrl+q"])
    await app_ctx.pilot.click("#SAVE")
    await app_ctx.pilot.pause()

    await _press_all(app_ctx, ["ctrl+q"])

    assert app_ctx.app.is_running
    assert not fake.cancelled
    assert _quit_prompts(app_ctx.app) == []

    fake.gate.set()
    await poll_until(app_ctx.pilot, lambda: not app_ctx.app.is_running)

    assert fake.shutdown_calls == [True]


# ---------------------------------------------------------------------------
# A write-back that does not return: the quit key offers to quit and keep files
# ---------------------------------------------------------------------------


class _HungWrite:
    """Replace a copy's write_back() so it does not return before the test finishes it."""

    def __init__(self, monkeypatch: pytest.MonkeyPatch, copy: LocalCopy) -> None:
        self.entered = threading.Event()
        self.calls = 0
        self._done = threading.Event()
        self._succeed = False
        original = copy.write_back

        def write_back(progress: ProgressCallback | None = None) -> None:
            self.calls += 1
            self.entered.set()
            self._done.wait(timeout=60)  # a broken test must not keep the worker thread for ever
            if not self._succeed:
                raise OSError("connection lost")
            original(progress)

        monkeypatch.setattr(copy, "write_back", write_back)

    def finish(self, *, succeed: bool) -> None:
        if not self._done.is_set():
            self._succeed = succeed
            self._done.set()


@dataclass
class _Hung:
    manager: LocalCopyManager
    fs: SchemeFs
    entry: CopyEntry
    write: _HungWrite


@pytest.fixture
def _no_orphan_scan(monkeypatch: pytest.MonkeyPatch) -> None:
    """Keep the startup scan away from the real local-copy directories; request this before the app starts."""
    monkeypatch.setattr(nova_navigator_module, "find_orphans", list)


async def _open_copy(app: NovaNavigator, pilot: Pilot[None], root: Path) -> tuple[LocalCopyManager, SchemeFs, CopyEntry]:
    manager = LocalCopyManager(root, app.start_job, poll_interval=0.05, settle_time=0.1)
    app.local_copies = manager
    fs = SchemeFs({"/d/report.txt": b"old"})
    worker = app.run_worker(manager.open(fs.path("/d/report.txt")))
    await poll_until(pilot, lambda: worker.is_finished)
    entry = worker.result
    assert entry is not None
    return manager, fs, entry


@pytest_asyncio.fixture
async def hung(_no_orphan_scan: None, app_ctx: AppCtx, tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> AsyncIterator[_Hung]:
    """An edited local copy whose automatic write-back is running and does not return."""
    manager, fs, entry = await _open_copy(app_ctx.app, app_ctx.pilot, tmp_path / "copies")
    write = _HungWrite(monkeypatch, entry.copy)
    entry.copy.path.write_bytes(b"edited")
    await poll_until(app_ctx.pilot, write.entered.is_set)
    assert write.entered.is_set()
    yield _Hung(manager, fs, entry, write)
    write.finish(succeed=False)
    await poll_until(app_ctx.pilot, lambda: not manager.writing())  # before the app is torn down


def _write_back_dialogs(app: NovaNavigator) -> list[WriteBackRunningDialog]:
    return [screen for screen in app.screen_stack if isinstance(screen, WriteBackRunningDialog)]


async def _quit_and_wait_for_write(pilot: Pilot[None], choice: str, key: str = "ctrl+q") -> None:
    """Press the quit key and answer the quit prompt, which leaves the quit waiting for the write-back."""
    app = pilot.app
    await pilot.press(*key.split())
    await poll_until(pilot, lambda: bool(app.screen.query("#IGNORE")))
    await pilot.click(f"#{choice}")
    await pilot.pause()


async def _open_write_back_dialog(pilot: Pilot[None], key: str = "ctrl+q") -> WriteBackRunningDialog:
    await pilot.press(*key.split())
    await poll_until(pilot, lambda: isinstance(pilot.app.screen, WriteBackRunningDialog))
    dialog = pilot.app.screen
    assert isinstance(dialog, WriteBackRunningDialog)
    return dialog


_CHOICES = {"sync and quit": "SAVE", "quit and keep files": "IGNORE"}


@pytest.mark.asyncio
@pytest.mark.integration
@pytest.mark.parametrize("choice", _CHOICES.values(), ids=_CHOICES.keys())
async def test_quit_key_during_a_hung_write_back_asks_what_to_do(app_ctx: AppCtx, hung: _Hung, choice: str) -> None:
    await _quit_and_wait_for_write(app_ctx.pilot, choice)
    assert app_ctx.app.is_running

    dialog = await _open_write_back_dialog(app_ctx.pilot)

    text = "\n".join(str(label.content) for label in dialog.query(widgets.Label))
    assert "report.txt" in text
    assert str(hung.manager.root) in text
    focused = app_ctx.app.focused
    assert focused is not None
    assert focused.id == "CANCEL"


@pytest.mark.asyncio
@pytest.mark.integration
@pytest.mark.parametrize("answer", ["click", "escape", "enter"])
@pytest.mark.parametrize("choice", _CHOICES.values(), ids=_CHOICES.keys())
async def test_keep_waiting_leaves_the_app_and_the_write_running(app_ctx: AppCtx, hung: _Hung, choice: str, answer: str) -> None:
    await _quit_and_wait_for_write(app_ctx.pilot, choice)
    await _open_write_back_dialog(app_ctx.pilot)

    if answer == "click":
        await app_ctx.pilot.click("#CANCEL")
    else:
        await app_ctx.pilot.press(answer)
    await app_ctx.pilot.pause()

    assert app_ctx.app.is_running
    assert _write_back_dialogs(app_ctx.app) == []
    assert hung.manager.writing() == [hung.entry]
    assert hung.write.calls == 1
    await _open_write_back_dialog(app_ctx.pilot)  # the next quit key asks again


@pytest.mark.asyncio
@pytest.mark.integration
@pytest.mark.usefixtures("hung")
async def test_enter_without_a_focused_button_does_not_quit(app_ctx: AppCtx) -> None:
    await _quit_and_wait_for_write(app_ctx.pilot, "IGNORE")
    dialog = await _open_write_back_dialog(app_ctx.pilot)
    dialog.set_focus(None)

    await app_ctx.pilot.press("enter")
    await app_ctx.pilot.pause()

    assert app_ctx.app.is_running
    assert app_ctx.app.screen is dialog


@pytest.mark.asyncio
@pytest.mark.integration
@pytest.mark.parametrize("choice", _CHOICES.values(), ids=_CHOICES.keys())
async def test_quit_and_keep_files_ends_the_app_and_keeps_the_copy(app_ctx: AppCtx, hung: _Hung, choice: str) -> None:
    await _quit_and_wait_for_write(app_ctx.pilot, choice)
    await _open_write_back_dialog(app_ctx.pilot)

    await app_ctx.pilot.click("#IGNORE")
    await poll_until(app_ctx.pilot, lambda: not app_ctx.app.is_running)

    assert not app_ctx.app.is_running
    assert hung.entry.copy.path.read_bytes() == b"edited"
    hung.write.finish(succeed=True)  # a write that gets through during teardown removes nothing either
    await poll_until(app_ctx.pilot, lambda: hung.fs.read(hung.fs.path("/d/report.txt")).read(10) == b"edited")
    await hung.manager.shutdown()
    assert hung.entry.copy.path.read_bytes() == b"edited"


@pytest.mark.asyncio
@pytest.mark.integration
@pytest.mark.usefixtures("hung")
async def test_repeated_quit_keys_open_one_dialog(app_ctx: AppCtx) -> None:
    await _quit_and_wait_for_write(app_ctx.pilot, "IGNORE")
    dialog = await _open_write_back_dialog(app_ctx.pilot)

    await _press_all(app_ctx, ["ctrl+q", "ctrl+q"])

    assert _write_back_dialogs(app_ctx.app) == [dialog]
    assert app_ctx.app.screen is dialog
    assert app_ctx.app.is_running


@pytest.mark.asyncio
@pytest.mark.integration
@pytest.mark.parametrize("choice", _CHOICES.values(), ids=_CHOICES.keys())
async def test_a_write_that_finishes_while_asking_quits_as_chosen(app_ctx: AppCtx, hung: _Hung, choice: str) -> None:
    await _quit_and_wait_for_write(app_ctx.pilot, choice)
    await _open_write_back_dialog(app_ctx.pilot)

    hung.write.finish(succeed=True)
    await poll_until(app_ctx.pilot, lambda: not app_ctx.app.is_running)

    assert not app_ctx.app.is_running
    assert hung.entry.copy.path.exists() == (choice == "IGNORE")


@pytest.mark.asyncio
@pytest.mark.integration
async def test_a_write_that_fails_while_asking_closes_the_dialog(app_ctx: AppCtx, hung: _Hung) -> None:
    await _quit_and_wait_for_write(app_ctx.pilot, "SAVE")
    await _open_write_back_dialog(app_ctx.pilot)

    hung.write.finish(succeed=False)
    await poll_until(app_ctx.pilot, lambda: not _write_back_dialogs(app_ctx.app))

    assert _write_back_dialogs(app_ctx.app) == []
    assert app_ctx.app.is_running
    assert hung.entry.copy.path.read_bytes() == b"edited"


@pytest.mark.asyncio
@pytest.mark.integration
@pytest.mark.usefixtures("_no_orphan_scan")
@pytest.mark.parametrize("binding", ["ctrl+k", "ctrl+x ctrl+c"])
async def test_rebound_quit_key_asks_during_a_hung_write_back(tmp_path: Path, monkeypatch: pytest.MonkeyPatch, binding: str) -> None:
    config = tmp_path / "config"
    config.mkdir()
    (config / "keybindings.toml").write_text(f'[bindings]\n"app.quit" = "{binding}"\n')
    monkeypatch.setattr(loader, "_APP_CONFIG_DIR", config)
    app = NovaNavigator(config_dir=config)
    async with app.run_test(size=(120, 40)) as pilot:
        await pilot.pause()
        _manager, _fs, entry = await _open_copy(app, pilot, tmp_path / "copies")
        write = _HungWrite(monkeypatch, entry.copy)
        try:
            entry.copy.path.write_bytes(b"edited")
            await poll_until(pilot, write.entered.is_set)
            await _quit_and_wait_for_write(pilot, "IGNORE", binding)

            await _open_write_back_dialog(pilot, binding)

            assert app.is_running
        finally:
            write.finish(succeed=False)


@pytest.mark.asyncio
@pytest.mark.integration
@pytest.mark.usefixtures("_no_orphan_scan")
async def test_quit_key_asks_during_a_hung_reopen_overwrite(app_ctx: AppCtx, tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    manager, fs, entry = await _open_copy(app_ctx.app, app_ctx.pilot, tmp_path / "copies")
    await manager.close(entry)
    entry.copy.path.write_bytes(b"local edit")
    overwrite(fs, "/d/report.txt", b"server edit")
    write = _HungWrite(monkeypatch, entry.copy)
    try:
        app_ctx.app.run_worker(manager.open(fs.path("/d/report.txt")))
        await poll_until(app_ctx.pilot, lambda: bool(app_ctx.app.screen.query("#OVERWRITE")))
        await app_ctx.pilot.click("#OVERWRITE")
        await poll_until(app_ctx.pilot, write.entered.is_set)
        await _quit_and_wait_for_write(app_ctx.pilot, "IGNORE")
        assert app_ctx.app.is_running

        await _open_write_back_dialog(app_ctx.pilot)
        await app_ctx.pilot.click("#IGNORE")
        await poll_until(app_ctx.pilot, lambda: not app_ctx.app.is_running)

        assert not app_ctx.app.is_running
        assert entry.copy.path.read_bytes() == b"local edit"
    finally:
        write.finish(succeed=False)


class _BlockedShutdown(_FakeLocalCopyManager):
    """A fake whose shutdown waits for ``gate`` although nothing is being written."""

    def __init__(self) -> None:
        super().__init__(pending=[])
        self.gate = asyncio.Event()

    async def shutdown(self, remove_files: bool = True) -> None:
        await self.gate.wait()
        await super().shutdown(remove_files)


@pytest.mark.asyncio
@pytest.mark.integration
async def test_second_quit_key_without_a_write_back_asks_nothing(app_ctx: AppCtx) -> None:
    fake = _BlockedShutdown()
    app_ctx.app.local_copies = fake

    await _press_all(app_ctx, ["ctrl+q", "ctrl+q"])

    assert _write_back_dialogs(app_ctx.app) == []
    fake.gate.set()
    await poll_until(app_ctx.pilot, lambda: not app_ctx.app.is_running)
    assert fake.shutdown_calls == [True]
