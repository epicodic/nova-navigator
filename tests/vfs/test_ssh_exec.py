from __future__ import annotations

import shlex
from unittest.mock import MagicMock

import paramiko
import pytest

from tests.vfs.test_ssh_filesystem import _make_fs


def _mock_channel(chunks: list[bytes], exit_code: int) -> MagicMock:
    pending = list(chunks)
    channel = MagicMock()
    channel.recv_ready.side_effect = lambda: bool(pending)
    channel.recv.side_effect = lambda _size: pending.pop(0)
    channel.exit_status_ready.return_value = True
    channel.recv_exit_status.return_value = exit_code
    return channel


def _set_channel(mock_ssh: MagicMock, channel: MagicMock) -> MagicMock:
    stdin = MagicMock()
    stdout = MagicMock()
    stdout.channel = channel
    mock_ssh.exec_command.return_value = (stdin, stdout, MagicMock())
    return stdin


def test_ssh_scheme_and_capability() -> None:
    fs, _, _ = _make_fs()
    assert fs.scheme == "ssh"
    assert fs.capabilities.commands is True


def test_ssh_exec_command_wraps_script_with_cd_and_merged_stderr() -> None:
    fs, mock_ssh, _ = _make_fs()
    _set_channel(mock_ssh, _mock_channel([], 0))
    fs.exec_command("make", fs.path("/home/user"))
    expected_script = "cd /home/user || exit 1\nmake"
    mock_ssh.exec_command.assert_called_once_with(f"sh -c {shlex.quote(expected_script)} 2>&1")


def test_ssh_exec_command_returns_output_and_exit_code() -> None:
    fs, mock_ssh, _ = _make_fs()
    stdin = _set_channel(mock_ssh, _mock_channel([b"hello ", b"world"], 3))
    result = fs.exec_command("echo", fs.path("/"))
    assert result.exit_code == 3
    assert result.output == "hello world"
    stdin.close.assert_called_once()


def test_ssh_exec_command_cancel_closes_channel() -> None:
    fs, mock_ssh, _ = _make_fs()
    channel = _mock_channel([], 0)
    channel.exit_status_ready.return_value = False
    _set_channel(mock_ssh, channel)
    result = fs.exec_command("sleep 10", fs.path("/"), should_cancel=lambda: True)
    assert result.exit_code == -1
    channel.close.assert_called_once()


def test_ssh_exec_command_channel_error_becomes_oserror() -> None:
    fs, mock_ssh, _ = _make_fs()
    channel = _mock_channel([], 0)
    channel.recv_ready.side_effect = paramiko.SSHException("boom")
    _set_channel(mock_ssh, channel)
    with pytest.raises(OSError, match="boom"):
        fs.exec_command("echo", fs.path("/"))
    channel.close.assert_called_once()
