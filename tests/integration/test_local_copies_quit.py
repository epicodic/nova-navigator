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

import pytest
from textual import widgets

from tests.integration.conftest import AppCtx


class _FakeLocalCopyManager:
    """Stand-in for LocalCopyManager: a scripted unsynced() list, and recorded calls."""

    def __init__(self, pending: list[object], *, sync_succeeds: bool = True) -> None:
        self._pending = pending
        self._sync_succeeds = sync_succeeds
        self.synced: list[object] = []
        self.shutdown_calls: list[bool] = []

    def unsynced(self) -> list[object]:
        if self.synced and self._sync_succeeds:
            return []
        return self._pending

    async def sync_now(self, entry: object) -> None:
        self.synced.append(entry)

    async def shutdown(self, remove_files: bool = True) -> None:
        self.shutdown_calls.append(remove_files)


@pytest.mark.asyncio
@pytest.mark.integration
async def test_quit_with_no_unsynced_copies_exits_directly(app_ctx: AppCtx) -> None:
    fake = _FakeLocalCopyManager(pending=[])
    app_ctx.app.local_copies = fake  # type: ignore[assignment]

    app_ctx.screen._action_quit()
    await app_ctx.pilot.pause()

    assert not app_ctx.app.is_running
    assert fake.shutdown_calls == [True]


@pytest.mark.asyncio
@pytest.mark.integration
async def test_quit_prompt_cancel_keeps_app_running(app_ctx: AppCtx) -> None:
    fake = _FakeLocalCopyManager(pending=["entry"])
    app_ctx.app.local_copies = fake  # type: ignore[assignment]

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
    fake = _FakeLocalCopyManager(pending=["entry"])
    app_ctx.app.local_copies = fake  # type: ignore[assignment]

    app_ctx.screen._action_quit()
    await app_ctx.pilot.pause()

    await app_ctx.pilot.click("#IGNORE")
    await app_ctx.pilot.pause()

    assert not app_ctx.app.is_running
    assert fake.shutdown_calls == [False]


@pytest.mark.asyncio
@pytest.mark.integration
async def test_quit_prompt_sync_and_quit_shuts_down_and_removes_files(app_ctx: AppCtx) -> None:
    fake = _FakeLocalCopyManager(pending=["entry"])
    app_ctx.app.local_copies = fake  # type: ignore[assignment]

    app_ctx.screen._action_quit()
    await app_ctx.pilot.pause()

    await app_ctx.pilot.click("#SAVE")
    await app_ctx.pilot.pause()

    assert fake.synced == ["entry"]
    assert not app_ctx.app.is_running
    assert fake.shutdown_calls == [True]


@pytest.mark.asyncio
@pytest.mark.integration
async def test_quit_prompt_sync_failure_keeps_app_running(app_ctx: AppCtx) -> None:
    fake = _FakeLocalCopyManager(pending=["entry"], sync_succeeds=False)
    app_ctx.app.local_copies = fake  # type: ignore[assignment]

    app_ctx.screen._action_quit()
    await app_ctx.pilot.pause()

    await app_ctx.pilot.click("#SAVE")
    await app_ctx.pilot.pause()

    assert fake.synced == ["entry"]
    assert app_ctx.app.is_running
    assert fake.shutdown_calls == []
