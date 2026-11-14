"""Directory listing of SSHFilesystem through SFTP (REQ-21): primitives, attribute mapping and equality with the GNU listing."""

from __future__ import annotations

import os
from collections.abc import Generator
from pathlib import Path
from unittest.mock import patch

import paramiko
import pytest
from paramiko.sftp import CMD_CLOSE, CMD_HANDLE, CMD_NAME, CMD_OPENDIR, CMD_READDIR

from nova_navigator.vfs.filesystems import ssh as ssh_module
from nova_navigator.vfs.filesystems.ssh import SSHFilesystem, StatEntry, _entry_from_attrs, _read_attrs, _sftp_readdir, _SftpAttrs
from nova_navigator.vfs.vpath import VPath
from tests._utils.stub_ssh_server import StubSSHServer

_LOGIN_WORD = "testpass"
_AWKWARD = ["a,b", "a\nb", "a b", "-dash", "--", ",", " lead", "trail ", "*(D)", "it's", 'say "hi"']


class _AcceptAnyHostKey(paramiko.MissingHostKeyPolicy):
    def missing_host_key(self, client: paramiko.SSHClient, hostname: str, key: paramiko.PKey) -> None:
        return


@pytest.fixture(scope="module")
def server(tmp_path_factory: pytest.TempPathFactory) -> Generator[StubSSHServer, None, None]:
    server = StubSSHServer(root_dir=tmp_path_factory.mktemp("ssh_listing_sftp_root"))
    server.start()
    yield server
    server.stop()


@pytest.fixture
def fs(server: StubSSHServer) -> Generator[SSHFilesystem, None, None]:
    client = paramiko.SSHClient()
    client.set_missing_host_key_policy(_AcceptAnyHostKey())
    filesystem = SSHFilesystem(hostname=server.host, port=server.port, username="testuser", password=_LOGIN_WORD, ssh_client=client)
    yield filesystem
    client.close()


def _write_bytes_named(raw_name: bytes, content: bytes) -> None:
    with open(raw_name, "wb") as handle:
        handle.write(content)


def _make_tree(root: Path) -> None:
    (root / "file.txt").write_text("hello")
    (root / "run.sh").write_text("#!/bin/sh\n")
    (root / "run.sh").chmod(0o755)
    (root / ".hidden").write_text("h")
    (root / "folder").mkdir()
    (root / "folder" / "inner.txt").write_text("i")
    (root / "link_to_file").symlink_to("file.txt")
    (root / "link_to_folder").symlink_to("folder", target_is_directory=True)
    (root / "broken").symlink_to("missing-target")
    (root / "loop").symlink_to("loop")
    for name in _AWKWARD:
        (root / name).write_text(name)
    _write_bytes_named(os.fsencode(root) + b"/bad\xffname", b"bad")


def _core(entries: dict[str, StatEntry]) -> dict[str, tuple[int, int, float, bool, bool]]:
    """The fields REQ-21 names (owner, atime and ctime are not available over SFTP and never reach ``Stat``)."""
    return {n: (e.size, e.permissions, e.modified_time, e.is_directory, e.is_symlink) for n, e in entries.items()}


# --- listing equality with the GNU command ------------------------------------------------------------------------


@pytest.mark.parametrize("follow", [True, False])
def test_sftp_listing_matches_the_gnu_listing(fs: SSHFilesystem, tmp_path: Path, follow: bool) -> None:
    _make_tree(tmp_path)
    path = str(tmp_path)
    gnu = fs._dir_stat(path, follow)
    fs.refresh(VPath(tmp_path, fs))
    sftp = fs._sftp_dir_stat(path, follow)
    assert _core(sftp) == _core(gnu)
    assert sftp.keys() == gnu.keys()


def test_sftp_listing_follows_links_for_the_follow_pass(fs: SSHFilesystem, tmp_path: Path) -> None:
    _make_tree(tmp_path)
    followed = fs._sftp_dir_stat(str(tmp_path), True)
    lstat = fs._sftp_dir_stat(str(tmp_path), False)
    assert followed["link_to_folder"].is_directory is True
    assert followed["link_to_folder"].is_symlink is False
    assert followed["link_to_file"].size == len("hello")
    assert lstat["link_to_folder"].is_symlink is True
    assert lstat["link_to_folder"].is_directory is False
    assert "broken" in lstat
    assert "broken" not in followed  # iterdir/stat turn "in lstat, missing in follow" into a broken link
    assert "loop" in lstat
    assert "loop" not in followed


def test_sftp_listing_reports_each_awkward_name_once(fs: SSHFilesystem, tmp_path: Path) -> None:
    _make_tree(tmp_path)
    names = set(fs._sftp_dir_stat(str(tmp_path), False))
    assert set(_AWKWARD) <= names
    assert os.fsdecode(b"bad\xffname") in names
    assert "." not in names
    assert ".." not in names


def test_sftp_listing_fills_both_cache_keys_in_one_pass(fs: SSHFilesystem, tmp_path: Path) -> None:
    _make_tree(tmp_path)
    with patch.object(ssh_module, "_sftp_readdir", wraps=ssh_module._sftp_readdir) as readdir:
        requested = fs._sftp_dir_stat(str(tmp_path), True)
    assert readdir.call_count == 1
    assert fs._stat_cache[(str(tmp_path), True)] is requested
    assert (str(tmp_path), False) in fs._stat_cache


@pytest.mark.usefixtures("stub_surrogateescape")
def test_sftp_listing_follows_a_link_with_a_non_utf8_name(fs: SSHFilesystem, tmp_path: Path) -> None:
    (tmp_path / "target.txt").write_text("t")
    os.symlink(os.fsencode("target.txt"), os.fsencode(tmp_path) + b"/bad\xfflink")
    name = os.fsdecode(b"bad\xfflink")
    assert fs._sftp_dir_stat(str(tmp_path), False)[name].is_symlink is True
    followed = fs._sftp_dir_stat(str(tmp_path), True)[name]
    assert followed.is_symlink is False
    assert followed.size == 1


def test_sftp_listing_spans_several_readdir_packets(fs: SSHFilesystem, tmp_path: Path) -> None:
    expected = {f"file-{index:02d}" for index in range(40)}
    for name in expected:
        (tmp_path / name).write_text("x")
    assert set(fs._sftp_dir_stat(str(tmp_path), False)) == expected


# --- empty, missing, file, permission, transport -------------------------------------------------------------------


def test_sftp_listing_of_an_empty_directory_is_empty(fs: SSHFilesystem, tmp_path: Path) -> None:
    assert fs._sftp_dir_stat(str(tmp_path), True) == {}


def test_sftp_listing_of_a_missing_directory_is_empty(fs: SSHFilesystem, tmp_path: Path) -> None:
    assert fs._sftp_dir_stat(str(tmp_path / "nope"), True) == {}


def test_sftp_listing_of_a_file_is_empty(fs: SSHFilesystem, tmp_path: Path) -> None:
    (tmp_path / "plain").write_text("x")
    assert fs._sftp_dir_stat(str(tmp_path / "plain"), True) == {}


@pytest.mark.skipif(os.geteuid() == 0, reason="root ignores directory permissions")
def test_sftp_listing_of_an_unreadable_directory_raises_permission_error(fs: SSHFilesystem, tmp_path: Path) -> None:
    locked = tmp_path / "locked"
    locked.mkdir()
    (locked / "f").write_text("")
    locked.chmod(0o000)
    try:
        with pytest.raises(PermissionError):
            fs._sftp_dir_stat(str(locked), True)
    finally:
        locked.chmod(0o755)


def test_sftp_listing_transport_failure_is_an_oserror(fs: SSHFilesystem, tmp_path: Path) -> None:
    with patch.object(ssh_module, "_sftp_readdir", side_effect=paramiko.SSHException("gone")), pytest.raises(OSError, match="gone"):
        fs._sftp_dir_stat(str(tmp_path), True)


def test_sftp_listing_link_stat_transport_failure_is_not_a_broken_link(fs: SSHFilesystem, tmp_path: Path) -> None:
    (tmp_path / "t").write_text("x")
    (tmp_path / "l").symlink_to("t")
    with patch.object(fs._sftp_client, "stat", side_effect=paramiko.SSHException("gone")), pytest.raises(OSError, match="gone"):
        fs._sftp_dir_stat(str(tmp_path), True)


def test_sftp_listing_failed_not_cached(fs: SSHFilesystem, tmp_path: Path) -> None:
    with patch.object(ssh_module, "_sftp_readdir", side_effect=paramiko.SSHException("gone")), pytest.raises(OSError, match="gone"):
        fs._sftp_dir_stat(str(tmp_path), True)
    assert (str(tmp_path), True) not in fs._stat_cache


# --- primitives: attribute mapping, ATTRS parsing, READDIR loop ----------------------------------------------------


def _attrs_message(*, size: int | None, mode: int | None, times: tuple[int, int] | None, uid_gid: bool = False, extended: int = 0, trailer: int | None = None) -> paramiko.Message:
    flags = paramiko.SFTPAttributes.FLAG_SIZE if size is not None else 0
    flags |= paramiko.SFTPAttributes.FLAG_UIDGID if uid_gid else 0
    flags |= paramiko.SFTPAttributes.FLAG_PERMISSIONS if mode is not None else 0
    flags |= paramiko.SFTPAttributes.FLAG_AMTIME if times is not None else 0
    flags |= 0x80000000 if extended else 0
    msg = paramiko.Message()
    msg.add_int(flags)
    if size is not None:
        msg.add_int64(size)
    if uid_gid:
        msg.add_int(1000)
        msg.add_int(1000)
    if mode is not None:
        msg.add_int(mode)
    if times is not None:
        msg.add_int(times[0])
        msg.add_int(times[1])
    if extended:
        msg.add_int(extended)
        for index in range(extended):
            msg.add_string(f"key{index}".encode())
            msg.add_string(b"value")
    if trailer is not None:
        msg.add_int(trailer)
    msg.rewind()
    return msg


def test_read_attrs_reads_every_flag_and_consumes_exactly_its_bytes() -> None:
    msg = _attrs_message(size=7, mode=0o100644, times=(11, 22), uid_gid=True, extended=2, trailer=99)
    assert _read_attrs(msg) == _SftpAttrs(size=7, mode=0o100644, atime=11, mtime=22)
    assert msg.get_int() == 99  # the trailing field proves nothing was over- or under-read


def test_read_attrs_without_flags_has_no_values() -> None:
    assert _read_attrs(_attrs_message(size=None, mode=None, times=None)) == _SftpAttrs(size=None, mode=None, atime=None, mtime=None)


@pytest.mark.parametrize(
    ("mode", "permissions", "is_directory", "is_symlink"),
    [(0o040755, 0o755, True, False), (0o120777, 0o777, False, True), (0o100644, 0o644, False, False), (0o104755, 0o4755, False, False), (None, 0, False, False)],
)
def test_entry_from_attrs_maps_mode(mode: int | None, permissions: int, is_directory: bool, is_symlink: bool) -> None:
    entry = _entry_from_attrs("n", _SftpAttrs(size=5, mode=mode, atime=3, mtime=4))
    assert (entry.permissions, entry.is_directory, entry.is_symlink) == (permissions, is_directory, is_symlink)
    assert (entry.name, entry.size, entry.modified_time, entry.access_time, entry.change_time, entry.owner) == ("n", 5, 4.0, 3.0, 0.0, "")


def test_entry_from_attrs_defaults_missing_values() -> None:
    entry = _entry_from_attrs("n", _SftpAttrs(size=None, mode=None, atime=None, mtime=None))
    assert (entry.size, entry.modified_time, entry.access_time) == (0, 0.0, 0.0)


class _FakeRawSftp:
    """Scripted stand-in for the raw request channel of ``paramiko.SFTPClient``."""

    def __init__(self, replies: list[tuple[int, paramiko.Message] | Exception]) -> None:
        self._replies = list(replies)
        self.requests: list[tuple[int, tuple[object, ...]]] = []

    def _request(self, t: int, *args: object) -> tuple[int, paramiko.Message]:
        self.requests.append((t, args))
        reply = self._replies.pop(0)
        if isinstance(reply, Exception):
            raise reply
        return reply


def _handle_reply(handle: bytes = b"h1") -> tuple[int, paramiko.Message]:
    msg = paramiko.Message()
    msg.add_string(handle)
    msg.rewind()
    return CMD_HANDLE, msg


def _names_reply(names: list[bytes]) -> tuple[int, paramiko.Message]:
    msg = paramiko.Message()
    msg.add_int(len(names))
    for name in names:
        msg.add_string(name)
        msg.add_string(b"-rw-r--r-- 1 u g 1 Jan 1 00:00 x")
        msg.add_int(paramiko.SFTPAttributes.FLAG_SIZE | paramiko.SFTPAttributes.FLAG_PERMISSIONS | paramiko.SFTPAttributes.FLAG_AMTIME)
        msg.add_int64(3)
        msg.add_int(0o100644)
        msg.add_int(1)
        msg.add_int(2)
    msg.rewind()
    return CMD_NAME, msg


def test_readdir_returns_raw_byte_names_without_dot_entries_and_closes_the_handle() -> None:
    sftp = _FakeRawSftp([_handle_reply(), _names_reply([b".", b"..", b"a", b"bad\xffname"]), _names_reply([b"z"]), EOFError("eof"), (0, paramiko.Message())])
    entries = _sftp_readdir(sftp, b"/d")
    assert [name for name, _ in entries] == [b"a", b"bad\xffname", b"z"]
    assert entries[0][1] == _SftpAttrs(size=3, mode=0o100644, atime=1, mtime=2)
    assert [request[0] for request in sftp.requests] == [CMD_OPENDIR, CMD_READDIR, CMD_READDIR, CMD_READDIR, CMD_CLOSE]
    assert sftp.requests[0][1] == (b"/d",)
    assert sftp.requests[-1][1] == (b"h1",)


def test_readdir_closes_the_handle_when_a_later_request_fails() -> None:
    sftp = _FakeRawSftp([_handle_reply(), _names_reply([b"a"]), paramiko.SFTPError("boom"), (0, paramiko.Message())])
    with pytest.raises(paramiko.SFTPError, match="boom"):
        _sftp_readdir(sftp, b"/d")
    assert sftp.requests[-1][0] == CMD_CLOSE


def test_readdir_closes_the_handle_on_an_unexpected_reply_type() -> None:
    sftp = _FakeRawSftp([_handle_reply(), (CMD_HANDLE, paramiko.Message()), (0, paramiko.Message())])
    with pytest.raises(paramiko.SFTPError):
        _sftp_readdir(sftp, b"/d")
    assert sftp.requests[-1][0] == CMD_CLOSE


def test_readdir_ignores_a_failing_close_and_keeps_the_original_error() -> None:
    sftp = _FakeRawSftp([_handle_reply(), paramiko.SFTPError("boom"), OSError("close failed")])
    with pytest.raises(paramiko.SFTPError, match="boom"):
        _sftp_readdir(sftp, b"/d")


def test_readdir_open_failure_sends_no_close() -> None:
    sftp = _FakeRawSftp([OSError(13, "denied")])
    with pytest.raises(PermissionError):
        _sftp_readdir(sftp, b"/d")
    assert [request[0] for request in sftp.requests] == [CMD_OPENDIR]
