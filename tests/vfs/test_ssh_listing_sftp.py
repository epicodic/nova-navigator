"""Directory listing of SSHFilesystem through SFTP (REQ-21): primitives, attribute mapping and equality with the GNU listing."""

from __future__ import annotations

import os
from collections.abc import Generator, Iterator
from contextlib import contextmanager
from pathlib import Path
from unittest.mock import patch

import paramiko
import pytest
from paramiko.channel import ChannelFile, ChannelStderrFile, ChannelStdinFile
from paramiko.sftp import CMD_CLOSE, CMD_HANDLE, CMD_NAME, CMD_OPENDIR, CMD_READDIR

from nova_navigator.vfs.filesystems import ssh as ssh_module
from nova_navigator.vfs.filesystems.ssh import _PROBE_COMMAND, SSHFilesystem, StatEntry, _entry_from_attrs, _read_attrs, _sftp_readdir, _SftpAttrs
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


# --- a non-GNU remote selects the SFTP listing --------------------------------------------------------------------


@pytest.fixture(scope="module", params=["illegal-option", "missing"])
def non_gnu_server(request: pytest.FixtureRequest, tmp_path_factory: pytest.TempPathFactory) -> Generator[StubSSHServer, None, None]:
    server = StubSSHServer(root_dir=tmp_path_factory.mktemp("ssh_non_gnu_root"), non_gnu=request.param)
    server.start()
    yield server
    server.stop()


@pytest.fixture
def sftp_fs(non_gnu_server: StubSSHServer) -> Generator[SSHFilesystem, None, None]:
    client = paramiko.SSHClient()
    client.set_missing_host_key_policy(_AcceptAnyHostKey())
    filesystem = SSHFilesystem(hostname=non_gnu_server.host, port=non_gnu_server.port, username="testuser", password=_LOGIN_WORD, ssh_client=client)
    yield filesystem
    client.close()


@pytest.fixture(scope="module")
def refusing_server(tmp_path_factory: pytest.TempPathFactory) -> Generator[StubSSHServer, None, None]:
    server = StubSSHServer(root_dir=tmp_path_factory.mktemp("ssh_refuse_exec_root"), refuse_exec=True)
    server.start()
    yield server
    server.stop()


@pytest.fixture
def refusing_fs(refusing_server: StubSSHServer) -> Generator[SSHFilesystem, None, None]:
    client = paramiko.SSHClient()
    client.set_missing_host_key_policy(_AcceptAnyHostKey())
    filesystem = SSHFilesystem(hostname=refusing_server.host, port=refusing_server.port, username="testuser", password=_LOGIN_WORD, ssh_client=client)
    yield filesystem
    client.close()


@contextmanager
def _spy_exec(filesystem: SSHFilesystem) -> Iterator[list[str]]:
    commands: list[str] = []
    original = filesystem._ssh_client.exec_command

    def spy(command: str) -> tuple[ChannelStdinFile, ChannelFile, ChannelStderrFile]:
        commands.append(command)
        return original(command)

    with patch.object(filesystem._ssh_client, "exec_command", side_effect=spy):
        yield commands


async def _snapshot(filesystem: SSHFilesystem, directory: Path) -> dict[str, tuple[object, ...]]:
    result: dict[str, tuple[object, ...]] = {}
    async for vp in filesystem.iterdir(VPath(directory, filesystem)):
        st = vp.stat
        result[vp.name] = (st.size, st.modified, st.mode, st.is_hidden, st.is_directory, st.is_executable, st.is_symlink, st.is_broken_symlink)
    return result


async def _names(filesystem: SSHFilesystem, directory: Path) -> list[str]:
    return [vp.name async for vp in filesystem.iterdir(VPath(directory, filesystem))]


@pytest.mark.asyncio
async def test_non_gnu_listing_has_the_same_entries_and_attributes_as_the_gnu_listing(fs: SSHFilesystem, sftp_fs: SSHFilesystem, tmp_path: Path) -> None:
    _make_tree(tmp_path)
    via_sftp = await _snapshot(sftp_fs, tmp_path)
    via_gnu = await _snapshot(fs, tmp_path)
    assert sftp_fs._listing_via_sftp is True
    assert fs._listing_via_sftp is False
    assert via_sftp == via_gnu
    # tuple order: size, modified, mode, is_hidden, is_directory, is_executable, is_symlink, is_broken_symlink
    assert via_sftp["link_to_folder"][4] is True  # a link to a folder is a folder
    assert (via_sftp["link_to_folder"][6], via_sftp["link_to_folder"][7]) == (True, False)
    assert via_sftp["broken"][4:] == (False, False, True, True)  # broken link: not a folder, a link, marked broken
    assert via_sftp["loop"][-1] is True
    assert via_sftp[".hidden"][3] is True
    assert via_sftp["run.sh"][5] is True  # executable flag
    assert via_sftp["file.txt"][0] == os.lstat(tmp_path / "file.txt").st_size
    assert via_sftp["file.txt"][1] == int(os.lstat(tmp_path / "file.txt").st_mtime)


@pytest.mark.asyncio
async def test_non_gnu_first_listing_of_a_non_empty_directory_is_never_empty(sftp_fs: SSHFilesystem, tmp_path: Path) -> None:
    (tmp_path / "a").write_text("x")
    assert await _names(sftp_fs, tmp_path) == ["a"]
    assert sftp_fs._listing_via_sftp is True


@pytest.mark.asyncio
async def test_non_gnu_directory_with_only_symlinks_lists_them(sftp_fs: SSHFilesystem, tmp_path: Path) -> None:
    (tmp_path / "one").symlink_to("missing")
    (tmp_path / "two").symlink_to(".", target_is_directory=True)
    assert sorted(await _names(sftp_fs, tmp_path)) == ["one", "two"]


@pytest.mark.asyncio
async def test_non_gnu_empty_directory_listed_first_does_not_decide_the_method(sftp_fs: SSHFilesystem, tmp_path: Path) -> None:
    (tmp_path / "empty").mkdir()
    (tmp_path / "full").mkdir()
    (tmp_path / "full" / "f").write_text("x")
    assert await _names(sftp_fs, tmp_path / "empty") == []
    assert sftp_fs._listing_via_sftp is False
    assert await _names(sftp_fs, tmp_path / "full") == ["f"]
    assert sftp_fs._listing_via_sftp is True


@pytest.mark.asyncio
async def test_non_gnu_switch_costs_one_probe_and_later_listings_run_no_command(sftp_fs: SSHFilesystem, tmp_path: Path) -> None:
    for directory in ("first", "second"):
        (tmp_path / directory).mkdir()
        (tmp_path / directory / "f").write_text("x")
        (tmp_path / directory / "link").symlink_to("f")
    with _spy_exec(sftp_fs) as commands:
        await _names(sftp_fs, tmp_path / "first")
        assert len(commands) == 2
        assert commands.count(_PROBE_COMMAND) == 1
        await _names(sftp_fs, tmp_path / "second")
        assert len(commands) == 2  # zero exec calls after the switch
    assert sftp_fs._listing_via_sftp is True


@pytest.mark.asyncio
async def test_sftp_mode_lists_a_directory_once_for_both_follow_modes(sftp_fs: SSHFilesystem, tmp_path: Path) -> None:
    (tmp_path / "f").write_text("x")
    (tmp_path / "sub").mkdir()
    (tmp_path / "sub" / "g").write_text("x")
    await _names(sftp_fs, tmp_path)  # decides the method
    with patch.object(ssh_module, "_sftp_readdir", wraps=ssh_module._sftp_readdir) as readdir:
        await _names(sftp_fs, tmp_path / "sub")
        await _names(sftp_fs, tmp_path / "sub")
        assert readdir.call_count == 1
        sftp_fs.refresh(VPath(tmp_path / "sub", sftp_fs))
        await _names(sftp_fs, tmp_path / "sub")
        assert readdir.call_count == 2


@pytest.mark.skipif(os.geteuid() == 0, reason="root ignores directory permissions")
@pytest.mark.asyncio
async def test_non_gnu_unreadable_first_directory_raises_without_switching(sftp_fs: SSHFilesystem, tmp_path: Path) -> None:
    locked = tmp_path / "locked"
    locked.mkdir()
    (locked / "f").write_text("")
    locked.chmod(0o000)
    try:
        with pytest.raises(PermissionError):
            await _names(sftp_fs, locked)
    finally:
        locked.chmod(0o755)
    assert sftp_fs._listing_via_sftp is False


@pytest.mark.skipif(os.geteuid() == 0, reason="root ignores directory permissions")
@pytest.mark.asyncio
async def test_non_gnu_unreadable_directory_after_the_switch_raises_permission_error(sftp_fs: SSHFilesystem, tmp_path: Path) -> None:
    (tmp_path / "ok").mkdir()
    (tmp_path / "ok" / "f").write_text("x")
    locked = tmp_path / "locked"
    locked.mkdir()
    (locked / "f").write_text("")
    await _names(sftp_fs, tmp_path / "ok")
    assert sftp_fs._listing_via_sftp is True
    locked.chmod(0o000)
    try:
        with pytest.raises(PermissionError):
            await _names(sftp_fs, locked)
    finally:
        locked.chmod(0o755)


@pytest.mark.parametrize("switched", [False, True])
@pytest.mark.asyncio
async def test_non_gnu_missing_directory_and_file_path_list_empty(sftp_fs: SSHFilesystem, tmp_path: Path, switched: bool) -> None:
    (tmp_path / "plain").write_text("x")
    if switched:
        (tmp_path / "d").mkdir()
        (tmp_path / "d" / "f").write_text("x")
        await _names(sftp_fs, tmp_path / "d")
        assert sftp_fs._listing_via_sftp is True
    assert await _names(sftp_fs, tmp_path / "nope") == []
    assert await _names(sftp_fs, tmp_path / "plain") == []


@pytest.mark.asyncio
async def test_non_gnu_odd_names_list_exactly_once(sftp_fs: SSHFilesystem, tmp_path: Path) -> None:
    for name in _AWKWARD:
        (tmp_path / name).write_text(name)
    _write_bytes_named(os.fsencode(tmp_path) + b"/bad\xffname", b"bad")
    names = await _names(sftp_fs, tmp_path)
    assert sorted(names) == sorted([*_AWKWARD, os.fsdecode(b"bad\xffname")])


@pytest.mark.usefixtures("stub_surrogateescape")
@pytest.mark.asyncio
async def test_non_gnu_non_utf8_name_round_trips(sftp_fs: SSHFilesystem, tmp_path: Path) -> None:
    _write_bytes_named(os.fsencode(tmp_path) + b"/bad\xffname", b"payload")
    entries = {vp.name: vp async for vp in sftp_fs.iterdir(VPath(tmp_path, sftp_fs))}
    vp = entries[os.fsdecode(b"bad\xffname")]
    assert sftp_fs.stat(vp).size == len(b"payload")
    reader = sftp_fs.read(vp)
    assert reader.read(1024) == b"payload"
    reader.close()


@pytest.mark.asyncio
async def test_non_gnu_stat_of_entries(sftp_fs: SSHFilesystem, tmp_path: Path) -> None:
    _make_tree(tmp_path)
    await _names(sftp_fs, tmp_path)
    assert sftp_fs._listing_via_sftp is True
    assert sftp_fs.stat(VPath(tmp_path / "file.txt", sftp_fs)).size == len("hello")
    assert sftp_fs.stat(VPath(tmp_path / "link_to_folder", sftp_fs)).is_directory is True
    broken = sftp_fs.stat(VPath(tmp_path / "broken", sftp_fs))
    assert (broken.is_symlink, broken.is_broken_symlink, broken.is_directory) == (True, True, False)
    with pytest.raises(FileNotFoundError):
        sftp_fs.stat(VPath(tmp_path / "nope", sftp_fs))


@pytest.mark.asyncio
async def test_dir_stat_refused_exec_switches_to_sftp(fs: SSHFilesystem, refusing_fs: SSHFilesystem, tmp_path: Path) -> None:
    """DEC-32: a server that refuses the exec request lists through SFTP, stickily, with the same entries as the GNU listing."""
    _make_tree(tmp_path)
    (tmp_path / "second").mkdir()
    with _spy_exec(refusing_fs) as commands:
        via_sftp = await _snapshot(refusing_fs, tmp_path)
        assert refusing_fs._listing_via_sftp is True
        assert len(commands) == 1  # the refused list command; no probe
        await _names(refusing_fs, tmp_path / "second")
        assert len(commands) == 1  # sticky: no further exec attempts
    assert set(via_sftp) >= {"file.txt", "run.sh", ".hidden", "folder", "link_to_file", "link_to_folder", "broken", "loop"}
    assert via_sftp == await _snapshot(fs, tmp_path)
