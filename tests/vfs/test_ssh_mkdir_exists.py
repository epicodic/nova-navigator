"""SSHFilesystem.mkdir reports an existing path as FileExistsError (stub server and mocked SFTP)."""

from __future__ import annotations

import os
from pathlib import Path
from unittest.mock import MagicMock

import pytest

from nova_navigator.vfs.filesystems.ssh import SSHFilesystem
from nova_navigator.vfs.vpath import VPath
from tests.vfs.test_ssh_filesystem import _make_fs


def test_ssh_mkdir_existing_directory_raises_file_exists(ssh_stub_fs: SSHFilesystem, tmp_path: Path) -> None:
    (tmp_path / "exists").mkdir()
    with pytest.raises(FileExistsError):
        ssh_stub_fs.mkdir(VPath(tmp_path / "exists", ssh_stub_fs))


def test_ssh_mkdir_existing_file_raises_file_exists(ssh_stub_fs: SSHFilesystem, tmp_path: Path) -> None:
    (tmp_path / "file.txt").write_text("x")
    with pytest.raises(FileExistsError):
        ssh_stub_fs.mkdir(VPath(tmp_path / "file.txt", ssh_stub_fs))


def test_ssh_mkdir_existing_link_to_directory_raises_file_exists(ssh_stub_fs: SSHFilesystem, tmp_path: Path) -> None:
    (tmp_path / "real").mkdir()
    (tmp_path / "lnk").symlink_to("real", target_is_directory=True)
    with pytest.raises(FileExistsError):
        ssh_stub_fs.mkdir(VPath(tmp_path / "lnk", ssh_stub_fs))


def test_ssh_mkdir_missing_parent_still_raises_not_found_style_error(ssh_stub_fs: SSHFilesystem, tmp_path: Path) -> None:
    with pytest.raises(FileNotFoundError):
        ssh_stub_fs.mkdir(VPath(tmp_path / "no_such_parent" / "child", ssh_stub_fs))


@pytest.mark.asyncio
async def test_ssh_mkdir_creates_new_directory_and_refreshes_parent(ssh_stub_fs: SSHFilesystem, tmp_path: Path) -> None:
    parent = VPath(tmp_path, ssh_stub_fs)
    before = {vp.name async for vp in ssh_stub_fs.iterdir(parent)}
    assert "fresh" not in before
    ssh_stub_fs.mkdir(VPath(tmp_path / "fresh", ssh_stub_fs))
    assert os.path.isdir(tmp_path / "fresh")
    after = {vp.name async for vp in ssh_stub_fs.iterdir(parent)}
    assert "fresh" in after


def test_ssh_mkdir_race_existing_after_failure_is_file_exists() -> None:
    fs, _mock_ssh, mock_sftp = _make_fs()
    mock_sftp.mkdir.side_effect = OSError("Failure")
    mock_sftp.lstat.return_value = MagicMock()
    with pytest.raises(FileExistsError):
        fs.mkdir(fs.path("/home/user/racy"))


def test_ssh_mkdir_failure_without_existing_path_reraises_original_error() -> None:
    fs, _mock_ssh, mock_sftp = _make_fs()
    original = OSError("Failure")
    mock_sftp.mkdir.side_effect = original
    mock_sftp.lstat.side_effect = FileNotFoundError
    with pytest.raises(OSError, match="Failure") as info:
        fs.mkdir(fs.path("/home/user/missing/child"))
    assert info.value is original
    assert not isinstance(info.value, FileExistsError)
