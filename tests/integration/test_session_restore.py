"""Integration tests for restoring the right panel directory and the sort orders at start.

Each test starts the real app, changes the panels, quits through ``request_quit`` and starts a second app that
reads the state file written at quit.
"""

from __future__ import annotations

import asyncio
import contextlib
import zipfile
from collections.abc import AsyncIterator, Callable
from pathlib import Path, PurePath

import pytest

from nova_navigator import session_state
from nova_navigator.nova_navigator import MainScreen, NovaNavigator
from nova_navigator.session_state import SessionState, save_session_state
from nova_navigator.terminal import Terminal
from nova_navigator.vfs import VPath
from nova_navigator.vfs.filesystems import LocalFilesystem
from nova_navigator.widgets.directory_browser import DirectoryBrowser, SortKey, UpPath
from tests._utils.mock_filesystem import MockFilesystem


@contextlib.asynccontextmanager
async def _session(config_dir: Path) -> AsyncIterator[tuple[NovaNavigator, MainScreen]]:
    app = NovaNavigator(config_dir=config_dir)
    async with app.run_test() as pilot:
        await pilot.pause()
        yield app, app._main_screen


async def _wait_for(predicate: Callable[[], bool], max_wait: float = 5.0) -> None:
    """Yield to the event loop until *predicate* holds."""
    steps = round(max_wait / 0.05)
    for _ in range(steps):
        if predicate():
            return
        await asyncio.sleep(0.05)
    assert predicate()


def _committed(browser: DirectoryBrowser) -> bool:
    return not isinstance(browser.path, UpPath)


def _local_dir(browser: DirectoryBrowser) -> str:
    return str(browser.path.path)


@pytest.fixture
def work_dir(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Path:
    """The app's cwd (left panel) and a right-panel directory with one file."""
    work = tmp_path / "work"
    right = tmp_path / "right"
    work.mkdir()
    right.mkdir()
    (right / "right.txt").write_text("right")
    monkeypatch.chdir(work)
    return right


@pytest.fixture
def config_dir(tmp_path: Path) -> Path:
    config = tmp_path / "config"
    config.mkdir()
    return config


@pytest.mark.asyncio
@pytest.mark.integration
async def test_restart_restores_right_directory_and_left_shows_cwd(work_dir: Path, config_dir: Path) -> None:
    cwd = Path.cwd()
    async with _session(config_dir) as (app, screen):
        await _wait_for(lambda: _committed(screen._right_panel))
        screen._right_panel.set_path(VPath(work_dir, LocalFilesystem.singleton()))
        await _wait_for(lambda: _local_dir(screen._right_panel) == str(work_dir))
        await app.request_quit()

    async with _session(config_dir) as (_app, screen):
        await _wait_for(lambda: _committed(screen._right_panel) and _committed(screen._left_panel))
        assert _local_dir(screen._right_panel) == str(work_dir)
        assert _local_dir(screen._left_panel) == str(cwd)


@pytest.mark.asyncio
@pytest.mark.integration
async def test_left_panel_is_active_at_start(config_dir: Path) -> None:
    async with _session(config_dir) as (app, screen):
        await _wait_for(lambda: _committed(screen._left_panel))
        assert app.focused is screen._left_panel
        assert screen.active_panel() is screen._left_panel


@pytest.mark.asyncio
@pytest.mark.integration
async def test_without_state_right_panel_shows_cwd(config_dir: Path) -> None:
    cwd = Path.cwd()
    async with _session(config_dir) as (_app, screen):
        await _wait_for(lambda: _committed(screen._right_panel))
        assert _local_dir(screen._right_panel) == str(cwd)


@pytest.mark.asyncio
@pytest.mark.integration
async def test_missing_stored_directory_falls_back_to_cwd(tmp_path: Path, config_dir: Path) -> None:
    cwd = Path.cwd()
    save_session_state(SessionState(right_path=str(tmp_path / "gone")))
    async with _session(config_dir) as (_app, screen):
        await _wait_for(lambda: _committed(screen._right_panel))
        assert _local_dir(screen._right_panel) == str(cwd)


@pytest.mark.asyncio
@pytest.mark.integration
async def test_unreadable_stored_directory_falls_back_to_cwd(tmp_path: Path, config_dir: Path) -> None:
    cwd = Path.cwd()
    locked = tmp_path / "locked"
    locked.mkdir()
    (locked / "inside.txt").write_text("x")
    save_session_state(SessionState(right_path=str(locked)))
    locked.chmod(0o000)
    try:
        async with _session(config_dir) as (_app, screen):
            await _wait_for(lambda: _committed(screen._right_panel))
            assert _local_dir(screen._right_panel) == str(cwd)
    finally:
        locked.chmod(0o755)


@pytest.mark.asyncio
@pytest.mark.integration
async def test_right_panel_left_in_archive_restores_last_local_directory(work_dir: Path, config_dir: Path) -> None:
    zip_path = work_dir / "bundle.zip"
    with zipfile.ZipFile(zip_path, "w") as zf:
        zf.writestr("member.txt", "member")
    async with _session(config_dir) as (app, screen):
        await _wait_for(lambda: _committed(screen._right_panel))
        screen._right_panel.set_path(VPath(work_dir, LocalFilesystem.singleton()))
        await _wait_for(lambda: _local_dir(screen._right_panel) == str(work_dir))
        await screen._open_path(VPath(zip_path, LocalFilesystem.singleton()), screen._right_panel)
        await _wait_for(lambda: screen._right_panel.path.filesystem is not LocalFilesystem.singleton())
        await app.request_quit()

    async with _session(config_dir) as (_app, screen):
        await _wait_for(lambda: _committed(screen._right_panel))
        assert _local_dir(screen._right_panel) == str(work_dir)


@pytest.mark.asyncio
@pytest.mark.integration
async def test_right_panel_left_on_remote_restores_last_local_directory(work_dir: Path, config_dir: Path) -> None:
    remote = MockFilesystem({"/remote/dir/file.txt": b"x"})
    async with _session(config_dir) as (app, screen):
        await _wait_for(lambda: _committed(screen._right_panel))
        screen._right_panel.set_path(VPath(work_dir, LocalFilesystem.singleton()))
        await _wait_for(lambda: _local_dir(screen._right_panel) == str(work_dir))
        screen._right_panel.set_path(VPath("/remote/dir", remote))
        await _wait_for(lambda: screen._right_panel.path.filesystem is remote)
        await app.request_quit()

    async with _session(config_dir) as (_app, screen):
        await _wait_for(lambda: _committed(screen._right_panel))
        assert _local_dir(screen._right_panel) == str(work_dir)


@pytest.mark.asyncio
@pytest.mark.integration
async def test_restart_restores_sort_per_panel(config_dir: Path) -> None:
    async with _session(config_dir) as (app, screen):
        await _wait_for(lambda: _committed(screen._left_panel) and _committed(screen._right_panel))
        screen._left_panel.sort_column = SortKey.SIZE
        screen._left_panel.sort_ascending = False
        screen._right_panel.sort_column = SortKey.MODIFIED
        screen._right_panel.sort_ascending = True
        await app.request_quit()

    async with _session(config_dir) as (_app, screen):
        await _wait_for(lambda: _committed(screen._left_panel) and _committed(screen._right_panel))
        assert screen._left_panel.sort_column is SortKey.SIZE
        assert screen._left_panel.sort_ascending is False
        assert screen._right_panel.sort_column is SortKey.MODIFIED
        assert screen._right_panel.sort_ascending is True


@pytest.mark.asyncio
@pytest.mark.integration
async def test_damaged_state_starts_and_quits(config_dir: Path) -> None:
    state_file = session_state._STATE_FILE
    state_file.parent.mkdir(parents=True, exist_ok=True)
    state_file.write_text("this is [not toml", encoding="utf-8")
    async with _session(config_dir) as (app, screen):
        await _wait_for(lambda: _committed(screen._right_panel))
        assert _committed(screen._left_panel)
        await app.request_quit()


@pytest.mark.asyncio
@pytest.mark.integration
async def test_unwritable_state_does_not_block_quit(config_dir: Path) -> None:
    state_file = session_state._STATE_FILE
    state_file.parent.parent.mkdir(parents=True, exist_ok=True)
    state_file.parent.write_text("a regular file, not a directory", encoding="utf-8")
    async with _session(config_dir) as (app, screen):
        await _wait_for(lambda: _committed(screen._right_panel))
        await app.request_quit()


@pytest.mark.asyncio
@pytest.mark.integration
async def test_restoring_right_directory_does_not_move_terminal(work_dir: Path, config_dir: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    requested: list[str] = []
    original = Terminal.request_cd

    def recording_request_cd(self: Terminal, path: PurePath) -> None:
        requested.append(str(path))
        original(self, path)

    monkeypatch.setattr(Terminal, "request_cd", recording_request_cd)
    cwd = str(Path.cwd())
    save_session_state(SessionState(right_path=str(work_dir)))
    async with _session(config_dir) as (_app, screen):
        await _wait_for(lambda: _committed(screen._right_panel) and _committed(screen._left_panel))
        await _wait_for(lambda: _local_dir(screen._right_panel) == str(work_dir))
        await asyncio.sleep(0.2)
    assert requested
    assert all(path == cwd for path in requested)
