"""Exercise editing replacement through a real SFTP connection."""

import hashlib
import os
from collections.abc import Iterator
from pathlib import Path
from uuid import uuid4

import paramiko
import pytest
from paramiko.sftp import SFTP_OK

from nova_navigator.editing.commit import commit_ssh
from nova_navigator.editing.model import SourceConflictError, SourceVersion
from nova_navigator.vfs.filesystems.remote import RemoteFilesystem
from nova_navigator.vfs.filesystems.ssh import SSHFilesystem
from nova_navigator.vfs.vpath import VPath
from tests._utils.stub_ssh_server import StubSFTPServerInterface, StubSSHServer


@pytest.fixture
def editing_ssh(ssh_server: StubSSHServer) -> Iterator[SSHFilesystem]:
    client = paramiko.SSHClient()
    client.get_host_keys().add(f"[{ssh_server.host}]:{ssh_server.port}", "ssh-rsa", ssh_server.host_key)
    filesystem = SSHFilesystem(hostname=ssh_server.host, port=ssh_server.port, username="testuser", password=uuid4().hex, ssh_client=client)
    try:
        yield filesystem
    finally:
        client.close()


@pytest.mark.parametrize("case", ["success", "unsupported", "conflict", "force", "upload_race", "force_race"])
def test_ssh_staged_commit(tmp_path: Path, editing_ssh: SSHFilesystem, monkeypatch: pytest.MonkeyPatch, case: str) -> None:
    source = tmp_path / "source"
    mirror = tmp_path / "mirror"
    original = b"new" if case in {"conflict", "force", "force_race"} else b"old"
    source.write_bytes(original)
    mirror.write_bytes(b"edited" * 30000)
    expected = SourceVersion("digest", hashlib.sha256(b"old").hexdigest(), 3)
    wrapped = RemoteFilesystem("saved", editing_ssh)
    path = VPath(source, wrapped)
    original_uri = path.uri

    def posix_rename(_server: StubSFTPServerInterface, oldpath: str, newpath: str) -> int:
        os.replace(oldpath, newpath)
        return SFTP_OK

    if case != "unsupported":
        monkeypatch.setattr(StubSFTPServerInterface, "posix_rename", posix_rename)
    if case in {"upload_race", "force_race"}:
        original_open = StubSFTPServerInterface.open

        def racing_open(server: StubSFTPServerInterface, remote_path: str, flags: int, attr: paramiko.SFTPAttributes) -> paramiko.SFTPHandle | int:
            if flags & os.O_WRONLY:
                source.write_bytes(b"raced")
            return original_open(server, remote_path, flags, attr)

        monkeypatch.setattr(StubSFTPServerInterface, "open", racing_open)
    if case == "unsupported":
        with pytest.raises(OSError, match="unsupported"):
            commit_ssh(path, mirror, expected, False)
    elif case in {"conflict", "upload_race", "force_race"}:
        with pytest.raises(SourceConflictError):
            commit_ssh(path, mirror, expected, case == "force_race")
    else:
        commit_ssh(path, mirror, expected, case == "force")
    wanted = mirror.read_bytes() if case in {"success", "force"} else original
    if case in {"upload_race", "force_race"}:
        wanted = b"raced"
    assert source.read_bytes() == wanted
    assert mirror.read_bytes() == b"edited" * 30000
    assert len(list(tmp_path.iterdir())) == 2
    assert path.filesystem is wrapped
    assert path.uri == original_uri


def test_ssh_rejects_symlink_source(tmp_path: Path, editing_ssh: SSHFilesystem) -> None:
    target = tmp_path / "target"
    source = tmp_path / "source"
    mirror = tmp_path / "mirror"
    target.write_bytes(b"old")
    source.symlink_to(target)
    mirror.write_bytes(b"edited")
    expected = SourceVersion("digest", hashlib.sha256(b"old").hexdigest(), 3)
    with pytest.raises(ValueError, match="symlink"):
        commit_ssh(VPath(source, editing_ssh), mirror, expected, False)
    assert source.is_symlink()
    assert target.read_bytes() == b"old"
    assert mirror.read_bytes() == b"edited"
    assert len(list(tmp_path.iterdir())) == 3


def test_ssh_stage_is_private_during_upload(tmp_path: Path, editing_ssh: SSHFilesystem, monkeypatch: pytest.MonkeyPatch) -> None:
    from tests._utils.stub_ssh_server import StubSFTPHandle

    source = tmp_path / "source"
    mirror = tmp_path / "mirror"
    source.write_bytes(b"old")
    mirror.write_bytes(b"edited")
    expected = SourceVersion("digest", hashlib.sha256(b"old").hexdigest(), 3)
    modes: list[int] = []
    original_write = StubSFTPHandle.write

    def observe_write(handle: StubSFTPHandle, offset: int, data: bytes) -> int:
        stages = list(tmp_path.glob(".nova-edit-*"))
        modes.append(stages[0].stat().st_mode & 0o777)
        return original_write(handle, offset, data)

    monkeypatch.setattr(StubSFTPHandle, "write", observe_write)
    with pytest.raises(OSError, match="replacement failed"):
        commit_ssh(VPath(source, editing_ssh), mirror, expected, False)
    assert modes == [0o600]


@pytest.mark.parametrize("outcome", ["unchanged", "replaced", "unverifiable"])
def test_ssh_failed_rename_reports_source_outcome(tmp_path: Path, editing_ssh: SSHFilesystem, monkeypatch: pytest.MonkeyPatch, outcome: str) -> None:
    source = tmp_path / "source"
    mirror = tmp_path / "mirror"
    source.write_bytes(b"old")
    mirror.write_bytes(b"edited")
    expected = SourceVersion("digest", hashlib.sha256(b"old").hexdigest(), 3)
    failure = OSError("lost rename response")

    def fail_read(_path: VPath) -> None:
        raise OSError("connection lost")

    def fail_remove(_path: str) -> None:
        raise OSError("cleanup connection lost")

    def uncertain_rename(oldpath: str, newpath: str) -> None:
        if outcome != "unchanged":
            os.replace(oldpath, newpath)
        if outcome == "unverifiable":
            monkeypatch.setattr(editing_ssh, "read", fail_read)
        monkeypatch.setattr(editing_ssh._sftp_client, "remove", fail_remove)
        raise failure

    monkeypatch.setattr(editing_ssh._sftp_client, "posix_rename", uncertain_rename)
    with pytest.raises(OSError, match=f"source {outcome}") as caught:
        commit_ssh(VPath(source, editing_ssh), mirror, expected, False)
    assert caught.value.__cause__ is failure
    assert source.read_bytes() == (b"old" if outcome == "unchanged" else b"edited")
    assert mirror.read_bytes() == b"edited"


@pytest.mark.parametrize("failure_point", ["upload", "chmod"])
def test_ssh_staging_failure_cleans_stage(tmp_path: Path, editing_ssh: SSHFilesystem, monkeypatch: pytest.MonkeyPatch, failure_point: str) -> None:
    source = tmp_path / "source"
    mirror = tmp_path / "mirror"
    source.write_bytes(b"old")
    mirror.write_bytes(b"edited")
    expected = SourceVersion("digest", hashlib.sha256(b"old").hexdigest(), 3)
    writers: list[paramiko.SFTPFile] = []

    def fail_write(writer: paramiko.SFTPFile, _data: bytes) -> None:
        writers.append(writer)
        raise OSError("upload failed")

    def fail_chmod(_path: str, _mode: int) -> None:
        raise OSError("chmod failed")

    if failure_point == "upload":
        monkeypatch.setattr(paramiko.SFTPFile, "write", fail_write)
    else:
        monkeypatch.setattr(editing_ssh._sftp_client, "chmod", fail_chmod)
    with pytest.raises(OSError, match=f"{failure_point} failed"):
        commit_ssh(VPath(source, editing_ssh), mirror, expected, False)
    assert all(writer.closed for writer in writers)
    assert source.read_bytes() == b"old"
    assert mirror.read_bytes() == b"edited"
    assert len(list(tmp_path.iterdir())) == 2


def test_ssh_rejects_symlink_created_during_upload(tmp_path: Path, editing_ssh: SSHFilesystem, monkeypatch: pytest.MonkeyPatch) -> None:
    source = tmp_path / "source"
    target = tmp_path / "target"
    mirror = tmp_path / "mirror"
    source.write_bytes(b"old")
    target.write_bytes(b"old")
    mirror.write_bytes(b"edited")
    expected = SourceVersion("digest", hashlib.sha256(b"old").hexdigest(), 3)
    original_write = paramiko.SFTPFile.write

    def replace_with_link(writer: paramiko.SFTPFile, data: bytes) -> None:
        original_write(writer, data)
        source.unlink()
        source.symlink_to(target)

    monkeypatch.setattr(paramiko.SFTPFile, "write", replace_with_link)
    with pytest.raises(ValueError, match="symlink"):
        commit_ssh(VPath(source, editing_ssh), mirror, expected, False)
    assert source.is_symlink()
    assert target.read_bytes() == b"old"
    assert mirror.read_bytes() == b"edited"
    assert len(list(tmp_path.iterdir())) == 3
