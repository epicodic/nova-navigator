from __future__ import annotations

from pathlib import Path

from nova_navigator.vfs import VPath
from nova_navigator.vfs.filesystems import LocalFilesystem
from nova_navigator.vfs.filesystems.archive import ArchiveFilesystem
from nova_navigator.vfs.filesystems.remote import RemoteFilesystem

_DATA = Path(__file__).parent.parent / "_data"


def test_remote_delegates_scheme_and_capability() -> None:
    remote = RemoteFilesystem("box", LocalFilesystem.singleton())
    assert remote.scheme == "local"
    assert remote.capabilities.commands is True


def test_remote_delegates_exec_command(tmp_path: Path) -> None:
    remote = RemoteFilesystem("box", LocalFilesystem.singleton())
    result = remote.exec_command("echo hi", remote.path(tmp_path))
    assert result.exit_code == 0
    assert result.output.strip() == "hi"


def test_archive_scheme_and_no_commands() -> None:
    local = LocalFilesystem.singleton()
    archive = ArchiveFilesystem(archive_parent=VPath(_DATA, local), archive=VPath(_DATA / "test_archive.zip", local))
    assert archive.scheme == "archive"
    assert archive.capabilities.commands is False
