"""Tests for SSHFilesystem using injected mock paramiko clients."""

from __future__ import annotations

import os
import shlex
import subprocess
from unittest.mock import MagicMock, patch

import paramiko
import pytest

from nova_navigator.vfs.filesystems.ssh import SSHFilesystem, _build_list_command, _parse_stat_output, _quote_path, _sftp_path
from nova_navigator.vfs.types import Stat

# ── helpers ───────────────────────────────────────────────────────────────────


def _stat_record(
    name: str,
    size: int = 100,
    perms: str = "644",
    owner: str = "user",
    mtime: float = 1000.0,
    atime: float = 1001.0,
    ctime: float = 1002.0,
    ftype: str = "regular file",
) -> bytes:
    """Return one NUL-terminated eight-field ``stat --printf`` record."""
    fields = [str(size), perms, owner, str(int(mtime)), str(int(atime)), str(int(ctime)), ftype]
    return b"".join(f.encode() + b"\0" for f in fields) + os.fsencode(name) + b"\0"


def _make_fs(cwd: str = "/home/user") -> tuple[SSHFilesystem, MagicMock, MagicMock]:
    """Return (fs, mock_ssh, mock_sftp) with no real network activity."""
    mock_ssh = MagicMock(spec=paramiko.SSHClient)
    mock_sftp = MagicMock(spec=paramiko.SFTPClient)
    mock_ssh.open_sftp.return_value = mock_sftp
    mock_sftp.getcwd.return_value = cwd
    fs = SSHFilesystem("localhost", ssh_client=mock_ssh)
    return fs, mock_ssh, mock_sftp


def _set_exec_output(mock_ssh: MagicMock, output: bytes) -> None:
    """Configure mock_ssh.exec_command to return *output* as raw stdout bytes."""
    stdout = MagicMock()
    stdout.read.return_value = output
    stdout.channel.recv_exit_status.return_value = 0
    mock_ssh.exec_command.return_value = (MagicMock(), stdout, MagicMock())


def _set_exec_error(mock_ssh: MagicMock, exit_code: int, stderr_text: str) -> None:
    """Configure mock_ssh.exec_command to simulate a failed command (empty stdout)."""
    stdout = MagicMock()
    stdout.read.return_value = b""
    stdout.channel.recv_exit_status.return_value = exit_code
    stderr = MagicMock()
    stderr.read.return_value = stderr_text.encode()
    mock_ssh.exec_command.return_value = (MagicMock(), stdout, stderr)


# ── _parse_stat_output ────────────────────────────────────────────────────────


def test_parse_stat_output_regular_file() -> None:
    line = _stat_record("file.txt", size=42, perms="644", mtime=1234.0, ftype="regular file")
    entries = _parse_stat_output(line)
    assert "file.txt" in entries
    e = entries["file.txt"]
    assert e.size == 42
    assert e.permissions == 0o644
    assert e.modified_time == 1234.0
    assert e.is_directory is False
    assert e.is_symlink is False


def test_parse_stat_output_directory() -> None:
    line = _stat_record("subdir", ftype="directory")
    entries = _parse_stat_output(line)
    assert entries["subdir"].is_directory is True
    assert entries["subdir"].is_symlink is False


def test_parse_stat_output_symlink() -> None:
    line = _stat_record("link.txt", ftype="symbolic link")
    entries = _parse_stat_output(line)
    assert entries["link.txt"].is_symlink is True
    assert entries["link.txt"].is_directory is False


@pytest.mark.parametrize(
    "name",
    ["a,b", "a\nb", "a b", "-dash", "--", ",", " lead", "trail ", "a\r\n", "ünï"],
)
def test_parse_stat_output_awkward_names(name: str) -> None:
    entries = _parse_stat_output(_stat_record("first") + _stat_record(name) + _stat_record("last"))
    assert set(entries) == {"first", name, "last"}
    assert entries[name].name == name


def test_parse_stat_output_non_utf8_name_uses_surrogateescape() -> None:
    entries = _parse_stat_output(_stat_record("bad\udcff"))
    assert list(entries) == ["bad\udcff"]
    assert os.fsencode("bad\udcff") == b"bad\xff"


def test_parse_stat_output_strips_dot_slash_prefix_once() -> None:
    record = _stat_record("x")
    raw = record[: -len(b"x\0")] + b"././/y\0"
    assert list(_parse_stat_output(raw)) == [".//y"]


def test_parse_stat_output_drops_truncated_trailing_record() -> None:
    output = _stat_record("a") + _stat_record("b")
    fields = output.split(b"\0")
    truncated = b"\0".join(fields[:12]) + b"\0"  # 8 + 4 fields
    assert list(_parse_stat_output(truncated)) == ["a"]


def test_parse_stat_output_drops_partial_trailing_field() -> None:
    output = _stat_record("a") + _stat_record("b")
    assert list(_parse_stat_output(output[:-3])) == ["a"]


def test_parse_stat_output_stops_at_unparsable_record() -> None:
    bad = b"notanumber\0" + _stat_record("b")[len(b"100\0") :]
    assert list(_parse_stat_output(_stat_record("a") + bad + _stat_record("c"))) == ["a"]


def test_parse_stat_output_empty_bytes() -> None:
    assert _parse_stat_output(b"") == {}


def test_parse_stat_output_multiple_entries() -> None:
    output = _stat_record("a.txt") + _stat_record("b.txt", size=200) + _stat_record("c", ftype="directory")
    entries = _parse_stat_output(output)
    assert len(entries) == 3
    assert entries["b.txt"].size == 200


# ── _build_list_command ───────────────────────────────────────────────────────

_HOSTILE_PATHS = [
    "/data/a b",
    "/data/it's",
    '/data/say "hi"',
    "/data/$HOME",
    "/data/a;b",
    "/data/*",
    "/data/`id`",
    "/data/a\nb",
    "/data/-dash",
    "/data/x; touch /data/pwned; #",
]


@pytest.mark.parametrize("path", _HOSTILE_PATHS)
def test_build_list_command_quotes_path(path: str) -> None:
    command = _build_list_command(path, follow_symlinks=False)
    outer = shlex.split(command)
    assert outer[:2] == ["sh", "-c"]
    assert len(outer) == 3
    assert shlex.split(outer[2])[:3] == ["cd", "--", path]


@pytest.mark.parametrize("follow", [True, False])
def test_build_list_command_follow_flag(follow: bool) -> None:
    script = shlex.split(_build_list_command("/data/d", follow_symlinks=follow))[2]
    assert ("stat -L --printf" in script) is follow
    assert ("stat --printf" in script) is (not follow)
    assert "\\0" in script
    assert "-- {}" in script


@pytest.mark.parametrize("raw", [b"/data/bad\xffdir", b"/data/nl\n", b"/data/\xff\n\n", b"/data/it's \xfe$x", b"/data/plain"])
def test_quote_path_is_ascii_and_evaluates_to_exact_bytes(raw: bytes) -> None:
    prelude, word = _quote_path(os.fsdecode(raw))
    assert (prelude + word).isascii()
    result = subprocess.run(["sh", "-c", f"{prelude}\nprintf %s {word}"], capture_output=True, check=True)
    assert result.stdout == raw


def test_quote_path_valid_utf8_uses_shlex_quote() -> None:
    assert _quote_path("/data/a b") == ("", shlex.quote("/data/a b"))
    assert _quote_path("/data/\u00e4") == ("", shlex.quote("/data/\u00e4"))


def test_build_list_command_non_utf8_path_is_ascii() -> None:
    assert _build_list_command(os.fsdecode(b"/data/bad\xff\n"), follow_symlinks=True).isascii()


def test_sftp_path_encodes_surrogates_back_to_original_bytes() -> None:
    fs, _, _ = _make_fs()
    assert _sftp_path(fs.path(os.fsdecode(b"/home/bad\xff"))) == b"/home/bad\xff"


# ── __init__ ──────────────────────────────────────────────────────────────────


def test_init_with_injected_client() -> None:
    fs, mock_ssh, mock_sftp = _make_fs()
    mock_ssh.connect.assert_called_once_with("localhost", port=22, username=None, key_filename=None, password=None)
    mock_ssh.open_sftp.assert_called_once()
    assert fs._ssh_client is mock_ssh
    assert fs._sftp_client is mock_sftp


def test_init_creates_new_client_when_none_provided() -> None:
    mock_ssh = MagicMock(spec=paramiko.SSHClient)
    mock_sftp = MagicMock(spec=paramiko.SFTPClient)
    mock_ssh.open_sftp.return_value = mock_sftp
    with patch("nova_navigator.vfs.filesystems.ssh.paramiko.SSHClient", return_value=mock_ssh):
        SSHFilesystem("remotehost", port=2222)
    mock_ssh.load_system_host_keys.assert_called_once()
    mock_ssh.connect.assert_called_once_with("remotehost", port=2222, username=None, key_filename=None, password=None)


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
    fs, _, _ = _make_fs(cwd="/remote/dir")
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
    output = _stat_record("a.txt") + _stat_record("b.txt", size=50)
    _set_exec_output(mock_ssh, output)
    result = fs._dir_stat("/home/user", follow_symlinks=False)
    assert set(result.keys()) == {"a.txt", "b.txt"}


def test_dir_stat_raises_permission_error_on_exit_120() -> None:
    fs, mock_ssh, _ = _make_fs()
    _set_exec_error(mock_ssh, exit_code=120, stderr_text="")
    with pytest.raises(PermissionError):
        fs._dir_stat("/restricted", follow_symlinks=False)


def test_dir_stat_returns_empty_for_missing_directory() -> None:
    fs, mock_ssh, _ = _make_fs()
    _set_exec_error(mock_ssh, exit_code=121, stderr_text="")
    assert fs._dir_stat("/missing", follow_symlinks=False) == {}


def test_dir_stat_parses_partial_output_with_nonzero_exit() -> None:
    fs, mock_ssh, _ = _make_fs()
    _set_exec_output(mock_ssh, _stat_record("ok.txt"))
    mock_ssh.exec_command.return_value[1].channel.recv_exit_status.return_value = 1
    assert set(fs._dir_stat("/home/user", follow_symlinks=True)) == {"ok.txt"}


def test_dir_stat_decodes_non_utf8_stderr_without_raising() -> None:
    fs, mock_ssh, _ = _make_fs()
    _set_exec_error(mock_ssh, exit_code=1, stderr_text="")
    mock_ssh.exec_command.return_value[2].read.return_value = b"stat: bad\xff: Permission denied\n"
    with pytest.raises(PermissionError):
        fs._dir_stat("/restricted", follow_symlinks=False)


@pytest.mark.asyncio
async def test_iterdir_issues_one_exec_per_follow_mode() -> None:
    fs, mock_ssh, _ = _make_fs()
    _set_exec_output(mock_ssh, _stat_record("x.txt"))
    for _ in range(2):
        assert {vp.name async for vp in fs.iterdir(fs.path("/home/user"))} == {"x.txt"}
    assert mock_ssh.exec_command.call_count == 2
    commands = [c.args[0] for c in mock_ssh.exec_command.call_args_list]
    assert sum("stat -L" in c for c in commands) == 1


def test_dir_stat_raises_permission_error_when_cd_fails() -> None:
    fs, mock_ssh, _ = _make_fs()
    _set_exec_error(mock_ssh, exit_code=1, stderr_text="cd: /restricted: Permission denied\n")
    with pytest.raises(PermissionError):
        fs._dir_stat("/restricted", follow_symlinks=False)


def test_dir_stat_returns_empty_for_empty_directory() -> None:
    """A non-zero exit with no 'permission denied' in stderr is an empty dir (glob no-match)."""
    fs, mock_ssh, _ = _make_fs()
    _set_exec_error(mock_ssh, exit_code=1, stderr_text="zsh: no matches found: *(D)\n")
    result = fs._dir_stat("/home/user/empty", follow_symlinks=False)
    assert result == {}


@pytest.mark.asyncio
async def test_iterdir_returns_vpaths() -> None:
    fs, mock_ssh, _ = _make_fs()
    output = _stat_record("x.txt") + _stat_record("y.txt")
    _set_exec_output(mock_ssh, output)
    names = {vp.name async for vp in fs.iterdir(fs.path("/home/user"))}
    assert names == {"x.txt", "y.txt"}


# ── stat ──────────────────────────────────────────────────────────────────────


def test_stat_root_path() -> None:
    fs, _, _ = _make_fs()
    s = fs.stat(fs.path("/"))
    assert s.is_directory is True


def test_stat_regular_file() -> None:
    fs, mock_ssh, _ = _make_fs()
    line = _stat_record("file.txt", size=512, perms="755", mtime=9999.0)
    _set_exec_output(mock_ssh, line)
    s = fs.stat(fs.path("/home/user/file.txt"))
    assert s.size == 512
    assert s.is_directory is False
    assert s.is_executable is True
    assert s.is_hidden is False


def test_stat_hidden_file() -> None:
    fs, mock_ssh, _ = _make_fs()
    line = _stat_record(".hidden", perms="600")
    _set_exec_output(mock_ssh, line)
    s = fs.stat(fs.path("/home/user/.hidden"))
    assert s.is_hidden is True


def test_stat_symlink() -> None:
    fs, mock_ssh, _ = _make_fs()
    # follow_symlinks=True sees regular file; lstat sees symlink
    follow_line = _stat_record("link.txt", ftype="regular file")
    lstat_line = _stat_record("link.txt", ftype="symbolic link")
    call_count = 0

    def _exec_side_effect(cmd: str) -> tuple:
        nonlocal call_count
        stdout = MagicMock()
        stdout.read.return_value = follow_line if "stat -L" in cmd else lstat_line
        stdout.channel.recv_exit_status.return_value = 0
        call_count += 1
        return (MagicMock(), stdout, MagicMock())

    mock_ssh.exec_command.side_effect = _exec_side_effect
    # clear cache so both calls are made
    fs.refresh()
    s = fs.stat(fs.path("/home/user/link.txt"))
    assert s.is_symlink is True


def test_stat_missing_file_raises_file_not_found() -> None:
    fs, mock_ssh, _ = _make_fs()
    _set_exec_output(mock_ssh, b"")  # empty directory listing
    fs.refresh()
    with pytest.raises(FileNotFoundError):
        fs.stat(fs.path("/home/user/missing.txt"))


# ── read / write ──────────────────────────────────────────────────────────────


def test_read_opens_sftp_file() -> None:
    fs, _, mock_sftp = _make_fs()
    vp = fs.path("/home/user/data.bin")
    fs.read(vp)
    mock_sftp.open.assert_called_once_with(b"/home/user/data.bin", "rb")


def test_write_opens_sftp_file() -> None:
    fs, _, mock_sftp = _make_fs()
    vp = fs.path("/home/user/out.bin")
    fs.write(vp)
    mock_sftp.open.assert_called_once_with(b"/home/user/out.bin", "wb")


# ── mutating operations ───────────────────────────────────────────────────────


def test_remove_calls_sftp_remove() -> None:
    fs, _, mock_sftp = _make_fs()
    vp = fs.path("/home/user/file.txt")
    fs.remove(vp)
    mock_sftp.remove.assert_called_once_with(b"/home/user/file.txt")


def test_rename_calls_sftp_rename() -> None:
    fs, _, mock_sftp = _make_fs()
    src = fs.path("/home/user/old.txt")
    dst = fs.path("/home/user/new.txt")
    fs.rename(src, dst)
    mock_sftp.rename.assert_called_once_with(b"/home/user/old.txt", b"/home/user/new.txt")


def test_rmdir_calls_sftp_rmdir() -> None:
    fs, _, mock_sftp = _make_fs()
    vp = fs.path("/home/user/emptydir")
    fs.rmdir(vp)
    mock_sftp.rmdir.assert_called_once_with(b"/home/user/emptydir")


def test_mkdir_calls_sftp_mkdir() -> None:
    fs, _, mock_sftp = _make_fs()
    vp = fs.path("/home/user/newdir")
    fs.mkdir(vp)
    mock_sftp.mkdir.assert_called_once_with(b"/home/user/newdir")


# ── copy_stat ─────────────────────────────────────────────────────────────────


def test_copy_stat_sets_mtime_and_mode() -> None:
    fs, _, mock_sftp = _make_fs()
    vp = fs.path("/home/user/file.txt")
    fs.copy_stat(vp, Stat(modified=5000.0, mode=0o644))
    mock_sftp.utime.assert_called_once_with(b"/home/user/file.txt", (5000.0, 5000.0))
    mock_sftp.chmod.assert_called_once_with(b"/home/user/file.txt", 0o644)


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
    with pytest.raises(OSError, match="not a symbolic link"):
        fs.readlink(vp)


# ── dict cache ────────────────────────────────────────────────────────────────


def test_dir_stat_caches_result() -> None:
    """Second call with same args must not fire exec_command again."""
    fs, mock_ssh, _ = _make_fs()
    output = _stat_record("f.txt")
    _set_exec_output(mock_ssh, output)
    fs._dir_stat("/home/user", follow_symlinks=False)
    fs._dir_stat("/home/user", follow_symlinks=False)
    assert mock_ssh.exec_command.call_count == 1


def test_refresh_path_evicts_both_follow_symlink_variants() -> None:
    fs, mock_ssh, _ = _make_fs()
    output = _stat_record("f.txt")
    _set_exec_output(mock_ssh, output)
    # warm both cache keys
    fs._dir_stat("/home/user", follow_symlinks=False)
    fs._dir_stat("/home/user", follow_symlinks=True)
    assert mock_ssh.exec_command.call_count == 2
    # evict
    fs.refresh(fs.path("/home/user"))
    # both keys must have been removed
    assert ("/home/user", False) not in fs._stat_cache
    assert ("/home/user", True) not in fs._stat_cache


def test_refresh_none_clears_all_cache_entries() -> None:
    fs, mock_ssh, _ = _make_fs()
    _set_exec_output(mock_ssh, _stat_record("f.txt"))
    fs._dir_stat("/home/user", follow_symlinks=False)
    fs._dir_stat("/var/log", follow_symlinks=False)
    assert len(fs._stat_cache) == 2
    fs.refresh()
    assert len(fs._stat_cache) == 0


def test_refresh_nonexistent_path_is_safe() -> None:
    fs, _, _ = _make_fs()
    fs.refresh(fs.path("/no/such/dir"))  # must not raise


# ── _PipelinedWriter ──────────────────────────────────────────────────────────


def test_write_returns_pipelined_writer() -> None:
    fs, _, mock_sftp = _make_fs()
    mock_file = MagicMock()
    mock_sftp.open.return_value = mock_file
    writer = fs.write(fs.path("/home/user/out.bin"))
    mock_file.set_pipelined.assert_called_once_with(True)
    writer.write(b"hello")
    mock_file.write.assert_called_once_with(b"hello")


def test_write_close_evicts_parent_cache() -> None:
    fs, mock_ssh, mock_sftp = _make_fs()
    # warm cache for /home/user
    _set_exec_output(mock_ssh, _stat_record("out.bin"))
    fs._dir_stat("/home/user", follow_symlinks=False)
    assert ("/home/user", False) in fs._stat_cache
    # write and close
    mock_sftp.open.return_value = MagicMock()
    writer = fs.write(fs.path("/home/user/out.bin"))
    writer.close()
    assert ("/home/user", False) not in fs._stat_cache


# ── post-mutation eviction ────────────────────────────────────────────────────


def test_remove_evicts_parent_cache() -> None:
    fs, mock_ssh, _ = _make_fs()
    _set_exec_output(mock_ssh, _stat_record("file.txt"))
    fs._dir_stat("/home/user", follow_symlinks=False)
    fs.remove(fs.path("/home/user/file.txt"))
    assert ("/home/user", False) not in fs._stat_cache


def test_rename_evicts_both_parent_caches() -> None:
    fs, mock_ssh, _ = _make_fs()
    _set_exec_output(mock_ssh, _stat_record("old.txt"))
    fs._dir_stat("/home/user", follow_symlinks=False)
    fs._dir_stat("/var/log", follow_symlinks=False)
    fs.rename(fs.path("/home/user/old.txt"), fs.path("/var/log/new.txt"))
    assert ("/home/user", False) not in fs._stat_cache
    assert ("/var/log", False) not in fs._stat_cache


def test_mkdir_evicts_parent_cache() -> None:
    fs, mock_ssh, _ = _make_fs()
    _set_exec_output(mock_ssh, _stat_record("subdir", ftype="directory"))
    fs._dir_stat("/home/user", follow_symlinks=False)
    fs.mkdir(fs.path("/home/user/newdir"))
    assert ("/home/user", False) not in fs._stat_cache


def test_rmdir_evicts_parent_cache() -> None:
    fs, mock_ssh, _ = _make_fs()
    _set_exec_output(mock_ssh, _stat_record("emptydir", ftype="directory"))
    fs._dir_stat("/home/user", follow_symlinks=False)
    fs.rmdir(fs.path("/home/user/emptydir"))
    assert ("/home/user", False) not in fs._stat_cache
