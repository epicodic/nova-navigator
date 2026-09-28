"""Tests for filesystem plugin registration."""

from __future__ import annotations

from pathlib import Path
from zipfile import ZipFile

import pytest

from nova_navigator.config import conf_
from nova_navigator.config.settings import Settings
from nova_navigator.plugins import PluginRegistry
from nova_navigator.terminal.terminal_pool import TerminalPool
from nova_navigator.terminal.vfs_shell import VirtualPtyBackend
from nova_navigator.vfs.filesystems import ArchiveFilesystem, LocalFilesystem
from nova_navigator.vfs.scheme_registry import SchemeRegistry
from nova_navigator.vfs.vpath import VPath


@pytest.mark.asyncio
async def test_archive_plugin_registers_terminal_without_uri_connector(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    from nova_navigator.archive.terminal import ARCHIVE_PLUGIN

    monkeypatch.setattr(conf_, "settings", Settings(), raising=False)
    archive_path = tmp_path / "sample.zip"
    with ZipFile(archive_path, "w") as archive:
        archive.writestr("file.txt", "contents")
    local = LocalFilesystem.singleton()
    archive_fs = ArchiveFilesystem(VPath(tmp_path, local), VPath(archive_path, local))
    schemes = SchemeRegistry()
    pool = TerminalPool()

    PluginRegistry(schemes, pool).register(ARCHIVE_PLUGIN)

    assert schemes.find("archive") is None
    terminal = await pool.create_for(archive_fs)
    assert terminal is not None
    assert isinstance(terminal._backend, VirtualPtyBackend)
