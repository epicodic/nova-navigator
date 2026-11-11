"""Directory listing of SSHFilesystem against the stub SSH server (real shell, real files)."""

from __future__ import annotations

import os
from collections.abc import Generator, Iterator
from contextlib import contextmanager
from pathlib import Path
from unittest.mock import patch

import paramiko
import pytest
from paramiko.channel import ChannelFile, ChannelStderrFile, ChannelStdinFile

from nova_navigator.vfs.filesystems.ssh import SSHFilesystem
from nova_navigator.vfs.vpath import VPath
from tests._utils.stub_ssh_server import StubSSHServer

_LOGIN = ("testuser", "testpass")


class _AcceptAnyHostKey(paramiko.MissingHostKeyPolicy):
    """Accept the stub server's throw-away host key."""

    def missing_host_key(self, client: paramiko.SSHClient, hostname: str, key: paramiko.PKey) -> None:
        return


@pytest.fixture(scope="module")
def server(tmp_path_factory: pytest.TempPathFactory) -> Generator[StubSSHServer, None, None]:
    server = StubSSHServer(root_dir=tmp_path_factory.mktemp("ssh_listing_root"))
    server.start()
    yield server
    server.stop()


@pytest.fixture
def fs(server: StubSSHServer) -> Generator[SSHFilesystem, None, None]:
    client = paramiko.SSHClient()
    client.set_missing_host_key_policy(_AcceptAnyHostKey())
    filesystem = SSHFilesystem(hostname=server.host, port=server.port, username=_LOGIN[0], password=_LOGIN[1], ssh_client=client)
    yield filesystem
    client.close()


def _write_bytes_named(raw_name: bytes, content: bytes) -> None:
    """Create a file by raw byte name (sync helper, so async tests do not open files themselves)."""
    with open(raw_name, "wb") as handle:
        handle.write(content)


@contextmanager
def _spy_exec(fs: SSHFilesystem) -> Iterator[list[str]]:
    """Record every command the filesystem sends over SSH while still running it."""
    commands: list[str] = []
    original = fs._ssh_client.exec_command

    def spy(command: str) -> tuple[ChannelStdinFile, ChannelFile, ChannelStderrFile]:
        commands.append(command)
        return original(command)

    with patch.object(fs._ssh_client, "exec_command", side_effect=spy):
        yield commands


async def _names(fs: SSHFilesystem, directory: Path) -> set[str]:
    return {vp.name async for vp in fs.iterdir(VPath(directory, fs))}


async def _entries(fs: SSHFilesystem, directory: Path) -> dict[str, VPath]:
    return {vp.name: vp async for vp in fs.iterdir(VPath(directory, fs))}


@pytest.mark.parametrize(
    "dirname",
    ["a b", "it's", 'say "hi"', "$HOME", "a;b", "*", "`id`", "a\nb", "-dash", "(D)", "a&b", "a|b", "~", "#hash"],
)
@pytest.mark.asyncio
async def test_list_directory_with_hostile_path(fs: SSHFilesystem, tmp_path: Path, dirname: str) -> None:
    directory = tmp_path / dirname
    directory.mkdir()
    (directory / "inner.txt").write_text("x")
    assert await _names(fs, directory) == {"inner.txt"}


@pytest.mark.asyncio
async def test_injection_directory_does_not_execute(fs: SSHFilesystem, tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    # The stub's shell subprocess inherits our cwd: keep a firing injection out of the repository.
    monkeypatch.chdir(tmp_path)
    marker = tmp_path / "pwned_rel"

    # Positive control: the same relative-write mechanism as the injection works through a real command.
    result = fs.exec_command("touch pwned_rel", VPath(tmp_path, fs))
    assert result.exit_code == 0
    assert marker.exists()
    marker.unlink()

    directory = tmp_path / "x; touch pwned_rel; #"
    directory.mkdir()
    (directory / "inner.txt").write_text("x")
    assert await _names(fs, directory) == {"inner.txt"}
    assert not marker.exists()


@pytest.mark.asyncio
async def test_entries_with_awkward_names_listed_once(fs: SSHFilesystem, tmp_path: Path) -> None:
    names = ["a,b", "a\nb", "a b", "-dash", "--", ",", " lead", "trail ", "*(D)"]
    for name in names:
        (tmp_path / name).write_text(name)
    _write_bytes_named(os.fsencode(tmp_path) + b"/bad\xff", b"bad")
    (tmp_path / "sub\nsub").mkdir()
    expected_sizes = {**{name: len(name) for name in names}, "bad\udcff": 3}

    entries = await _entries(fs, tmp_path)
    assert set(entries) == {*expected_sizes, "sub\nsub"}
    for name, size in expected_sizes.items():
        assert entries[name].stat.size == size, name
        assert entries[name].stat.is_directory is False, name
        assert entries[name].stat.is_executable is False, name
    assert entries["sub\nsub"].stat.is_directory is True


@pytest.mark.asyncio
async def test_listing_reports_kinds_sizes_and_modes(fs: SSHFilesystem, tmp_path: Path) -> None:
    (tmp_path / "file.txt").write_text("12345")
    script = tmp_path / "run.sh"
    script.write_text("#!/bin/sh\n")
    script.chmod(0o755)
    (tmp_path / "sub").mkdir()
    (tmp_path / "link").symlink_to("file.txt")
    (tmp_path / ".hidden").write_text("")

    entries = await _entries(fs, tmp_path)
    assert set(entries) == {"file.txt", "run.sh", "sub", "link", ".hidden"}
    assert entries["file.txt"].stat.size == 5
    assert entries["file.txt"].stat.is_directory is False
    assert entries["run.sh"].stat.is_executable is True
    assert entries["file.txt"].stat.is_executable is False
    assert entries["sub"].stat.is_directory is True
    assert entries["link"].stat.is_symlink is True
    assert entries["link"].stat.size == 5
    assert entries[".hidden"].stat.is_hidden is True


@pytest.mark.asyncio
async def test_sh_wrapper_is_used(fs: SSHFilesystem, tmp_path: Path) -> None:
    (tmp_path / "plain").write_text("")
    with _spy_exec(fs) as commands:
        assert await _names(fs, tmp_path) == {"plain"}
    assert commands
    assert all(c.startswith("sh -c ") for c in commands)


@pytest.mark.asyncio
async def test_one_exec_per_directory_per_follow_mode(fs: SSHFilesystem, tmp_path: Path) -> None:
    (tmp_path / "f").write_text("")
    with _spy_exec(fs) as commands:
        await _names(fs, tmp_path)
        assert len(commands) == 2
        assert sum("stat -L" in c for c in commands) == 1
        await _names(fs, tmp_path)
        assert len(commands) == 2
        fs.refresh(VPath(tmp_path, fs))
        await _names(fs, tmp_path)
        assert len(commands) == 4


@pytest.mark.skipif(os.geteuid() == 0, reason="root ignores directory permissions")
@pytest.mark.asyncio
async def test_permission_denied_directory(fs: SSHFilesystem, tmp_path: Path) -> None:
    locked = tmp_path / "locked"
    locked.mkdir()
    (locked / "f").write_text("")
    locked.chmod(0o000)
    try:
        with pytest.raises(PermissionError):
            await _names(fs, locked)
    finally:
        locked.chmod(0o755)


@pytest.mark.asyncio
async def test_missing_directory_lists_empty(fs: SSHFilesystem, tmp_path: Path) -> None:
    assert await _names(fs, tmp_path / "nope") == set()


@pytest.mark.asyncio
async def test_broken_symlink_listing_still_returns_other_entries(fs: SSHFilesystem, tmp_path: Path) -> None:
    (tmp_path / "ok").write_text("x")
    (tmp_path / "broken").symlink_to("does-not-exist")
    assert await _names(fs, tmp_path) == {"ok"}


@pytest.fixture
def surrogateescape_sftp(monkeypatch: pytest.MonkeyPatch) -> None:
    """Let the in-process stub SFTP server decode non-UTF-8 path bytes like a real OpenSSH server would pass them on."""

    def get_text(self: paramiko.Message) -> str:
        return self.get_string().decode("utf-8", "surrogateescape")

    monkeypatch.setattr(paramiko.Message, "get_text", get_text)


@pytest.mark.usefixtures("surrogateescape_sftp")
@pytest.mark.asyncio
async def test_non_utf8_name_round_trips(fs: SSHFilesystem, tmp_path: Path) -> None:
    raw_dir = os.fsencode(tmp_path)
    _write_bytes_named(raw_dir + b"/bad\xffname", b"payload")
    entries = await _entries(fs, tmp_path)
    vp = entries[os.fsdecode(b"bad\xffname")]
    assert fs.stat(vp).size == len(b"payload")
    reader = fs.read(vp)
    assert reader.read(1024) == b"payload"
    reader.close()
    fs.remove(vp)
    assert not os.path.exists(raw_dir + b"/bad\xffname")


@pytest.mark.parametrize("raw_name", [b"dir\xffbad", b"dir\xff\n"])
@pytest.mark.asyncio
async def test_list_non_utf8_directory(fs: SSHFilesystem, tmp_path: Path, raw_name: bytes) -> None:
    raw = os.fsencode(tmp_path) + b"/" + raw_name
    os.mkdir(raw)
    _write_bytes_named(raw + b"/inner.txt", b"x")
    assert await _names(fs, Path(os.fsdecode(raw))) == {"inner.txt"}
