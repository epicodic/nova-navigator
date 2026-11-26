"""SSHFilesystem symlink API against the stub SSH server (real shell, real files)."""

from __future__ import annotations

import errno
import os
from pathlib import Path
from unittest.mock import patch

import pytest

from nova_navigator.vfs.filesystems.ssh import SSHFilesystem
from nova_navigator.vfs.types import ExecResult
from nova_navigator.vfs.vpath import VPath


def test_ssh_supports_symlinks_and_agrees_with_capabilities(ssh_stub_fs: SSHFilesystem) -> None:
    assert ssh_stub_fs.supports_symlinks() is True
    assert ssh_stub_fs.capabilities.symlinks is True


def test_ssh_symlink_creates_link_with_identical_text(ssh_stub_fs: SSHFilesystem, tmp_path: Path) -> None:
    link = VPath(tmp_path / "l", ssh_stub_fs)
    ssh_stub_fs.symlink(link, "../some/relative")
    assert os.readlink(tmp_path / "l") == "../some/relative"
    assert ssh_stub_fs.readlink(link) == "../some/relative"


def test_ssh_symlink_refreshes_parent_listing(ssh_stub_fs: SSHFilesystem, tmp_path: Path) -> None:
    (tmp_path / "f.txt").write_text("x")
    before = ssh_stub_fs.stat(VPath(tmp_path / "f.txt", ssh_stub_fs))
    assert before.is_symlink is False
    ssh_stub_fs.symlink(VPath(tmp_path / "l", ssh_stub_fs), "f.txt")
    after = ssh_stub_fs.stat(VPath(tmp_path / "l", ssh_stub_fs))
    assert after.is_symlink is True


def test_ssh_real_path_resolves_directory_link(ssh_stub_fs: SSHFilesystem, tmp_path: Path) -> None:
    (tmp_path / "real").mkdir()
    (tmp_path / "lnk").symlink_to("real", target_is_directory=True)
    resolved = ssh_stub_fs.real_path(VPath(tmp_path / "lnk", ssh_stub_fs))
    assert resolved is not None
    assert resolved.filesystem is ssh_stub_fs
    assert resolved.path.as_posix() == os.path.realpath(tmp_path / "real")


def test_ssh_real_path_of_plain_directory_is_itself(ssh_stub_fs: SSHFilesystem, tmp_path: Path) -> None:
    resolved = ssh_stub_fs.real_path(VPath(tmp_path, ssh_stub_fs))
    assert resolved is not None
    assert resolved.path.as_posix() == os.path.realpath(tmp_path)


def test_ssh_real_path_of_non_utf8_reply_is_none(ssh_stub_fs: SSHFilesystem, tmp_path: Path) -> None:
    def boom(_path: bytes) -> str:
        raise UnicodeDecodeError("utf-8", b"\xff", 0, 1, "invalid start byte")

    with patch.object(ssh_stub_fs._sftp_client, "normalize", boom):
        assert ssh_stub_fs.real_path(VPath(tmp_path, ssh_stub_fs)) is None


def test_ssh_real_path_failure_is_oserror(ssh_stub_fs: SSHFilesystem, tmp_path: Path) -> None:
    def fail(_path: bytes) -> str:
        raise OSError(errno.ENOENT, "No such file")

    with patch.object(ssh_stub_fs._sftp_client, "normalize", fail), pytest.raises(OSError, match="No such file"):
        ssh_stub_fs.real_path(VPath(tmp_path / "x", ssh_stub_fs))


def test_ssh_readlink_falls_back_to_raw_bytes_on_undecodable_reply(ssh_stub_fs: SSHFilesystem, tmp_path: Path) -> None:
    raw_target = b"bad\xfftarget"
    os.symlink(raw_target, os.fsencode(tmp_path / "l"))

    def undecodable(_path: bytes) -> str:
        raise UnicodeDecodeError("utf-8", raw_target, 3, 4, "invalid start byte")

    with patch.object(ssh_stub_fs._sftp_client, "readlink", undecodable):
        text = ssh_stub_fs.readlink(VPath(tmp_path / "l", ssh_stub_fs))
    assert text == os.fsdecode(raw_target)
    assert os.fsencode(text) == raw_target


def test_ssh_readlink_fallback_keeps_trailing_newline_in_target(ssh_stub_fs: SSHFilesystem, tmp_path: Path) -> None:
    raw_target = b"t\n"
    os.symlink(raw_target, os.fsencode(tmp_path / "l"))

    def undecodable(_path: bytes) -> str:
        raise UnicodeDecodeError("utf-8", raw_target, 0, 1, "x")

    with patch.object(ssh_stub_fs._sftp_client, "readlink", undecodable):
        assert ssh_stub_fs.readlink(VPath(tmp_path / "l", ssh_stub_fs)) == "t\n"


def test_ssh_readlink_fallback_on_non_link_raises_oserror(ssh_stub_fs: SSHFilesystem, tmp_path: Path) -> None:
    (tmp_path / "plain").write_text("x")

    def undecodable(_path: bytes) -> str:
        raise UnicodeDecodeError("utf-8", b"\xff", 0, 1, "x")

    with patch.object(ssh_stub_fs._sftp_client, "readlink", undecodable), pytest.raises(OSError, match="not a symbolic link"):
        ssh_stub_fs.readlink(VPath(tmp_path / "plain", ssh_stub_fs))


@pytest.mark.usefixtures("stub_surrogateescape")
def test_ssh_symlink_creates_non_utf8_target_bytes_unchanged(ssh_stub_fs: SSHFilesystem, tmp_path: Path) -> None:
    raw_target = b"bad\xfftarget"
    ssh_stub_fs.symlink(VPath(tmp_path / "l", ssh_stub_fs), os.fsdecode(raw_target))
    assert os.readlink(os.fsencode(tmp_path / "l")) == raw_target


def test_ssh_readlink_fallback_truncated_hex_raises_oserror(ssh_stub_fs: SSHFilesystem, tmp_path: Path) -> None:
    os.symlink(b"t", os.fsencode(tmp_path / "l"))

    def undecodable(_path: bytes) -> str:
        raise UnicodeDecodeError("utf-8", b"\xff", 0, 1, "x")

    def truncated(_command: str, _cwd: VPath) -> ExecResult:
        return ExecResult(exit_code=0, output=" 7")

    with (
        patch.object(ssh_stub_fs._sftp_client, "readlink", undecodable),
        patch.object(ssh_stub_fs, "exec_command", truncated),
        pytest.raises(OSError, match="truncated"),
    ):
        ssh_stub_fs.readlink(VPath(tmp_path / "l", ssh_stub_fs))


@pytest.mark.usefixtures("stub_surrogateescape")
def test_ssh_readlink_fallback_quotes_awkward_link_path(ssh_stub_fs: SSHFilesystem, tmp_path: Path) -> None:
    raw_name = b"sp ace'quote\xff"
    link = tmp_path / os.fsdecode(raw_name)
    os.symlink(b"target", os.fsencode(link))
    scripts: list[str] = []
    real_exec = ssh_stub_fs.exec_command

    def recording(command: str, cwd: VPath) -> ExecResult:
        scripts.append(command)
        return real_exec(command, cwd)

    def undecodable(_path: bytes) -> str:
        raise UnicodeDecodeError("utf-8", b"\xff", 0, 1, "x")

    with patch.object(ssh_stub_fs._sftp_client, "readlink", undecodable), patch.object(ssh_stub_fs, "exec_command", recording):
        assert ssh_stub_fs.readlink(VPath(link, ssh_stub_fs)) == "target"
    assert len(scripts) == 1
    assert scripts[0].isascii()
    assert "readlink -n -- " in scripts[0]
    assert raw_name.decode("latin-1") not in scripts[0]
