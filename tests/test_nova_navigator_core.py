from __future__ import annotations

import asyncio
import zipfile
from pathlib import Path, PurePath

import pytest

from nova_navigator.dialogs import JobRegistry
from nova_navigator.local_copies import CopyEntry, CopyStatus, LocalCopyManager
from nova_navigator.nova_navigator_core import (
    NovaNavigatorCore,
    PanelRef,
)
from nova_navigator.scheduler import Job
from nova_navigator.vfs import VPath
from nova_navigator.vfs.filesystems import ArchiveFilesystem, LocalFilesystem
from nova_navigator.vfs.local_copy import Baseline, LocalCopy, SourceFingerprint
from nova_navigator.vfs.types import Stat
from tests._utils.mock_filesystem import MockFilesystem


class _StubCore(NovaNavigatorCore):
    def __init__(self) -> None:
        super().__init__()
        self.terminal_directory: VPath | None = None
        self.panel_path: VPath | None = None
        self.command_args: list[str] | None = None
        self.command_cwd: PurePath | None = None

    async def open_editor(self, path: VPath) -> None:
        pass

    async def execute_command(self, args: list[str], cwd: PurePath) -> None:
        self.command_args = args
        self.command_cwd = cwd

    async def set_terminal_directory(self, path: VPath) -> None:
        self.terminal_directory = path

    async def set_panel_directory(self, path: VPath, panel: PanelRef) -> None:
        self.panel_path = path


class _FakeLocalCopies(LocalCopyManager):
    """Records calls instead of running real jobs; scripted to return fixed results."""

    def __init__(self) -> None:
        super().__init__(Path("/unused"), self._unused_start_job)
        self.opened: list[VPath] = []
        self.mounted: list[VPath] = []
        self.checked: list[CopyEntry] = []
        self.entry_to_return: CopyEntry | None = None
        self.archive_fs_to_return: ArchiveFilesystem | None = None

    async def _unused_start_job(self, job: Job) -> None:
        raise AssertionError("start_job should not be called by this fake")

    async def open(self, source: VPath) -> CopyEntry | None:
        self.opened.append(source)
        return self.entry_to_return

    async def mount_archive(self, source: VPath) -> ArchiveFilesystem | None:
        self.mounted.append(source)
        return self.archive_fs_to_return

    async def check_now(self, entry: CopyEntry) -> None:
        self.checked.append(entry)


def _make_copy_entry(source: VPath, local_path: Path) -> CopyEntry:
    baseline = Baseline(SourceFingerprint(size=0, modified=0.0, tag=None), "")
    copy = LocalCopy(source, local_path, baseline, read_only=False, pass_through=False)
    return CopyEntry(copy=copy, status=CopyStatus.SYNCED)


def test_nova_navigator_core_has_job_registry() -> None:
    core = _StubCore()
    assert isinstance(core.job_registry, JobRegistry)


def test_open_path_directory_sets_panel_directory() -> None:
    fs = MockFilesystem({"/some/dir": None})
    path = VPath("/some/dir", fs)
    core = _StubCore()
    asyncio.run(core.open_path(path))
    assert core.panel_path == path


def test_open_path_non_local_file_uses_local_copies(tmp_path: Path) -> None:
    fs = MockFilesystem({"/some/file.txt": b"content"})
    path = VPath("/some/file.txt", fs)
    local_path = tmp_path / "file.txt"
    local_path.write_bytes(b"content")

    core = _StubCore()
    fake = _FakeLocalCopies()
    fake.entry_to_return = _make_copy_entry(path, local_path)
    core.local_copies = fake

    asyncio.run(core.open_path(path))

    assert fake.opened == [path]
    assert core.command_args is not None
    assert local_path.as_posix() in " ".join(core.command_args)
    assert core.command_cwd == local_path.parent
    assert fake.checked == [fake.entry_to_return]


def test_open_path_local_file_executes_open_command(tmp_path: Path) -> None:
    local_file = tmp_path / "doc.txt"
    local_file.write_text("hello")
    local_fs = LocalFilesystem.singleton()
    path = VPath(local_file, local_fs)
    core = _StubCore()
    fake = _FakeLocalCopies()
    core.local_copies = fake

    asyncio.run(core.open_path(path))

    assert fake.opened == []
    assert core.command_args is not None
    assert local_file.as_posix() in " ".join(core.command_args)


def test_open_path_executable_runs_directly(tmp_path: Path) -> None:
    script = tmp_path / "script.sh"
    script.write_text("#!/bin/sh\necho hello\n")
    script.chmod(0o755)
    local_fs = LocalFilesystem.singleton()
    path = VPath(script, local_fs)
    core = _StubCore()
    asyncio.run(core.open_path(path))
    assert core.command_args == [str(script)]


def test_open_path_non_local_executable_uses_local_copies(tmp_path: Path) -> None:
    fs = MockFilesystem({"/some/script.sh": b"#!/bin/sh\necho hi\n"})
    path = VPath("/some/script.sh", fs)
    path._stat = Stat(size=18, modified=0.0, is_executable=True)
    local_path = tmp_path / "script.sh"
    local_path.write_bytes(b"#!/bin/sh\necho hi\n")

    core = _StubCore()
    fake = _FakeLocalCopies()
    fake.entry_to_return = _make_copy_entry(path, local_path)
    core.local_copies = fake

    asyncio.run(core.open_path(path))

    assert fake.opened == [path]
    assert core.command_args is not None
    assert local_path.as_posix() in " ".join(core.command_args)


def test_open_path_non_local_archive_mounts_and_navigates(tmp_path: Path) -> None:
    fs = MockFilesystem({"/remote/archive.zip": b"not-really-a-zip"})
    path = VPath("/remote/archive.zip", fs)

    archive_path = tmp_path / "archive.zip"
    with zipfile.ZipFile(archive_path, "w") as zf:
        zf.writestr("a.txt", "hi")
    local_fs = LocalFilesystem.singleton()
    mounted_fs = ArchiveFilesystem(VPath(tmp_path, local_fs), VPath(archive_path, local_fs))

    core = _StubCore()
    fake = _FakeLocalCopies()
    fake.archive_fs_to_return = mounted_fs
    core.local_copies = fake

    asyncio.run(core.open_path(path))

    assert fake.mounted == [path]
    assert core.panel_path == VPath("/", mounted_fs)


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
