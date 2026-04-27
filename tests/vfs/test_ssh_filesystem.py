"""Tests for SSHFilesystem using injected mock paramiko clients."""

from __future__ import annotations

from unittest.mock import MagicMock, patch

import paramiko
import pytest

from nova_navigator.vfs.filesystems.ssh import SSHFilesystem, _parse_stat_output
from nova_navigator.vfs.types import Stat

# ── helpers ───────────────────────────────────────────────────────────────────


def _stat_line(
    name: str,
    size: int = 100,
    perms: str = "644",
    owner: str = "user",
    mtime: float = 1000.0,
    atime: float = 1001.0,
    ctime: float = 1002.0,
    ftype: str = "regular file",
) -> str:
    return f"{size},{perms},{owner},{mtime},{atime},{ctime},{ftype},{name}"


def _make_fs(cwd: str = "/home/user") -> tuple[SSHFilesystem, MagicMock, MagicMock]:
    """Return (fs, mock_ssh, mock_sftp) with no real network activity."""
    mock_ssh = MagicMock(spec=paramiko.SSHClient)
    mock_sftp = MagicMock(spec=paramiko.SFTPClient)
    mock_ssh.open_sftp.return_value = mock_sftp
    mock_sftp.getcwd.return_value = cwd
    fs = SSHFilesystem("localhost", ssh_client=mock_ssh)
    return fs, mock_ssh, mock_sftp


def _set_exec_output(mock_ssh: MagicMock, output: str) -> None:
    """Configure mock_ssh.exec_command to return *output* as decoded stdout."""
    stdout = MagicMock()
    stdout.read.return_value = output.encode()
    mock_ssh.exec_command.return_value = (MagicMock(), stdout, MagicMock())


# ── _parse_stat_output ────────────────────────────────────────────────────────


def test_parse_stat_output_regular_file() -> None:
    line = _stat_line("file.txt", size=42, perms="644", mtime=1234.0, ftype="regular file")
    entries = _parse_stat_output(line)
    assert "file.txt" in entries
    e = entries["file.txt"]
    assert e.size == 42
    assert e.permissions == 0o644
    assert e.modified_time == 1234.0
    assert e.is_directory is False
    assert e.is_symlink is False


def test_parse_stat_output_directory() -> None:
    line = _stat_line("subdir", ftype="directory")
    entries = _parse_stat_output(line)
    assert entries["subdir"].is_directory is True
    assert entries["subdir"].is_symlink is False


def test_parse_stat_output_symlink() -> None:
    line = _stat_line("link.txt", ftype="symbolic link")
    entries = _parse_stat_output(line)
    assert entries["link.txt"].is_symlink is True
    assert entries["link.txt"].is_directory is False


def test_parse_stat_output_skips_malformed_lines() -> None:
    output = "not,enough,fields\n" + _stat_line("ok.txt")
    entries = _parse_stat_output(output)
    assert "ok.txt" in entries
    assert len(entries) == 1


def test_parse_stat_output_name_with_comma() -> None:
    line = _stat_line("file,with,commas.txt")
    entries = _parse_stat_output(line)
    assert "file,with,commas.txt" in entries


def test_parse_stat_output_multiple_entries() -> None:
    output = "\n".join(
        [
            _stat_line("a.txt"),
            _stat_line("b.txt", size=200),
            _stat_line("c/", ftype="directory"),
        ]
    )
    entries = _parse_stat_output(output)
    assert len(entries) == 3
    assert entries["b.txt"].size == 200


# ── __init__ ──────────────────────────────────────────────────────────────────


def test_init_with_injected_client() -> None:
    fs, mock_ssh, mock_sftp = _make_fs()
    mock_ssh.connect.assert_called_once_with("localhost", port=22)
    mock_ssh.open_sftp.assert_called_once()
    assert fs._ssh_client is mock_ssh
    assert fs._sftp_client is mock_sftp


def test_init_creates_new_client_when_none_provided() -> None:
    mock_ssh = MagicMock(spec=paramiko.SSHClient)
    mock_sftp = MagicMock(spec=paramiko.SFTPClient)
    mock_ssh.open_sftp.return_value = mock_sftp
    with patch("nova_navigator.vfs.filesystems.ssh.paramiko.SSHClient", return_value=mock_ssh):
        fs = SSHFilesystem("remotehost", port=2222)
    mock_ssh.load_system_host_keys.assert_called_once()
    mock_ssh.connect.assert_called_once_with("remotehost", port=2222)


# ── identity ──────────────────────────────────────────────────────────────────


def test_eq_same_ssh_client() -> None:
    fs1, mock_ssh, _ = _make_fs()
    fs2 = SSHFilesystem("localhost", ssh_client=mock_ssh)
    assert fs1 == fs2


def test_eq_different_ssh_client() -> None:
    fs1, _, _ = _make_fs()
    fs2, _, _ = _make_fs()
    assert fs1 != fs2


def test_eq_non_instance() -> None:
    fs, _, _ = _make_fs()
    assert fs != "not a filesystem"


def test_hash_is_consistent() -> None:
    fs, mock_ssh, _ = _make_fs()
    assert hash(fs) == hash(mock_ssh)


def test_repr_contains_class_name() -> None:
    fs, _, _ = _make_fs()
    assert "SSHFilesystem" in repr(fs)


# ── navigation ────────────────────────────────────────────────────────────────


def test_is_same_device_always_false() -> None:
    fs, _, _ = _make_fs()
    p = fs.path("/a")
    assert fs.is_same_device(p, p) is False


def test_cwd_returns_sftp_getcwd() -> None:
    fs, _, mock_sftp = _make_fs(cwd="/remote/dir")
    assert str(fs.cwd().path) == "/remote/dir"


def test_cwd_falls_back_to_root_when_none() -> None:
    fs, _, mock_sftp = _make_fs()
    mock_sftp.getcwd.return_value = None
    assert str(fs.cwd().path) == "/"


def test_root_returns_slash() -> None:
    fs, _, _ = _make_fs()
    assert str(fs.root().path) == "/"


def test_home_returns_home() -> None:
    fs, _, _ = _make_fs()
    assert str(fs.home().path) == "/home"


def test_parent_returns_parent_path() -> None:
    fs, _, _ = _make_fs()
    vp = fs.path("/home/user/file.txt")
    assert str(fs.parent(vp).path) == "/home/user"


# ── _dir_stat / iterdir ───────────────────────────────────────────────────────


def test_dir_stat_parses_exec_command_output() -> None:
    fs, mock_ssh, _ = _make_fs()
    output = "\n".join([_stat_line("a.txt"), _stat_line("b.txt", size=50)])
    _set_exec_output(mock_ssh, output)
    result = fs._dir_stat("/home/user", follow_symlinks=False)
    assert set(result.keys()) == {"a.txt", "b.txt"}


def test_iterdir_returns_vpaths() -> None:
    fs, mock_ssh, _ = _make_fs()
    output = "\n".join([_stat_line("x.txt"), _stat_line("y.txt")])
    _set_exec_output(mock_ssh, output)
    entries = fs.iterdir(fs.path("/home/user"))
    names = {vp.name for vp in entries}
    assert names == {"x.txt", "y.txt"}


# ── stat ──────────────────────────────────────────────────────────────────────


def test_stat_root_path() -> None:
    fs, _, _ = _make_fs()
    s = fs.stat(fs.path("/"))
    assert s.is_directory is True


def test_stat_regular_file() -> None:
    fs, mock_ssh, _ = _make_fs()
    line = _stat_line("file.txt", size=512, perms="755", mtime=9999.0)
    _set_exec_output(mock_ssh, line)
    s = fs.stat(fs.path("/home/user/file.txt"))
    assert s.size == 512
    assert s.is_directory is False
    assert s.is_executable is True
    assert s.is_hidden is False


def test_stat_hidden_file() -> None:
    fs, mock_ssh, _ = _make_fs()
    line = _stat_line(".hidden", perms="600")
    _set_exec_output(mock_ssh, line)
    s = fs.stat(fs.path("/home/user/.hidden"))
    assert s.is_hidden is True


def test_stat_symlink() -> None:
    fs, mock_ssh, _ = _make_fs()
    # follow_symlinks=True sees regular file; lstat sees symlink
    follow_line = _stat_line("link.txt", ftype="regular file")
    lstat_line = _stat_line("link.txt", ftype="symbolic link")
    call_count = 0

    def _exec_side_effect(cmd: str) -> tuple:
        nonlocal call_count
        stdout = MagicMock()
        stdout.read.return_value = (follow_line if "stat -L" in cmd else lstat_line).encode()
        call_count += 1
        return (MagicMock(), stdout, MagicMock())

    mock_ssh.exec_command.side_effect = _exec_side_effect
    # clear lru_cache so both calls are made
    fs._dir_stat.cache_clear()
    s = fs.stat(fs.path("/home/user/link.txt"))
    assert s.is_symlink is True


def test_stat_missing_file_returns_empty_stat() -> None:
    fs, mock_ssh, _ = _make_fs()
    _set_exec_output(mock_ssh, "")  # empty directory listing
    fs._dir_stat.cache_clear()
    s = fs.stat(fs.path("/home/user/missing.txt"))
    assert s == Stat()


# ── read / write ──────────────────────────────────────────────────────────────


def test_read_opens_sftp_file() -> None:
    fs, _, mock_sftp = _make_fs()
    vp = fs.path("/home/user/data.bin")
    fs.read(vp)
    mock_sftp.open.assert_called_once_with("/home/user/data.bin", "rb")


def test_write_opens_sftp_file() -> None:
    fs, _, mock_sftp = _make_fs()
    vp = fs.path("/home/user/out.bin")
    fs.write(vp)
    mock_sftp.open.assert_called_once_with("/home/user/out.bin", "wb")


# ── mutating operations ───────────────────────────────────────────────────────


def test_remove_calls_sftp_remove() -> None:
    fs, _, mock_sftp = _make_fs()
    vp = fs.path("/home/user/file.txt")
    fs.remove(vp)
    mock_sftp.remove.assert_called_once_with("/home/user/file.txt")


def test_rename_calls_sftp_rename() -> None:
    fs, _, mock_sftp = _make_fs()
    src = fs.path("/home/user/old.txt")
    dst = fs.path("/home/user/new.txt")
    fs.rename(src, dst)
    mock_sftp.rename.assert_called_once_with("/home/user/old.txt", "/home/user/new.txt")


def test_rmdir_calls_sftp_rmdir() -> None:
    fs, _, mock_sftp = _make_fs()
    vp = fs.path("/home/user/emptydir")
    fs.rmdir(vp)
    mock_sftp.rmdir.assert_called_once_with("/home/user/emptydir")


def test_mkdir_calls_sftp_mkdir() -> None:
    fs, _, mock_sftp = _make_fs()
    vp = fs.path("/home/user/newdir")
    fs.mkdir(vp)
    mock_sftp.mkdir.assert_called_once_with("/home/user/newdir")


# ── copy_stat ─────────────────────────────────────────────────────────────────


def test_copy_stat_sets_mtime_and_mode() -> None:
    fs, _, mock_sftp = _make_fs()
    vp = fs.path("/home/user/file.txt")
    fs.copy_stat(vp, Stat(modified=5000.0, mode=0o644))
    mock_sftp.utime.assert_called_once_with("/home/user/file.txt", (5000.0, 5000.0))
    mock_sftp.chmod.assert_called_once_with("/home/user/file.txt", 0o644)


def test_copy_stat_skips_negative_values() -> None:
    fs, _, mock_sftp = _make_fs()
    vp = fs.path("/home/user/file.txt")
    fs.copy_stat(vp, Stat(modified=-1.0, mode=-1))
    mock_sftp.utime.assert_not_called()
    mock_sftp.chmod.assert_not_called()


# ── readlink ──────────────────────────────────────────────────────────────────


def test_readlink_returns_target() -> None:
    fs, _, mock_sftp = _make_fs()
    mock_sftp.readlink.return_value = "/real/target.txt"
    vp = fs.path("/home/user/link.txt")
    assert fs.readlink(vp) == "/real/target.txt"


def test_readlink_raises_oserror_when_not_a_link() -> None:
    fs, _, mock_sftp = _make_fs()
    mock_sftp.readlink.return_value = None
    vp = fs.path("/home/user/file.txt")
    with pytest.raises(OSError):
        fs.readlink(vp)
