"""Tests for atomic replacement and version tags."""

import os
import stat
from pathlib import Path
from typing import cast

import paramiko
import pytest

from nova_navigator.vfs.filesystems import local as local_module
from nova_navigator.vfs.filesystems.local import LocalFilesystem
from nova_navigator.vfs.filesystems.ssh import SSHFilesystem
from nova_navigator.vfs.vpath import VPath
from tests._utils.mock_filesystem import MockFilesystem


def test_local_write_atomic_replaces_and_keeps_mode(tmp_path: Path) -> None:
    target = tmp_path / "f.txt"
    target.write_bytes(b"old")
    target.chmod(0o640)
    fs = LocalFilesystem.singleton()
    writer = fs.write_atomic(fs.path(target))
    writer.write(b"new")
    assert target.read_bytes() == b"old"
    writer.close()
    assert target.read_bytes() == b"new"
    assert target.stat().st_mode & 0o777 == 0o640
    assert sorted(p.name for p in tmp_path.iterdir()) == ["f.txt"]


def test_local_write_atomic_abort_keeps_original(tmp_path: Path) -> None:
    target = tmp_path / "f.txt"
    target.write_bytes(b"old")
    fs = LocalFilesystem.singleton()
    writer = fs.write_atomic(fs.path(target))
    writer.write(b"partial")
    writer.abort()
    assert target.read_bytes() == b"old"
    assert sorted(p.name for p in tmp_path.iterdir()) == ["f.txt"]


def test_local_write_atomic_new_file_uses_umask_default(tmp_path: Path) -> None:
    target = tmp_path / "new.txt"
    fs = LocalFilesystem.singleton()
    writer = fs.write_atomic(fs.path(target))
    writer.write(b"data")
    writer.close()
    assert target.read_bytes() == b"data"
    assert target.stat().st_mode & 0o777 == 0o666 & ~local_module._PROCESS_UMASK


def test_local_write_atomic_replace_failure_leaves_target_and_cleans_tmp(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    target = tmp_path / "f.txt"
    target.write_bytes(b"old")
    fs = LocalFilesystem.singleton()
    writer = fs.write_atomic(fs.path(target))
    writer.write(b"new")

    def _raise_replace(_src: object, _dst: object) -> None:
        raise OSError("replace failed")

    monkeypatch.setattr(local_module.os, "replace", _raise_replace)
    with pytest.raises(OSError, match="replace failed"):
        writer.close()
    assert target.read_bytes() == b"old"
    assert sorted(p.name for p in tmp_path.iterdir()) == ["f.txt"]


def test_local_write_atomic_close_is_idempotent(tmp_path: Path) -> None:
    target = tmp_path / "f.txt"
    target.write_bytes(b"old")
    fs = LocalFilesystem.singleton()
    writer = fs.write_atomic(fs.path(target))
    writer.write(b"new")
    writer.close()
    writer.close()
    assert target.read_bytes() == b"new"
    assert sorted(p.name for p in tmp_path.iterdir()) == ["f.txt"]


def test_local_write_atomic_close_after_abort_is_noop(tmp_path: Path) -> None:
    target = tmp_path / "f.txt"
    target.write_bytes(b"old")
    fs = LocalFilesystem.singleton()
    writer = fs.write_atomic(fs.path(target))
    writer.write(b"partial")
    writer.abort()
    writer.close()
    assert target.read_bytes() == b"old"
    assert sorted(p.name for p in tmp_path.iterdir()) == ["f.txt"]


def test_local_write_atomic_rejects_symlink(tmp_path: Path) -> None:
    real = tmp_path / "real.txt"
    real.write_bytes(b"x")
    link = tmp_path / "link.txt"
    link.symlink_to(real)
    fs = LocalFilesystem.singleton()
    with pytest.raises(ValueError, match="symlink"):
        fs.write_atomic(fs.path(link))


def test_local_version_tag_changes_on_replace(tmp_path: Path) -> None:
    target = tmp_path / "f.txt"
    target.write_bytes(b"same")
    fs = LocalFilesystem.singleton()
    before = fs.version_tag(fs.path(target))
    replacement = tmp_path / "g.txt"
    replacement.write_bytes(b"same")
    os.utime(replacement, ns=(target.stat().st_atime_ns, target.stat().st_mtime_ns))
    os.replace(replacement, target)
    assert fs.version_tag(fs.path(target)) != before


def test_default_write_atomic_uploads_only_on_close() -> None:
    fs = MockFilesystem({"/a.txt": b"old"})
    writer = fs.write_atomic(fs.path("/a.txt"))
    writer.write(b"new")
    assert fs.read(fs.path("/a.txt")).read(10) == b"old"
    writer.close()
    assert fs.read(fs.path("/a.txt")).read(10) == b"new"


def test_default_write_atomic_abort_never_writes() -> None:
    fs = MockFilesystem({"/a.txt": b"old"})
    writer = fs.write_atomic(fs.path("/a.txt"))
    writer.write(b"new")
    writer.abort()
    assert fs.read(fs.path("/a.txt")).read(10) == b"old"
    assert fs.writers == []


# ── SSH: _SftpAtomicWriter (fake SFTP client) ──────────────────────────────────


class _FakeSftpFile:
    def __init__(self, store: dict[str, bytes], name: str, *, fail_close: bool = False) -> None:
        self._store, self._name, self._data = store, name, b""
        self._fail_close = fail_close

    def write(self, data: bytes) -> int:
        self._data += data
        return len(data)

    def set_pipelined(self, pipelined: bool) -> None:
        del pipelined

    def close(self) -> None:
        if self._fail_close:
            raise OSError("simulated close failure")
        self._store[self._name] = self._data


class _FakeSftp:
    def __init__(
        self,
        files: dict[str, bytes],
        *,
        posix_rename: bool = True,
        symlinks: frozenset[str] = frozenset(),
        fail_close: bool = False,
    ) -> None:
        self.files = files
        self._posix = posix_rename
        self._symlinks = symlinks
        self._fail_close = fail_close
        self.modes: dict[str, int] = {}

    def open(self, name: str, mode: str) -> _FakeSftpFile:
        assert mode == "wx"
        return _FakeSftpFile(self.files, name, fail_close=self._fail_close)

    def lstat(self, name: str) -> paramiko.SFTPAttributes:
        if name not in self.files:
            raise FileNotFoundError(name)
        attrs = paramiko.SFTPAttributes()
        attrs.st_mode = (stat.S_IFLNK if name in self._symlinks else stat.S_IFREG) | 0o640
        return attrs

    stat = lstat

    def chmod(self, name: str, mode: int) -> None:
        self.modes[name] = mode

    def posix_rename(self, old: str, new: str) -> None:
        if not self._posix:
            raise OSError("posix-rename@openssh.com not supported")
        self.files[new] = self.files.pop(old)

    def remove(self, name: str) -> None:
        self.files.pop(name, None)


class _TestSSH(SSHFilesystem):
    """SSHFilesystem subclass that skips the real network connection.

    ``_sftp_client`` is declared as ``paramiko.SFTPClient`` on ``SSHFilesystem``;
    ``_FakeSftp`` only duck-types the subset of methods ``_SftpAtomicWriter``
    calls, so the assignment needs an explicit ``cast``.
    """

    def __init__(self, sftp: _FakeSftp) -> None:
        # __eq__ compares _ssh_client by identity, so any distinct object works here.
        self._ssh_client = cast("paramiko.SSHClient", sftp)
        self._sftp_client = cast("paramiko.SFTPClient", sftp)

    def refresh(self, path: VPath | None = None) -> None:
        pass


def _ssh_with(sftp: _FakeSftp) -> SSHFilesystem:
    return _TestSSH(sftp)


def test_ssh_write_atomic_renames_over_target() -> None:
    sftp = _FakeSftp({"/d/f.txt": b"old"})
    fs = _ssh_with(sftp)
    writer = fs.write_atomic(VPath("/d/f.txt", fs))
    writer.write(b"new")
    writer.close()
    assert sftp.files == {"/d/f.txt": b"new"}
    assert 0o640 in sftp.modes.values()


def test_ssh_write_atomic_without_posix_rename_keeps_source() -> None:
    sftp = _FakeSftp({"/d/f.txt": b"old"}, posix_rename=False)
    fs = _ssh_with(sftp)
    writer = fs.write_atomic(VPath("/d/f.txt", fs))
    writer.write(b"new")
    with pytest.raises(OSError, match="atomic replacement"):
        writer.close()
    assert sftp.files == {"/d/f.txt": b"old"}


def test_ssh_write_atomic_close_is_idempotent() -> None:
    sftp = _FakeSftp({"/d/f.txt": b"old"})
    fs = _ssh_with(sftp)
    writer = fs.write_atomic(VPath("/d/f.txt", fs))
    writer.write(b"new")
    writer.close()
    writer.close()
    assert sftp.files == {"/d/f.txt": b"new"}


def test_ssh_write_atomic_abort_keeps_original() -> None:
    sftp = _FakeSftp({"/d/f.txt": b"old"})
    fs = _ssh_with(sftp)
    writer = fs.write_atomic(VPath("/d/f.txt", fs))
    writer.write(b"partial")
    writer.abort()
    assert sftp.files == {"/d/f.txt": b"old"}


def test_ssh_write_atomic_abort_is_idempotent() -> None:
    sftp = _FakeSftp({"/d/f.txt": b"old"})
    fs = _ssh_with(sftp)
    writer = fs.write_atomic(VPath("/d/f.txt", fs))
    writer.write(b"partial")
    writer.abort()
    writer.abort()
    assert sftp.files == {"/d/f.txt": b"old"}


def test_ssh_write_atomic_new_file_skips_chmod() -> None:
    sftp = _FakeSftp({})
    fs = _ssh_with(sftp)
    writer = fs.write_atomic(VPath("/d/new.txt", fs))
    writer.write(b"data")
    writer.close()
    assert sftp.files == {"/d/new.txt": b"data"}
    assert sftp.modes == {}


def test_ssh_write_atomic_removes_tmp_when_file_close_raises() -> None:
    sftp = _FakeSftp({"/d/f.txt": b"old"}, fail_close=True)
    fs = _ssh_with(sftp)
    writer = fs.write_atomic(VPath("/d/f.txt", fs))
    writer.write(b"new")
    with pytest.raises(OSError, match="simulated close failure"):
        writer.close()
    assert sftp.files == {"/d/f.txt": b"old"}


def test_ssh_write_atomic_rejects_symlink() -> None:
    sftp = _FakeSftp({"/d/link.txt": b"x"}, symlinks=frozenset({"/d/link.txt"}))
    fs = _ssh_with(sftp)
    with pytest.raises(ValueError, match="symlink"):
        fs.write_atomic(VPath("/d/link.txt", fs))


def test_ssh_version_tag_is_none() -> None:
    sftp = _FakeSftp({"/d/f.txt": b"old"})
    fs = _ssh_with(sftp)
    assert fs.version_tag(VPath("/d/f.txt", fs)) is None
