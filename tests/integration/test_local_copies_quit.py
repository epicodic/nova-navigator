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
from pathlib import Path

import pytest
from textual import widgets

from nova_navigator.config import loader
from nova_navigator.local_copies import CopyEntry, CopyStatus, LocalCopyManager
from nova_navigator.nova_navigator import NovaNavigator
from nova_navigator.scheduler import Job
from nova_navigator.vfs.filesystems.local import LocalFilesystem
from nova_navigator.vfs.local_copy import Baseline, LocalCopy, SourceFingerprint
from nova_navigator.vfs.vpath import VPath
from nova_widgets import MessageBox
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
