"""SSH integration test: a remote binary file is edited in the hex view through its local copy (REQ-24).

Runs against the local stub SFTP server of the ssh fixtures; no real host is contacted.
"""

from __future__ import annotations

import os
from pathlib import Path

import pytest

from nova_editor.screen import EditorScreen
from nova_navigator.local_copies import CopyStatus, LocalCopyManager
from nova_navigator.nova_navigator import MainScreen, NovaNavigator
from nova_navigator.vfs import VPath
from tests._utils.local_copy_helpers import ScriptedRunner
from tests.integration.conftest import poll_until
from tests.integration.ssh.conftest import SshAppCtx

BINARY = b"\x00\x01\x02\x03remote binary payload\n"
EDITED = b"\xee" + BINARY[1:]


@pytest.mark.asyncio
@pytest.mark.integration
@pytest.mark.ssh
async def test_a_remote_binary_file_is_edited_in_hex_and_the_remote_file_gets_the_change(ssh_app_ctx: SshAppCtx, tmp_path: Path) -> None:
    remote = ssh_app_ctx.remote_dir / "f.bin"
    remote.write_bytes(BINARY)
    pilot = ssh_app_ctx.pilot
    app = pilot.app
    assert isinstance(app, NovaNavigator)
    runner = ScriptedRunner()
    manager = LocalCopyManager(tmp_path / "copies", runner, poll_interval=0.05, settle_time=0.05)
    app.local_copies = manager
    await app.open_editor(VPath(remote, ssh_app_ctx.ssh_fs))
    await poll_until(pilot, lambda: isinstance(app.screen, EditorScreen))
    screen = app.screen
    assert isinstance(screen, EditorScreen)
    entry = manager.entries[0]
    await poll_until(pilot, lambda: screen.document.hex_mode and screen.document.shown.has_focus)
    assert screen.document.hex_mode
    assert screen.document.shown.has_focus
    assert len(manager.entries) == 1
    assert screen.document.file_path == entry.copy.path
    inode = os.stat(entry.copy.path).st_ino

    await pilot.press("ctrl+home", "e", "e", "ctrl+s")
    await poll_until(pilot, lambda: remote.read_bytes() == EDITED, max_wait=10)
    await poll_until(pilot, lambda: entry.status is CopyStatus.SYNCED, max_wait=10)

    assert os.stat(entry.copy.path).st_ino == inode
    assert entry.copy.path.read_bytes() == EDITED
    assert not screen.document.editor.modified
    await pilot.press("ctrl+w")
    await poll_until(pilot, lambda: isinstance(app.screen, MainScreen))
    await poll_until(pilot, lambda: entry.detector is None)
    assert len(runner.sync_jobs) == 1
    assert remote.read_bytes() == EDITED
    assert not [item.name for item in ssh_app_ctx.remote_dir.iterdir() if item.name.endswith(".nn-tmp")]
