"""SSH integration tests: navigate into a subdirectory and verify its contents.

Two test variants:
- fast server (near-zero latency) — basic smoke test.
- slow server (300 ms per-command delay) — reproduces the real-world timing
  race where a second navigation starts before the first load completes.
"""

from __future__ import annotations

from pathlib import PurePosixPath

import pytest

from nova_navigator.vfs import VPath
from tests.integration.conftest import poll_until
from tests.integration.ssh.conftest import SshAppCtx


def _make_two_dirs(ctx: SshAppCtx) -> tuple[object, object]:
    """Create dir_a/{file1-3.txt} and dir_b/{file4-6.txt} under remote_dir."""
    dir_a = ctx.remote_dir / "dir_a"
    dir_b = ctx.remote_dir / "dir_b"
    dir_a.mkdir(exist_ok=True)
    dir_b.mkdir(exist_ok=True)
    for i in range(1, 4):
        (dir_a / f"file{i}.txt").write_text(f"content {i}")
    for i in range(4, 7):
        (dir_b / f"file{i}.txt").write_text(f"content {i}")
    return dir_a, dir_b


@pytest.mark.asyncio
@pytest.mark.integration
async def test_navigate_into_ssh_subdir_shows_its_files(ssh_app_ctx: SshAppCtx) -> None:
    """Navigate into a remote subdirectory and verify its three files are listed."""
    dir_a, _dir_b = _make_two_dirs(ssh_app_ctx)

    panel = ssh_app_ctx.screen._left_panel
    panel.set_path(VPath(ssh_app_ctx.remote_dir, ssh_app_ctx.ssh_fs))
    panel.focus()

    await poll_until(
        ssh_app_ctx.pilot,
        lambda: {item.name for item in panel._shown_items} >= {"dir_a", "dir_b"},
    )

    dir_a_row = next(i for i, item in enumerate(panel._shown_items) if item.name == "dir_a")
    panel.cursor_row = dir_a_row
    await ssh_app_ctx.pilot.pause()

    await ssh_app_ctx.pilot.press("enter")
    await poll_until(ssh_app_ctx.pilot, lambda: panel.path.path == PurePosixPath(str(dir_a)))
    await poll_until(
        ssh_app_ctx.pilot,
        lambda: {item.name for item in panel._shown_items} >= {"file1.txt", "file2.txt", "file3.txt"},
    )

    names = {item.name for item in panel._shown_items}
    assert {"file1.txt", "file2.txt", "file3.txt"} <= names
    assert not {"file4.txt", "file5.txt", "file6.txt"} & names


@pytest.mark.asyncio
@pytest.mark.integration
async def test_navigate_into_subdir_while_previous_load_in_flight(slow_ssh_app_ctx: SshAppCtx) -> None:
    """Navigate into a subdir before the previous directory load finishes.

    With a 300 ms exec delay each SSH command takes ~300 ms.  We start loading
    the root directory, then navigate into dir_a only 50 ms later — while the
    root load is still in-flight.  The panel must eventually show dir_a's
    three files and must NOT show dir_b's files.
    """
    dir_a, _dir_b = _make_two_dirs(slow_ssh_app_ctx)

    panel = slow_ssh_app_ctx.screen._left_panel
    panel.set_path(VPath(slow_ssh_app_ctx.remote_dir, slow_ssh_app_ctx.ssh_fs))
    panel.focus()

    # Wait just long enough that the first load has started, but NOT completed
    # (each exec_command takes ~300 ms; 50 ms is safely in-flight).
    await slow_ssh_app_ctx.pilot.pause(delay=0.05)

    # Navigate to dir_a while the root load is still running.
    panel.set_path(VPath(str(dir_a), slow_ssh_app_ctx.ssh_fs))

    # Allow enough time for the new load to complete (two 300 ms SSH calls).
    await poll_until(
        slow_ssh_app_ctx.pilot,
        lambda: {item.name for item in panel._shown_items} >= {"file1.txt", "file2.txt", "file3.txt"},
        max_wait=10.0,
    )

    names = {item.name for item in panel._shown_items}
    assert {"file1.txt", "file2.txt", "file3.txt"} <= names
    assert not {"file4.txt", "file5.txt", "file6.txt"} & names
