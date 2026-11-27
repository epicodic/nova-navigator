"""The private paramiko members and messages that ``vfs/filesystems/ssh.py`` depends on.

A paramiko update that changes one of them must fail here instead of in a hang or a lost error.
"""

from __future__ import annotations

import io
import struct
import weakref
from collections.abc import Callable
from typing import Protocol, cast

import paramiko
import pytest
from paramiko.sftp import CMD_STATUS, CMD_VERSION, SFTP_EOF, SFTP_FAILURE


class _SilentChannel:
    """Answers the version handshake of ``SFTPClient`` and records nothing else."""

    def __init__(self) -> None:
        self._replies = io.BytesIO(struct.pack(">IBI", 5, CMD_VERSION, 3))

    def get_name(self) -> str:
        return "silent"

    def send(self, data: bytes) -> int:
        return len(data)

    def recv(self, size: int) -> bytes:
        return self._replies.read(size)


class _RawFile(Protocol):
    def _async_response(self, t: int, msg: paramiko.Message, num: int) -> None: ...

    def _check_exception(self) -> None: ...


class _Auth(Protocol):
    _auth: Callable[..., object]


def _client() -> paramiko.SFTPClient:
    return paramiko.SFTPClient(cast("paramiko.Channel", _SilentChannel()))


def _status(code: int) -> paramiko.Message:
    msg = paramiko.Message()
    msg.add_int(code)
    msg.add_string("text")
    msg.rewind()
    return msg


def test_sftp_client_members() -> None:
    client = _client()
    for name in ("_send_packet", "_read_response", "_async_request", "_convert_status"):
        assert callable(getattr(client, name)), name
    members = vars(client)
    # weak values: the entries of an abandoned file vanish with it
    assert isinstance(members["_expecting"], weakref.WeakValueDictionary)
    with members["_lock"]:
        pass


def test_sftp_file_members() -> None:
    client = _client()
    file = paramiko.SFTPFile(client, b"handle", "wb")
    file.set_pipelined(True)
    assert vars(file)["handle"] == b"handle"
    assert vars(file)["sftp"] is client
    assert vars(file)["_closed"] is False
    assert "__del__" in vars(paramiko.SFTPFile)  # the finalizer that _SessionSftpFile replaces
    assert list(vars(file)["_reqs"]) == []
    file.write(b"data")
    number = vars(file)["_reqs"][0]  # a pipelined write leaves its request number here
    assert number in vars(client)["_expecting"]
    del vars(client)["_expecting"][number]

    raw = cast("_RawFile", file)
    raw._async_response(CMD_STATUS, _status(SFTP_FAILURE), number)
    with pytest.raises(OSError, match="text"):
        raw._check_exception()
    raw._check_exception()  # raised once only
    vars(file)["_closed"] = True  # nothing to close on the silent channel


def test_convert_status_distinguishes_end_of_file() -> None:
    convert = vars(paramiko.SFTPClient)["_convert_status"]
    with pytest.raises(EOFError):
        convert(_client(), _status(SFTP_EOF))
    with pytest.raises(OSError, match="text"):
        convert(_client(), _status(SFTP_FAILURE))


def test_no_authentication_method_message() -> None:
    client = cast("_Auth", paramiko.SSHClient())
    with pytest.raises(paramiko.SSHException) as info:
        client._auth("user", None, None, [], False, False, False, False, True, None, None)
    assert type(info.value) is paramiko.SSHException
    assert str(info.value) == "No authentication methods available"
