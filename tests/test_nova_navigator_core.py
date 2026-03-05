from __future__ import annotations

import asyncio
from pathlib import Path, PurePath

from nova_navigator.dialogs import JobRegistry
from nova_navigator.nova_navigator_core import (
    NovaNavigatorCore,
    PanelRef,
)
from nova_navigator.vfs import VPath
from nova_navigator.vfs.filesystems import LocalFilesystem
from tests._utils.mock_filesystem import MockFilesystem


class _StubCore(NovaNavigatorCore):
    def __init__(self) -> None:
        super().__init__()
        self.terminal_directory: VPath | None = None
        self.panel_path: VPath | None = None
        self.command_args: list[str] | None = None

    async def open_editor(self, path: VPath) -> None:
        pass

    async def execute_command(self, args: list[str], cwd: PurePath) -> None:
        self.command_args = args

    async def set_terminal_directory(self, path: VPath) -> None:
        self.terminal_directory = path

    async def set_panel_path(self, path: VPath, panel: PanelRef) -> None:
        self.panel_path = path


def test_nova_navigator_core_has_job_registry() -> None:
    core = _StubCore()
    assert isinstance(core.job_registry, JobRegistry)


def test_open_path_directory_sets_terminal_directory() -> None:
    fs = MockFilesystem({"/some/dir": None})
    path = VPath("/some/dir", fs)
    core = _StubCore()
    asyncio.run(core.open_path(path))
    assert core.terminal_directory == path


def test_open_path_file_executes_open_command() -> None:
    fs = MockFilesystem({"/some/file.txt": b"content"})
    path = VPath("/some/file.txt", fs)
    core = _StubCore()
    asyncio.run(core.open_path(path))
    assert core.command_args is not None
    assert len(core.command_args) > 0


def test_open_path_executable_runs_directly(tmp_path: Path) -> None:
    script = tmp_path / "script.sh"
    script.write_text("#!/bin/sh\necho hello\n")
    script.chmod(0o755)
    local_fs = LocalFilesystem.singleton()
    path = VPath(script, local_fs)
    core = _StubCore()
    asyncio.run(core.open_path(path))
    assert core.command_args == [str(script)]
