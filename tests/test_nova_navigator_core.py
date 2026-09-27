from __future__ import annotations

import asyncio
from pathlib import Path, PurePath

import pytest

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

    async def set_panel_directory(self, path: VPath, panel: PanelRef) -> None:
        self.panel_path = path


def test_nova_navigator_core_has_job_registry() -> None:
    core = _StubCore()
    assert isinstance(core.job_registry, JobRegistry)


def test_open_path_directory_sets_panel_directory() -> None:
    fs = MockFilesystem({"/some/dir": None})
    path = VPath("/some/dir", fs)
    core = _StubCore()
    asyncio.run(core.open_path(path))
    assert core.panel_path == path


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


# ── NerdFont mode resolution ──────────────────────────────────────────────────


def test_resolve_nerd_font_yes_returns_nerdfont_variant() -> None:
    from nova_navigator.config.settings import NerdFontMode
    from nova_navigator.icons import IconSet
    from nova_navigator.nova_navigator_core import _resolve_nerd_font_variant

    assert _resolve_nerd_font_variant(NerdFontMode.YES) is IconSet.Variants.NERDFONT


def test_resolve_nerd_font_no_returns_unicode_variant() -> None:
    from nova_navigator.config.settings import NerdFontMode
    from nova_navigator.icons import IconSet
    from nova_navigator.nova_navigator_core import _resolve_nerd_font_variant

    assert _resolve_nerd_font_variant(NerdFontMode.NO) is IconSet.Variants.UNICODE


def test_resolve_nerd_font_auto_uses_detection_result(monkeypatch: pytest.MonkeyPatch) -> None:
    import nova_navigator.nova_navigator_core as core_mod
    from nova_navigator.config.settings import NerdFontMode
    from nova_navigator.icons import IconSet
    from nova_navigator.nova_navigator_core import _resolve_nerd_font_variant

    monkeypatch.setattr(core_mod, "detect_nerd_font", lambda: True)
    assert _resolve_nerd_font_variant(NerdFontMode.AUTO) is IconSet.Variants.NERDFONT

    monkeypatch.setattr(core_mod, "detect_nerd_font", lambda: False)
    assert _resolve_nerd_font_variant(NerdFontMode.AUTO) is IconSet.Variants.UNICODE
