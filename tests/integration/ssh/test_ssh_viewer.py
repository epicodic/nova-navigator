"""SSH integration tests: F3 on a remote file never writes it back and never touches an F4 copy of it (REQ-17).

Runs against the local stub SFTP server of the ssh fixtures; no real host is contacted.
"""

from __future__ import annotations

from pathlib import Path

import pytest

from nova_editor.screen import EditorScreen
from nova_navigator.local_copies import LocalCopyManager
from nova_navigator.nova_navigator import MainScreen, NovaNavigator
from nova_navigator.vfs import VPath
from tests.integration.conftest import poll_until
from tests.integration.ssh.conftest import SshAppCtx, set_ssh_panels_remote_left
from tests.integration.test_viewer import snapshot, spy_write_back, view_directories

SOURCE = b"remote content\n"


def app_of(ctx: SshAppCtx) -> NovaNavigator:
    app = ctx.pilot.app
    assert isinstance(app, NovaNavigator)
    return app


def install_manager(app: NovaNavigator, root: Path) -> LocalCopyManager:
    manager = LocalCopyManager(root, app.start_job, poll_interval=3600, settle_time=3600)
    app.local_copies = manager
    return manager


async def press_f3(ctx: SshAppCtx) -> EditorScreen:
    app = app_of(ctx)
    await ctx.pilot.press("f3")
    await poll_until(ctx.pilot, lambda: isinstance(app.screen, EditorScreen), max_wait=10)
    screen = app.screen
    assert isinstance(screen, EditorScreen)
    await ctx.pilot.pause()
    return screen


async def close_viewer(ctx: SshAppCtx) -> None:
    app = app_of(ctx)
    await ctx.pilot.press("escape")
    await poll_until(ctx.pilot, lambda: isinstance(app.screen, MainScreen) and not app._editor_busy)
    assert isinstance(app.screen, MainScreen)
    assert not app._editor_busy


@pytest.mark.asyncio
@pytest.mark.integration
@pytest.mark.ssh
async def test_f3_remote_file_is_never_written_back(ssh_app_ctx: SshAppCtx, tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    calls = spy_write_back(monkeypatch)
    source_file = ssh_app_ctx.remote_dir / "f.txt"
    source_file.write_bytes(SOURCE)
    before = snapshot(source_file)
    app = app_of(ssh_app_ctx)
    manager = install_manager(app, tmp_path / "copies")
    await set_ssh_panels_remote_left(ssh_app_ctx)

    screen = await press_f3(ssh_app_ctx)

    path = screen.document.file_path
    assert path is not None
    assert tmp_path / "copies" / "view" in path.parents
    assert screen.document.editor.text == SOURCE.decode()
    for key in ("x", "enter", "backspace", "delete", "ctrl+x", "ctrl+z", "ctrl+s"):
        await ssh_app_ctx.pilot.press(key)
        await ssh_app_ctx.pilot.pause()
    assert screen.document.editor.text == SOURCE.decode()
    await close_viewer(ssh_app_ctx)

    assert snapshot(source_file) == before
    assert not manager.entries
    assert calls == []
    assert view_directories(tmp_path / "copies") == []


@pytest.mark.asyncio
@pytest.mark.integration
@pytest.mark.ssh
async def test_f3_leaves_an_unsynced_editor_copy_alone(ssh_app_ctx: SshAppCtx, tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    calls = spy_write_back(monkeypatch)
    source_file = ssh_app_ctx.remote_dir / "f.txt"
    source_file.write_bytes(SOURCE)
    before = snapshot(source_file)
    app = app_of(ssh_app_ctx)
    manager = install_manager(app, tmp_path / "copies")
    source = VPath(source_file, ssh_app_ctx.ssh_fs)
    entry = await app.run_worker(manager.open(source)).wait()  # the F4 copy
    assert entry is not None
    entry.copy.path.write_bytes(b"unsynced\n")  # never synced within the test
    await set_ssh_panels_remote_left(ssh_app_ctx)

    screen = await press_f3(ssh_app_ctx)

    assert screen.document.file_path != entry.copy.path
    assert screen.document.editor.text == SOURCE.decode()
    await close_viewer(ssh_app_ctx)

    assert entry.copy.path.read_bytes() == b"unsynced\n"
    assert list(manager.entries) == [entry]
    assert snapshot(source_file) == before
    assert calls == []
    assert view_directories(tmp_path / "copies") == []
