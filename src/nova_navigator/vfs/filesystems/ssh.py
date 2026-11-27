from __future__ import annotations

import asyncio
import contextlib
import errno
import functools
import logging
import os
import shlex
import socket
import stat
import threading
import time
import uuid
from collections import deque
from collections.abc import AsyncIterator, Callable, Mapping, Sequence
from dataclasses import dataclass, field
from pathlib import Path, PurePath
from typing import Protocol, Self, cast, override

import paramiko
from paramiko.sftp import CMD_CLOSE, CMD_DATA, CMD_HANDLE, CMD_NAME, CMD_OPENDIR, CMD_READ, CMD_READDIR, CMD_STATUS, int64

from nova_navigator.remotes.known_hosts import (
    HostKeyConflictError,
    KnownHosts,
    KnownHostsLocations,
    append_host_key,
    fingerprint,
    host_key_name,
    key_type_name,
    read_known_hosts,
)
from nova_navigator.remotes.ssh_config import SshConfig
from nova_navigator.remotes.ssh_route import SshHop

from ..filesystem import AtomicWriterLike, Filesystem, FilesystemCapabilities, Stat, StreamReaderLike, StreamWriterLike
from ..tail_buffer import TailBuffer
from ..types import OUTPUT_TAIL_LIMIT, ExecResult
from ..vpath import VPath

_logger = logging.getLogger(__name__)

_EXEC_POLL_INTERVAL = 0.05
_EXEC_CHUNK_SIZE = 65536


@dataclass
class StatEntry:
    """Raw stat data of one directory entry, parsed from a remote ``stat`` command or read over SFTP."""

    size: int
    permissions: int
    owner: str
    modified_time: float
    access_time: float
    change_time: float
    is_directory: bool
    is_symlink: bool
    name: str


# Listing command contract.  The remote script ``cd``s into the (quoted) directory and runs
# ``find . -maxdepth 1 -mindepth 1 -exec stat --printf FORMAT -- {} +``.  FORMAT emits eight
# fields per entry, every field terminated by NUL (the only byte a file name cannot contain
# besides "/"), so names with commas, newlines or leading/trailing blanks cannot be confused
# with field or record separators.  Field order: size, octal permissions, owner, mtime, atime,
# ctime, file type, name (``./name`` as produced by find).
_STAT_FORMAT = r"%s\0%a\0%U\0%Y\0%X\0%Z\0%F\0%n\0"
_STAT_FIELD_COUNT = 8
# U+FFFD in UTF-8.  uutils `stat` (Ubuntu 26.04) writes it for each name byte that is not UTF-8, so such a listing
# may name files that do not exist, or other files.  A name that really contains U+FFFD takes the same path.
_REPLACEMENT_BYTES = "�".encode()
# Exit codes of the wrapper script when ``cd`` fails: the directory exists (permission problem)
# or it does not.  Shell-independent, unlike the wording of the ``cd`` error message.
_CD_FAILED_EXIT = 120
_CD_MISSING_EXIT = 121


def _decode(data: bytes) -> str:
    """Decode remote bytes like :func:`os.fsdecode` on a UTF-8 system (undecodable bytes become surrogates)."""
    return data.decode("utf-8", "surrogateescape")


def _sftp_path(path: VPath) -> bytes:
    """Return *path* as the raw bytes SFTP must send (surrogate-escaped names round-trip to the original bytes)."""
    return os.fsencode(path.path.as_posix())


def _quote_path(path: str) -> tuple[str, str]:
    """Return ``(prelude, word)`` that make a pure-ASCII shell script reproduce *path* exactly.

    *prelude* is a (possibly empty) shell statement list that must run first; *word* is the
    shell word that then expands to the exact bytes of *path*.  Paths that are valid UTF-8 are
    plain :func:`shlex.quote`d.  Paths containing undecodable bytes (surrogate escapes) are
    rebuilt with ``printf`` octal escapes plus a trailing sentinel, which keeps trailing
    newlines alive through command substitution.
    """
    # The strict UTF-8 check and os.fsencode below agree because the filesystem encoding is UTF-8
    # on the supported platform (Ubuntu); only surrogate escapes fail the strict encode.
    try:
        path.encode("utf-8")
    except UnicodeEncodeError:
        escaped = "".join(f"\\{byte:03o}" for byte in os.fsencode(path))
        return f"_p=$(printf '{escaped}x'); _p=${{_p%x}}", '"$_p"'
    return "", shlex.quote(path)


def _broken_link_stat(name: str, lstat_entry: StatEntry) -> Stat:
    """Build the ``Stat`` of a link whose target cannot be followed (missing, loop, unreadable)."""
    return Stat(
        size=lstat_entry.size or 0,
        modified=lstat_entry.modified_time or 0,
        mode=lstat_entry.permissions,
        is_hidden=name.startswith("."),
        is_directory=False,
        is_executable=False,
        is_symlink=True,
        is_broken_symlink=True,
    )


# The remote tools run with the C locale so that their stderr wording (matched below) does not depend on the remote user's language.
# File names are raw bytes in every locale, so the stdout of ``stat --printf`` does not change.
_C_LOCALE = "LC_ALL=C; export LC_ALL"


def _build_list_command(path: str, follow_symlinks: bool) -> str:
    """Build the remote command that lists *path* in ``_STAT_FORMAT``.

    The path is only ever a single quoted argument of ``cd --``; the whole script is quoted
    again for ``sh -c`` so the login shell of the remote user does not interpret it.
    """
    prelude, quoted = _quote_path(path)
    prelude = f"{_C_LOCALE}\n{prelude}\n" if prelude else f"{_C_LOCALE}\n"
    stat_opts = "-L " if follow_symlinks else ""
    script = (
        f"{prelude}cd -- {quoted} 2>/dev/null || {{ [ -d {quoted} ] && exit {_CD_FAILED_EXIT}; exit {_CD_MISSING_EXIT}; }}\n"
        f"find . -maxdepth 1 -mindepth 1 -exec stat {stat_opts}--printf '{_STAT_FORMAT}' -- {{}} +"
    )
    return f"sh -c {shlex.quote(script)}"


# Capability probe, run only after an unexplained listing failure: the production pipeline on "/" (always present and statable).
_PROBE_SCRIPT = f"{_C_LOCALE}\nfind / -maxdepth 0 -exec stat --printf '{_STAT_FORMAT}' -- {{}} +"
_PROBE_COMMAND = f"sh -c {shlex.quote(_PROBE_SCRIPT)}"
_NO_ERROR_TEXT = "listing command failed"


@dataclass(frozen=True)
class _CommandRun:
    """Outcome of one remote command."""

    stdout: bytes
    stderr: str
    exit_code: int


_DANGLING_LINK_ERRORS = ("no such file or directory", "too many levels of symbolic links")


def _only_dangling_link_errors(stderr: str) -> bool:
    """True when every non-empty line of *stderr* reports a link that ``stat -L`` could not follow."""
    lines = [line.lower() for line in stderr.splitlines() if line.strip()]
    return bool(lines) and all(any(text in line for text in _DANGLING_LINK_ERRORS) for line in lines)


class _ExecRefusedError(OSError):
    """The server refused the exec request on a live transport (an SFTP-only account)."""


def _parse_stat_output(output: bytes) -> dict[str, StatEntry]:
    """Parse NUL-separated ``stat --printf`` output (see ``_STAT_FORMAT``) into entries by name."""
    fields = output.split(b"\0")
    if fields.pop():  # a complete stream ends with NUL, so the last element is empty
        _logger.warning("Dropping truncated trailing field in stat output")
    if len(fields) % _STAT_FIELD_COUNT:
        _logger.warning("Dropping incomplete trailing record in stat output")
    entries: dict[str, StatEntry] = {}
    for start in range(0, len(fields) - _STAT_FIELD_COUNT + 1, _STAT_FIELD_COUNT):
        size, perms, owner, mtime, atime, ctime, ftype, raw_name = fields[start : start + _STAT_FIELD_COUNT]
        file_type = _decode(ftype)
        name = _decode(raw_name).removeprefix("./")
        try:
            entry = StatEntry(
                size=int(size),
                permissions=int(perms, 8),
                owner=_decode(owner),
                modified_time=float(mtime),
                access_time=float(atime),
                change_time=float(ctime),
                is_directory=file_type == "directory",
                is_symlink=file_type == "symbolic link",
                name=name,
            )
        except ValueError:
            _logger.warning("Unparsable stat record for %r; ignoring the rest of the output", name)
            break
        entries[name] = entry
    return entries


# SFTP attribute flags (draft-ietf-secsh-filexfer-02, section 5); paramiko's own FLAG_EXTENDED is mis-spelled in its source.
_ATTR_FLAG_SIZE = 0x1
_ATTR_FLAG_UIDGID = 0x2
_ATTR_FLAG_PERMISSIONS = 0x4
_ATTR_FLAG_ACMODTIME = 0x8
_ATTR_FLAG_EXTENDED = 0x80000000


class _RawSftp(Protocol):
    """The request channel of ``paramiko.SFTPClient`` that the directory reader needs (it is not part of the paramiko stubs)."""

    def _request(self, t: int, *args: object) -> tuple[int, paramiko.Message]: ...


class _RawSftpSender(Protocol):
    """The packet writer of ``paramiko.SFTPClient`` (not part of the paramiko stubs)."""

    _send_packet: Callable[[int, object], None]


# Pipelined uploads send pieces of this size and together leave at most this many requests of 32 KiB unanswered:
# about 1 MiB, below the 2 MiB window of an OpenSSH SFTP channel.
_WRITE_PIECE = 64 * 1024
_MAX_UNACKNOWLEDGED = 32
# A download keeps at most this many read requests of 32 KiB (4 MiB) in flight or buffered.  The requests are a few
# KiB together, so they cannot fill the server's input window even when the server cannot send its replies.
_READ_AHEAD = 128


class _RawSftpReplies(Protocol):
    """The reply bookkeeping of ``paramiko.SFTPClient`` (not part of the paramiko stubs)."""

    _lock: threading.Lock
    _expecting: dict[int, object]

    def _read_response(self) -> object: ...

    def _async_request(self, fileobj: object, t: int, *args: object) -> int: ...

    def _convert_status(self, msg: paramiko.Message) -> None: ...


class _RawSftpFile(Protocol):
    """The request and reply handling of an ``SFTPFile`` (not part of the paramiko stubs)."""

    sftp: _RawSftpReplies
    handle: bytes
    _reqs: deque[int]

    def _check_exception(self) -> None: ...


@dataclass(frozen=True)
class _SftpAttrs:
    """The attributes of an SFTP directory entry that a listing uses (``None`` when the server omitted them)."""

    size: int | None
    mode: int | None
    atime: int | None
    mtime: int | None


def _read_attrs(msg: paramiko.Message) -> _SftpAttrs:
    """Read one SFTP ``ATTRS`` structure from *msg*, consuming exactly its bytes."""
    flags = msg.get_int()
    size = msg.get_int64() if flags & _ATTR_FLAG_SIZE else None
    if flags & _ATTR_FLAG_UIDGID:
        msg.get_int()
        msg.get_int()
    mode = msg.get_int() if flags & _ATTR_FLAG_PERMISSIONS else None
    atime = mtime = None
    if flags & _ATTR_FLAG_ACMODTIME:
        atime = msg.get_int()
        mtime = msg.get_int()
    if flags & _ATTR_FLAG_EXTENDED:
        for _ in range(msg.get_int()):
            msg.get_string()
            msg.get_string()
    return _SftpAttrs(size, mode, atime, mtime)


def _attrs_of(attrs: paramiko.SFTPAttributes) -> _SftpAttrs:
    """Convert the result of ``SFTPClient.stat`` to :class:`_SftpAttrs`."""
    return _SftpAttrs(attrs.st_size, attrs.st_mode, attrs.st_atime, attrs.st_mtime)


def _entry_from_attrs(name: str, attrs: _SftpAttrs) -> StatEntry:
    """Map SFTP attributes to a :class:`StatEntry`; owner and change time do not exist in SFTP (they never reach ``Stat``)."""
    mode = attrs.mode if attrs.mode is not None else 0
    return StatEntry(
        size=attrs.size or 0,
        permissions=stat.S_IMODE(mode),
        owner="",
        modified_time=float(attrs.mtime or 0),
        access_time=float(attrs.atime or 0),
        change_time=0.0,
        is_directory=stat.S_ISDIR(mode),
        is_symlink=stat.S_ISLNK(mode),
        name=name,
    )


def _send_whole_packets(sftp: paramiko.SFTPClient) -> None:
    """Make every packet of *sftp* leave in one piece.

    paramiko sends a packet larger than the channel's maximum in several parts without a lock, and a garbage
    collected ``SFTPFile`` sends its close from whatever thread runs the collection, without the session lock.
    The lock is reentrant because that collection may run inside a send of the same thread.
    """
    sender = cast("_RawSftpSender", sftp)
    send = sender._send_packet
    lock = threading.RLock()

    def send_locked(t: int, packet: object) -> None:
        with lock:
            send(t, packet)

    sender._send_packet = send_locked


class _LockedSftpFile:
    """An SFTPFile whose every channel access holds the session lock: paramiko reads replies without one.

    paramiko reads the acknowledgements of pipelined writes later, by number, and every other reader of the channel
    drops them: the writer then waits forever.  So each write request is handed to the file object, to which any
    reader delivers its reply; the file keeps an error for the next ``write`` or ``close``.
    """

    def __init__(self, file: paramiko.SFTPFile, lock: threading.RLock, unacknowledged: deque[int]) -> None:
        self._file = file
        self._lock = lock
        self._unacknowledged = unacknowledged  # unanswered writes of the whole session, in the order sent

    def read(self, size: int | None = None) -> bytes:
        with self._lock:
            return self._file.read(size)

    def write(self, data: bytes) -> int:
        for start in range(0, len(data), _WRITE_PIECE):
            with self._lock:  # per piece, so others get a turn during a large write
                self._write_piece(data[start : start + _WRITE_PIECE])
        return len(data)

    def close(self) -> None:
        error: OSError | EOFError | None = None
        with self._lock:
            while True:
                try:
                    self._file.close()
                    self._check_write_error()
                    break
                except (OSError, EOFError) as exc:
                    # A failed write stops the close before the handle is released: close again.
                    if error is not None:
                        break
                    error = exc
        if isinstance(error, EOFError):
            raise OSError(str(error)) from error
        if error is not None:
            raise error

    def _write_piece(self, piece: bytes) -> None:
        self._file.write(piece)
        if not isinstance(self._file, paramiko.SFTPFile):  # test doubles have no outstanding requests
            return
        raw = cast("_RawSftpFile", self._file)
        client = raw.sftp
        with client._lock:
            for number in raw._reqs:
                client._expecting[number] = self._file
        self._unacknowledged.extend(raw._reqs)
        raw._reqs.clear()
        # The backlog is bounded: a server that cannot send (a download of this connection that nobody consumes at
        # the moment) stops taking input, and a send blocked on that would hold the session lock for ever.
        while self._count_unacknowledged(client) > _MAX_UNACKNOWLEDGED:
            client._read_response()
        try:
            self._check_write_error()
        except EOFError as exc:
            raise OSError(str(exc)) from exc

    def _count_unacknowledged(self, client: _RawSftpReplies) -> int:
        """Forget the answered requests (replies arrive in order) and return how many are still open."""
        while self._unacknowledged and self._unacknowledged[0] not in client._expecting:
            self._unacknowledged.popleft()
        return len(self._unacknowledged)

    def _check_write_error(self) -> None:
        if isinstance(self._file, paramiko.SFTPFile):
            cast("_RawSftpFile", self._file)._check_exception()


class _SftpDownload:
    """Sequential read of a remote file with a bounded number of read requests ahead of the consumer.

    paramiko's ``prefetch`` requests the whole file at once.  Beyond what the server's input window holds, its thread
    blocks in ``send``, and with it every other request of the session.  With ``max_concurrent_requests`` it leaves a
    thread spinning after a cancelled download.  So the consumer sends the requests itself, under the session lock,
    and whoever reads the channel hands the replies to this object.
    """

    def __init__(self, file: paramiko.SFTPFile, lock: threading.RLock) -> None:
        """Take over the reading of *file*; the caller holds *lock*."""
        self._file = file
        self._lock = lock
        self._client = cast("_RawSftpFile", file).sftp
        self._size = file.stat().st_size or 0
        self._offset = 0  # of the next byte not yet taken from a reply
        self._requested = 0  # end of the last request
        self._requests: deque[tuple[int, int, int]] = deque()  # number, offset, length
        self._replies: dict[int, tuple[int, paramiko.Message] | None] = {}  # None until the reply arrived
        self._buffer = b""
        self._closed = False

    def read(self, size: int | None = None) -> bytes:
        data = bytearray()
        while size is None or size < 0 or len(data) < size:
            with self._lock:  # per block, so others get a turn during a large read
                if self._closed:
                    raise OSError("File is closed")
                self._buffer = self._buffer or self._next_block()
                end = len(self._buffer) if size is None or size < 0 else size - len(data)
                piece, self._buffer = self._buffer[:end], self._buffer[end:]
            if not piece:
                break
            data += piece
        return bytes(data)

    def close(self) -> None:
        with self._lock:
            self._closed = True
            self._file.close()
            self._forget_requests()
            self._buffer = b""

    def _request(self, offset: int, length: int) -> tuple[int, int, int]:
        handle = cast("_RawSftpFile", self._file).handle
        number = self._client._async_request(self, CMD_READ, handle, int64(offset), length)
        self._replies[number] = None
        return number, offset, length

    def _async_response(self, t: int, msg: paramiko.Message, number: int) -> None:
        """Called by paramiko, in whichever thread reads the channel."""
        if number in self._replies:
            self._replies[number] = (t, msg)

    def _forget_requests(self) -> None:
        self._requests.clear()
        self._replies.clear()
        self._requested = self._offset

    def _next_block(self) -> bytes:
        while self._requested < self._size and len(self._requests) < _READ_AHEAD:
            length = min(paramiko.SFTPFile.MAX_REQUEST_SIZE, self._size - self._requested)
            self._requests.append(self._request(self._requested, length))
            self._requested += length
        if not self._requests:
            # Past the size the file had when it was opened: read on, in case it grew.
            self._file.seek(self._offset)
            data = self._file.read(paramiko.SFTPFile.MAX_REQUEST_SIZE)
            self._offset += len(data)
            return data
        number, offset, length = self._requests[0]
        while (reply := self._replies[number]) is None:
            self._client._read_response()
        t, msg = reply
        if t != CMD_DATA:
            self._forget_requests()  # a later read asks again from here
            if t == CMD_STATUS:
                try:
                    self._client._convert_status(msg)  # raises the error of the server
                except EOFError:  # the file shrank
                    self._size = self._offset
                    return self._next_block()
            raise paramiko.SFTPError("Expected data")
        self._requests.popleft()
        del self._replies[number]
        data = msg.get_string()
        self._offset += len(data)
        if 0 < len(data) < length:  # a short answer: ask for the rest before using what comes after
            self._requests.appendleft(self._request(offset + len(data), length - len(data)))
        return data


class _LockedRawSftp:
    """The raw request channel for ``_sftp_readdir``, holding the session lock per request, not per listing."""

    def __init__(self, sftp: _RawSftp, lock: threading.RLock) -> None:
        self._sftp = sftp
        self._lock = lock

    def _request(self, t: int, *args: object) -> tuple[int, paramiko.Message]:
        with self._lock:
            return self._sftp._request(t, *args)


def _sftp_readdir(sftp: _RawSftp, path: bytes) -> list[tuple[bytes, _SftpAttrs]]:
    """List *path* with OPENDIR/READDIR/CLOSE, returning the raw byte names without ``.`` and ``..``.

    ``SFTPClient.listdir_attr`` decodes names strictly and leaks the server handle when that fails;
    this loop keeps names as bytes and always closes the handle.
    Server status replies surface as the ``OSError`` subclasses of ``SFTPClient._convert_status``.
    """
    reply, msg = sftp._request(CMD_OPENDIR, path)
    if reply != CMD_HANDLE:
        raise paramiko.SFTPError("Expected handle")
    handle = msg.get_binary()
    entries: list[tuple[bytes, _SftpAttrs]] = []
    try:
        while True:
            try:
                reply, msg = sftp._request(CMD_READDIR, handle)
            except EOFError:
                break
            if reply != CMD_NAME:
                raise paramiko.SFTPError("Expected name response")
            for _ in range(msg.get_int()):
                name = msg.get_string()
                msg.get_string()  # the long name line
                attrs = _read_attrs(msg)
                if name not in {b".", b".."}:
                    entries.append((name, attrs))
    finally:
        with contextlib.suppress(*_CLOSE_ERRORS):
            sftp._request(CMD_CLOSE, handle)
    return entries


class HostKeyError(Exception):
    """Base class of the host-key refusals; carries the host name and the presented key."""

    def __init__(self, hostname: str, key: paramiko.PKey, message: str) -> None:
        super().__init__(message)
        self.hostname = hostname
        self.key = key

    @property
    def fingerprint(self) -> str:
        """SHA-256 fingerprint of the presented key in OpenSSH display format."""
        return fingerprint(self.key)

    @property
    def key_type(self) -> str:
        """Normalised key-type name, e.g. ``'ED25519'`` or ``'RSA'``."""
        return key_type_name(self.key)


class UnknownHostKeyError(HostKeyError):
    """Raised when the server's host key is not in known_hosts."""

    def __init__(self, hostname: str, key: paramiko.PKey) -> None:
        super().__init__(hostname, key, f"Server {hostname!r} not found in known_hosts")


class RevokedHostKeyError(HostKeyError):
    """Raised when the presented key is listed on an ``@revoked`` line."""

    def __init__(self, hostname: str, key: paramiko.PKey) -> None:
        super().__init__(hostname, key, f"Host key of {hostname!r} is revoked")


class ChangedHostKeyError(HostKeyError):
    """Raised when the presented key differs from the stored one (``expected_key``)."""

    def __init__(self, hostname: str, key: paramiko.PKey, expected_key: paramiko.PKey) -> None:
        super().__init__(hostname, key, f"Host key of {hostname!r} has changed")
        self.expected_key = expected_key


class ConfirmedKeyMismatchError(HostKeyError):
    """Raised when the server presents another key than the one the user confirmed (``confirmed_key``)."""

    def __init__(self, hostname: str, key: paramiko.PKey, confirmed_key: paramiko.PKey) -> None:
        super().__init__(hostname, key, f"Server {hostname!r} presented a different key than the one confirmed")
        self.confirmed_key = confirmed_key


class _CaptureUnknownHostPolicy(paramiko.MissingHostKeyPolicy):
    """First-attempt policy: refuse a revoked key, otherwise raise :class:`UnknownHostKeyError`."""

    def __init__(self, known_hosts: KnownHosts) -> None:
        self._known_hosts = known_hosts

    def missing_host_key(self, client: paramiko.SSHClient, hostname: str, key: paramiko.PKey) -> None:
        if self._known_hosts.is_revoked(key):
            raise RevokedHostKeyError(hostname, key)
        raise UnknownHostKeyError(hostname, key)


def _translate_bad_host_key(exc: paramiko.BadHostKeyException, name: str, known_hosts: KnownHosts | None, confirmed_key: paramiko.PKey | None) -> HostKeyError:
    """Map paramiko's ``BadHostKeyException``: revoked wins, then confirmation mismatch, else changed key."""
    if known_hosts is not None and known_hosts.is_revoked(exc.key):
        return RevokedHostKeyError(name, exc.key)
    if confirmed_key is not None:
        return ConfirmedKeyMismatchError(name, exc.key, confirmed_key)
    return ChangedHostKeyError(name, exc.key, exc.expected_key)


DEFAULT_CONNECT_TIMEOUT = 30.0
_MIN_ATTEMPT_SECONDS = 0.1
_HINT_SLACK_SECONDS = 1.0  # paramiko's own phase timeouts are only hints; the watchdog is the guarantee and must win the race
_LOCAL_ORIGIN = ("127.0.0.1", 0)
_CLOSE_ERRORS = (OSError, paramiko.SSHException, EOFError)

type HopCredentials = tuple[str | None, str | None]  # (username, password) typed by the user for one hop


def _hop_label(hop: SshHop) -> str:
    where = f"{hop.hostname}:{hop.port}"
    return repr(where) if hop.alias == hop.hostname else f"{hop.alias!r} ({where})"


def _hop_role(index: int, count: int) -> str:
    if count == 1:
        return "host"
    return "target host" if index == count - 1 else "jump host"


class SshHopError(Exception):
    """A hop of the route failed; the message names the hop (and, behind jumps, the route position)."""

    def __init__(self, route: Sequence[SshHop], index: int, message: str) -> None:
        super().__init__(message)
        self.hop = route[index]
        self.index = index
        self.count = len(route)


class SshConnectTimeoutError(SshHopError):
    """The hop was not established within ``connect_timeout`` seconds."""

    def __init__(self, route: Sequence[SshHop], index: int, seconds: float) -> None:
        role = _hop_role(index, len(route))
        super().__init__(route, index, f"Timed out after {seconds:g} s connecting to {role} {_hop_label(route[index])}.")


class HopAuthenticationError(paramiko.AuthenticationException):
    """Authentication failed on a hop; subclass of the paramiko error so existing handlers keep working."""

    def __init__(self, route: Sequence[SshHop], index: int) -> None:
        super().__init__(f"Authentication failed for {_hop_role(index, len(route))} {_hop_label(route[index])}.")
        self.hop = route[index]
        self.index = index
        self.count = len(route)


class NoAuthenticationMethodError(paramiko.SSHException):
    """No password, key or agent was left to try on a hop: paramiko's plain SSHException, typed so the password prompt can follow.

    It is deliberately not an ``AuthenticationException``, which callers treat as a rejected login.
    """

    def __init__(self, route: Sequence[SshHop], index: int) -> None:
        super().__init__(f"No password, key or agent is available for {_hop_role(index, len(route))} {_hop_label(route[index])}.")
        self.hop = route[index]
        self.index = index
        self.count = len(route)


def _hop_failure(route: Sequence[SshHop], index: int, cause: BaseException) -> SshHopError:
    text = str(cause) or type(cause).__name__
    label = _hop_label(route[index])
    if index == len(route) - 1:
        return SshHopError(route, index, f"Target host {label} via {route[index - 1].alias}: {text}")
    return SshHopError(route, index, f"Jump host {label}, hop {index + 1} of {len(route)}: {text}")


def _run_closer(closer: Callable[[], None]) -> None:
    with contextlib.suppress(*_CLOSE_ERRORS):
        closer()


def _close_socket(sock: socket.socket) -> None:
    with contextlib.suppress(OSError):
        sock.shutdown(socket.SHUT_RDWR)  # wakes a blocked recv; close() alone may not
    sock.close()


class _Deadline:
    """Wall-clock deadline of one hop; when it passes, a timer closes everything registered (socket, channel, client)."""

    def __init__(self, seconds: float) -> None:
        self.seconds = seconds
        self._end = time.monotonic() + seconds
        self._expired = threading.Event()
        self._done = False
        self._closers: list[Callable[[], None]] = []
        self._lock = threading.Lock()
        self._timer = threading.Timer(seconds, self._expire)
        self._timer.daemon = True

    def __enter__(self) -> Self:
        self._timer.start()
        return self

    def __exit__(self, *_exc: object) -> None:
        with self._lock:
            self._done = True  # a timer that fires after a successful hop must not close its resources
        self._timer.cancel()

    def finish(self) -> bool:
        """Mark the hop done so the watchdog can no longer close it; return False if the deadline already expired (resources are closed)."""
        with self._lock:
            if self._expired.is_set() or time.monotonic() >= self._end:
                return False
            self._done = True
            return True

    @property
    def expired(self) -> bool:
        return self._expired.is_set() or time.monotonic() >= self._end

    def remaining(self) -> float:
        return max(self._end - time.monotonic(), _MIN_ATTEMPT_SECONDS)

    def register(self, closer: Callable[[], None]) -> None:
        with self._lock:
            if not self._expired.is_set():
                self._closers.append(closer)
                return
        _run_closer(closer)

    def _expire(self) -> None:
        with self._lock:
            if self._done:
                return
            self._expired.set()
            closers = list(reversed(self._closers))
        for closer in closers:
            _run_closer(closer)


def _open_tcp(host: str, port: int, deadline: _Deadline) -> socket.socket:
    """Connect to *host*:*port*, trying every address; each attempt is bounded by the remaining time and closable by the watchdog.

    ``getaddrinfo`` itself cannot be interrupted (documented limit).
    """
    last_error: OSError | None = None
    for family, kind, proto, _canon, address in socket.getaddrinfo(host, port, type=socket.SOCK_STREAM):
        sock = socket.socket(family, kind, proto)
        deadline.register(functools.partial(_close_socket, sock))
        try:
            sock.settimeout(deadline.remaining())
            sock.connect(address)
        except OSError as exc:
            _close_socket(sock)
            last_error = exc
            continue
        return sock
    raise last_error if last_error is not None else OSError(f"No address found for {host}")


@dataclass(frozen=True)
class _HopLink:
    sock: socket.socket | paramiko.Channel
    timeout: float
    register: Callable[[Callable[[], None]], None]


@dataclass(frozen=True)
class _RouteContext:
    locations: KnownHostsLocations
    ssh_config: SshConfig
    connect_timeout: float
    trusted_host_keys: Mapping[str, paramiko.PKey]
    credentials: Mapping[int, HopCredentials] = field(repr=False)
    target_password: str | None = field(repr=False)


@dataclass
class _Connected:
    clients: list[paramiko.SSHClient]
    sftp: paramiko.SFTPClient
    save_error: OSError | None
    login_dir: str


def _store_confirmed_key(client: paramiko.SSHClient, locations: KnownHostsLocations, ssh_config: SshConfig, alias: str, name: str, key: paramiko.PKey) -> OSError | None:
    """Append *key* once the host key was verified; return the ``OSError`` instead of raising (DEC-17 Q3).

    ``HashKnownHosts`` is looked up by *alias* (the name as the user wrote it); the entry is stored under *name*.

    Raises:
        ChangedHostKeyError: known_hosts already holds a different key of the same type for the host
            (another window stored it meanwhile); nothing is stored and *client* is closed (DEC-19).
    """
    try:
        hashed = ssh_config.lookup(alias).hash_known_hosts
        append_host_key(locations.user_known_hosts, name, key, hashed=hashed)
    except HostKeyConflictError as exc:
        client.close()
        raise ChangedHostKeyError(name, key, exc.stored_key) from exc
    except OSError as exc:
        _logger.warning("Could not save host key for %s to %s: %s", name, locations.user_known_hosts, exc)
        return exc
    return None


def _connect_checked(
    hostname: str,
    port: int,
    username: str | None,
    key_filename: list[str] | None,
    password: str | None,
    trusted_host_key: paramiko.PKey | None,
    locations: KnownHostsLocations,
    *,
    link: _HopLink,
    ssh_config: SshConfig,
    alias: str,
) -> tuple[paramiko.SSHClient, OSError | None]:
    """Connect over *link* with ``known_hosts`` verification; returns the client and a save error (if any)."""
    known_hosts = read_known_hosts([locations.user_known_hosts, locations.system_known_hosts])
    name = host_key_name(hostname, port)
    client = paramiko.SSHClient()
    link.register(client.close)
    if trusted_host_key is None:
        known_hosts.apply_to(client.get_host_keys(), name)
        client.set_missing_host_key_policy(_CaptureUnknownHostPolicy(known_hosts))
    else:
        if known_hosts.is_revoked(trusted_host_key):
            raise RevokedHostKeyError(name, trusted_host_key)
        client.get_host_keys().add(name, trusted_host_key.get_name(), trusted_host_key)
        client.set_missing_host_key_policy(paramiko.RejectPolicy())
    try:
        client.connect(
            hostname,
            port=port,
            username=username,
            key_filename=cast("str | None", key_filename),  # paramiko accepts a list; its stub only declares str
            password=password,
            sock=link.sock,
            timeout=link.timeout,
            banner_timeout=link.timeout,
            auth_timeout=link.timeout,
        )
    except paramiko.BadHostKeyException as exc:
        client.close()
        raise _translate_bad_host_key(exc, name, known_hosts, trusted_host_key) from exc
    except paramiko.AuthenticationException:
        # paramiko verifies the host key before it authenticates (client.py:454-483), so the confirmed key is verified here.
        if trusted_host_key is not None:
            _store_confirmed_key(client, locations, ssh_config, alias, name, trusted_host_key)
        client.close()
        raise
    except BaseException:
        client.close()
        raise
    save_error = _store_confirmed_key(client, locations, ssh_config, alias, name, trusted_host_key) if trusted_host_key is not None else None
    return client, save_error


def _open_link(hop: SshHop, previous: paramiko.SSHClient | None, deadline: _Deadline) -> socket.socket | paramiko.Channel:
    if previous is None:
        return _open_tcp(hop.hostname, hop.port, deadline)
    transport = previous.get_transport()
    if transport is None:
        raise paramiko.SSHException("The jump host connection is closed")
    channel = transport.open_channel("direct-tcpip", (hop.hostname, hop.port), _LOCAL_ORIGIN, timeout=deadline.remaining())
    deadline.register(channel.close)
    return channel


def _close_link(sock: socket.socket | paramiko.Channel | None) -> None:
    if isinstance(sock, socket.socket):
        _close_socket(sock)
    elif sock is not None:
        _run_closer(sock.close)


def _translate_hop_error(exc: BaseException, route: Sequence[SshHop], index: int, deadline: _Deadline) -> BaseException:
    """Timeout first, then authentication; transport errors are wrapped only when jumps are involved (single hop errors stay as today)."""
    transport_error = isinstance(exc, (OSError, paramiko.SSHException, EOFError))
    if transport_error and deadline.expired:
        return SshConnectTimeoutError(route, index, deadline.seconds)
    if type(exc) is paramiko.SSHException and str(exc) == "No authentication methods available":
        return NoAuthenticationMethodError(route, index)  # paramiko's only signal for "nothing to try": the password prompt follows
    if isinstance(exc, paramiko.AuthenticationException):
        return HopAuthenticationError(route, index)
    if transport_error and len(route) > 1:
        return _hop_failure(route, index, exc)
    return exc


def _hop_login(ctx: _RouteContext, route: Sequence[SshHop], index: int) -> tuple[str | None, str | None, list[str] | None]:
    """Return ``(username, password, key_filename)``: typed credentials replace the configured identity, only the target gets the password."""
    hop = route[index]
    typed = ctx.credentials.get(index)
    if typed is not None:
        return (typed[0] or hop.user), typed[1], None
    password = ctx.target_password if index == len(route) - 1 else None
    return hop.user, password, (list(hop.identity_files) or None)


def _login_directory(sftp: paramiko.SFTPClient) -> str:
    """Return the directory the SFTP session starts in, or ``"/"`` when the server cannot tell.

    OpenSSH resolves ``.`` to the working directory of the session, which is the user's home,
    ``/`` under a chroot or the ``-d`` directory of ``internal-sftp``.
    """
    try:
        resolved = sftp.normalize(".")
    except (OSError, paramiko.SSHException, paramiko.SFTPError, EOFError, UnicodeDecodeError) as exc:
        _logger.warning("Cannot determine the SSH login directory (%s); using /", exc)
        return "/"
    if not isinstance(resolved, str) or not resolved.startswith("/"):
        _logger.warning("Unusable SSH login directory %r; using /", resolved)
        return "/"
    return resolved


def _connect_hop(
    route: Sequence[SshHop], index: int, ctx: _RouteContext, previous: paramiko.SSHClient | None, *, open_sftp: bool
) -> tuple[paramiko.SSHClient, paramiko.SFTPClient | None, str | None, OSError | None]:
    hop = route[index]
    username, password, key_filename = _hop_login(ctx, route, index)
    trusted = ctx.trusted_host_keys.get(host_key_name(hop.hostname, hop.port))
    sock: socket.socket | paramiko.Channel | None = None
    with _Deadline(ctx.connect_timeout) as deadline:
        try:
            sock = _open_link(hop, previous, deadline)
            link = _HopLink(sock, deadline.remaining() + _HINT_SLACK_SECONDS, deadline.register)
            client, save_error = _connect_checked(hop.hostname, hop.port, username, key_filename, password, trusted, ctx.locations, link=link, ssh_config=ctx.ssh_config, alias=hop.alias)
            try:
                sftp = client.open_sftp() if open_sftp else None
                login_dir = _login_directory(sftp) if sftp is not None else None
                if not deadline.finish():  # the watchdog fired in the last step: the hop is not usable
                    if sftp is not None:
                        _run_closer(sftp.close)
                    raise SshConnectTimeoutError(route, index, deadline.seconds)
            except BaseException:
                client.close()
                raise
        except HostKeyError:
            _close_link(sock)
            raise
        except BaseException as exc:
            _close_link(sock)
            translated = _translate_hop_error(exc, route, index, deadline)
            if translated is exc:
                raise
            raise translated from exc
    return client, sftp, login_dir, save_error


def _close_reversed(clients: Sequence[paramiko.SSHClient]) -> None:
    for client in reversed(clients):
        _run_closer(client.close)


def _connect_route(route: Sequence[SshHop], ctx: _RouteContext) -> _Connected:
    """Connect hop by hop; on any failure close what was opened, last first."""
    opened: list[paramiko.SSHClient] = []
    save_error: OSError | None = None
    sftp: paramiko.SFTPClient | None = None
    login_dir = "/"
    try:
        for index in range(len(route)):
            last = index == len(route) - 1
            client, sftp, hop_login_dir, error = _connect_hop(route, index, ctx, opened[-1] if opened else None, open_sftp=last)
            if hop_login_dir is not None:
                login_dir = hop_login_dir
            opened.append(client)
            if error is not None and save_error is None:
                save_error = error
            elif error is not None:
                _logger.warning("Further host key could not be saved for hop %d: %s", index, error)
        if sftp is None:
            raise paramiko.SSHException("SFTP session was not opened")
    except BaseException:
        _close_reversed(opened)
        raise
    return _Connected(opened, sftp, save_error, login_dir)


class _SftpAtomicWriter:
    """Upload to a sibling temporary name and replace with ``posix_rename``.

    ``posix_rename`` (the ``posix-rename@openssh.com`` SFTP extension) replaces the
    target atomically, unlike plain SFTP ``rename`` which fails if the target
    already exists.  Servers that do not implement the extension raise ``OSError``
    on ``close()``, leaving the original target untouched and the temporary file
    removed.
    """

    def __init__(self, filesystem: SSHFilesystem, path: VPath) -> None:
        client = filesystem._sftp_client
        lock = filesystem._sftp_lock
        target = _sftp_path(path)
        try:
            with lock:
                mode = client.lstat(target).st_mode
        except FileNotFoundError:
            mode = None
        if mode is not None and stat.S_ISLNK(mode):
            raise ValueError(f"Cannot atomically replace a symlink: {path}")
        self._filesystem = filesystem
        self._path = path
        self._client = client
        self._lock = lock
        self._target = target
        self._tmp = os.fsencode((path.path.parent / f".{path.name}.{uuid.uuid4().hex}.nn-tmp").as_posix())
        with lock:
            file = client.open(self._tmp, "wx")
            file.set_pipelined(True)
            self._file = _LockedSftpFile(file, lock, filesystem._unacknowledged_writes)
        self._done = False

    def write(self, data: bytes) -> int:
        self._file.write(data)
        return len(data)

    def close(self) -> None:
        if self._done:
            return
        self._done = True
        try:
            self._file.close()
            try:
                with self._lock:
                    mode = self._client.stat(self._target).st_mode
            except FileNotFoundError:
                mode = None
            if mode is not None:
                with self._lock:
                    self._client.chmod(self._tmp, stat.S_IMODE(mode))
            try:
                with self._lock:
                    self._client.posix_rename(self._tmp, self._target)
            except (OSError, paramiko.SSHException) as error:
                raise OSError(f"SSH server does not support atomic replacement: {error}") from error
        except BaseException:
            self._remove_tmp()
            raise
        self._filesystem.refresh(self._filesystem.parent(self._path))

    def abort(self) -> None:
        if not self._done:
            self._done = True
            with contextlib.suppress(OSError):  # the data is discarded anyway
                self._file.close()
            self._remove_tmp()

    def _remove_tmp(self) -> None:
        with contextlib.suppress(OSError, paramiko.SSHException), self._lock:
            self._client.remove(self._tmp)


class SSHFilesystem(Filesystem):
    """Filesystem implementation for remote hosts accessed over SSH/SFTP."""

    class _PipelinedWriter:
        """Wraps a locked SFTP file for pipelined writes with a close callback."""

        def __init__(self, f: _LockedSftpFile, on_close: Callable[[], None]) -> None:
            self._f = f
            self._on_close = on_close

        def write(self, data: bytes) -> int:
            return self._f.write(data)

        def close(self) -> None:
            try:
                self._f.close()
            finally:
                self._on_close()

    _ssh_client: paramiko.SSHClient
    _sftp_client: paramiko.SFTPClient

    def __init__(
        self,
        hostname: str,
        port: int = 22,
        username: str | None = None,
        key_filename: str | None = None,
        password: str | None = None,
        ssh_client: paramiko.SSHClient | None = None,
        trusted_host_key: paramiko.PKey | None = None,
        known_hosts: KnownHostsLocations | None = None,
        *,
        route: Sequence[SshHop] | None = None,
        connect_timeout: float = DEFAULT_CONNECT_TIMEOUT,
        trusted_host_keys: Mapping[str, paramiko.PKey] | None = None,
        credentials: Mapping[int, HopCredentials] | None = None,
        ssh_config: SshConfig | None = None,
    ) -> None:
        """Connect to *hostname*:*port* over SSH, reusing *ssh_client* if provided.

        Args:
            hostname: Remote host to connect to.
            port: SSH port (default 22).
            username: Remote username, or ``None`` to use the local username.
            key_filename: Path to private key file, or ``None`` for default discovery.
            password: Password for password-based or key passphrase authentication.
            ssh_client: Pre-configured :class:`paramiko.SSHClient` to reuse; skips all host-key handling here.
            trusted_host_key: The host key the user confirmed after an :class:`UnknownHostKeyError`.
                Only this exact key is trusted for the connection and, once verified, appended to
                ``~/.ssh/known_hosts``.  ``None`` (default) trusts only keys already in known_hosts.
            known_hosts: Locations of the known_hosts and ssh_config files (default: the real ones).
            route: Hops to connect through, jump hosts first and the target last.  ``hostname``/``port``/``username``
                stay the requested values and serve ``uri_for_path`` only when *route* is given.
                ``None`` connects directly to *hostname*.
            connect_timeout: Seconds each hop may take to be established (including the SFTP session of the target).
            trusted_host_keys: Confirmed host keys by known_hosts name, for any hop (in addition to *trusted_host_key*).
            credentials: User name and password typed for a hop, by hop index; they replace the configured identity.
            ssh_config: Parsed ssh_config; loaded from *known_hosts* locations when ``None``.

        Raises:
            UnknownHostKeyError: The host key is not in known_hosts.
            ChangedHostKeyError: The host key differs from the stored one, or known_hosts holds a
                different key of the same type than *trusted_host_key*.
            RevokedHostKeyError: The host key is listed as revoked.
            ConfirmedKeyMismatchError: The server presented another key than *trusted_host_key*.
            SshHopError: A hop failed (named in the message); ``SshConnectTimeoutError`` when it timed out.
            HopAuthenticationError: Authentication failed on a hop.
        """
        self.host_key_save_error: OSError | None = None
        self.host_key_save_path: Path | None = None
        # One SFTP session serves all threads, and paramiko reads replies without a lock: a thread that reads another's
        # reply drops it, and its owner waits forever.  So every call that may read holds this lock, per request or
        # chunk.  Leaf lock: nothing run while holding it takes another lock of this class or calls back.
        # ``close()`` does not take it, so closing can always unblock a stuck request.
        self._sftp_lock = threading.RLock()
        self._unacknowledged_writes: deque[int] = deque()  # request numbers, guarded by _sftp_lock
        # Jump transports must stay referenced while the target rides on their channels.
        self._hop_clients: list[paramiko.SSHClient] = []
        self._owns_client = ssh_client is None
        if ssh_client is None:
            locations = known_hosts if known_hosts is not None else KnownHostsLocations.default()
            hops = list(route) if route else [SshHop(hostname, hostname, port, username, (key_filename,) if key_filename else ())]
            trusted = dict(trusted_host_keys or {})
            if trusted_host_key is not None:
                trusted[host_key_name(hops[-1].hostname, hops[-1].port)] = trusted_host_key
            config = ssh_config if ssh_config is not None else SshConfig.load(locations.config_paths)
            context = _RouteContext(locations, config, connect_timeout, trusted, credentials or {}, password)
            connected = _connect_route(hops, context)
            self._ssh_client = connected.clients[-1]
            self._hop_clients = connected.clients[:-1]
            self._sftp_client = connected.sftp
            self._login_dir = connected.login_dir
            self.host_key_save_error = connected.save_error
            if connected.save_error is not None:
                self.host_key_save_path = locations.user_known_hosts
        else:
            self._ssh_client = ssh_client
            try:
                ssh_client.connect(hostname, port=port, username=username, key_filename=key_filename, password=password)
            except paramiko.BadHostKeyException as exc:
                raise ChangedHostKeyError(host_key_name(hostname, port), exc.key, exc.expected_key) from exc
            self._sftp_client = ssh_client.open_sftp()  # an injected client belongs to the caller
            # The login directory never changes for a connection; home() must not do I/O (it runs on the Textual thread).
            self._login_dir = _login_directory(self._sftp_client)
        _send_whole_packets(self._sftp_client)
        self._stat_cache: dict[tuple[str, bool], dict[str, StatEntry]] = {}
        self._stat_cache_lock = threading.Lock()
        # Listing method of this connection: the GNU command first, SFTP for the rest of the connection once the remote
        # proved to lack GNU find or stat or refused the exec request (ADR-7, DEC-32).
        # _gnu_confirmed: GNU produced a listing, so never probe again.
        self._listing_lock = threading.Lock()
        self._listing_via_sftp = False
        self._gnu_confirmed = False
        self._hostname = hostname
        self._port = port
        self._username = username

    def __eq__(self, value: object) -> bool:
        return isinstance(value, SSHFilesystem) and self._ssh_client == value._ssh_client

    def __hash__(self) -> int:
        return hash(self._ssh_client)

    def __repr__(self) -> str:
        return f"{self.__class__.__name__}({self._ssh_client!r})"

    def close(self) -> None:
        """Close the SFTP session and the target transport, then the jump transports nearest-first.  Idempotent, never raises."""
        _run_closer(self._sftp_client.close)
        if self._owns_client:
            _run_closer(self._ssh_client.close)
        _close_reversed(self._hop_clients)

    @override
    def uri_for_path(self, path: PurePath) -> str:
        _DEFAULT_SSH_PORT = 22
        netloc = self._username + "@" + self._hostname if self._username else self._hostname
        if self._port != _DEFAULT_SSH_PORT:
            netloc = netloc + ":" + str(self._port)
        return f"ssh://{netloc}{path}"

    @override
    def is_same_device(self, path1: VPath, path2: VPath) -> bool:
        return False

    @override
    def cwd(self) -> VPath:
        cwd = self._sftp_client.getcwd()  # a stored value, no request: no lock, so the caller never waits
        if cwd is None:
            cwd = "/"
        return VPath(cwd, self)

    @override
    def root(self) -> VPath:
        return VPath("/", self)

    @override
    def home(self) -> VPath:
        return VPath(self._login_dir, self)

    def _run_command(self, command: str) -> _CommandRun:
        """Run *command*; a refused exec request on a live transport raises ``_ExecRefusedError``, other SSH errors ``OSError``."""
        _logger.debug("Running SSH stat command: %s", command)
        try:
            stdin, stdout, stderr = self._ssh_client.exec_command(command)
            # Send the EOF now.  Left to the garbage collector, it can follow the close of the channel when other
            # threads keep the transport busy, and the server then drops the whole connection.
            stdin.close()
        except paramiko.SSHException as exc:
            transport = self._ssh_client.get_transport()
            if transport is not None and transport.is_active():
                raise _ExecRefusedError(str(exc)) from exc
            raise OSError(str(exc)) from exc
        try:
            output = stdout.read()
            stderr_output = stderr.read().decode("utf-8", "replace")
            _logger.debug("stderr: %s", stderr_output)
            exit_code = stdout.channel.recv_exit_status()
        except paramiko.SSHException as exc:
            raise OSError(str(exc)) from exc
        return _CommandRun(output, stderr_output, exit_code)

    def _cache_listing(self, key: tuple[str, bool], result: dict[str, StatEntry]) -> dict[str, StatEntry]:
        with self._stat_cache_lock:
            self._stat_cache[key] = result
        return result

    def _dir_stat(self, path: str, follow_symlinks: bool) -> dict[str, StatEntry]:
        key = (path, follow_symlinks)
        with self._stat_cache_lock:
            if key in self._stat_cache:
                return self._stat_cache[key]
        if self._listing_via_sftp:
            return self._sftp_dir_stat(path, follow_symlinks)
        try:
            run = self._run_command(_build_list_command(path, follow_symlinks))
        except _ExecRefusedError as exc:
            # The exec itself cannot run, so no probe is needed (DEC-32).
            return self._switch_to_sftp(path, follow_symlinks, str(exc))
        if run.exit_code == _CD_FAILED_EXIT:
            raise PermissionError(13, "Permission denied", path)
        if run.exit_code == _CD_MISSING_EXIT:
            return self._cache_listing(key, {})
        if run.stdout:
            result = _parse_stat_output(run.stdout)
            if result:
                self._gnu_confirmed = True
                if _REPLACEMENT_BYTES in run.stdout:
                    return self._sftp_dir_stat(path, follow_symlinks)
                return self._cache_listing(key, result)
        elif run.exit_code == 0:
            if run.stderr.strip():
                # A BusyBox-style find can exit 0 with empty stdout although stat failed: not an empty directory.
                return self._after_unexplained_failure(path, follow_symlinks, run)
            # Genuinely empty.  This decides nothing about the tools: a non-GNU host also exits 0 for an empty directory.
            return self._cache_listing(key, {})
        else:
            err = run.stderr.lower()
            if "permission denied" in err or "operation not permitted" in err:
                raise PermissionError(13, "Permission denied", path)
        return self._after_unexplained_failure(path, follow_symlinks, run)

    def _after_unexplained_failure(self, path: str, follow_symlinks: bool, run: _CommandRun) -> dict[str, StatEntry]:
        """Handle a failed GNU listing that is neither a permission nor a missing-directory failure.

        On a host where GNU works (confirmed earlier or by the probe) the failure is real and raised.
        Otherwise the host lacks GNU find or stat and the connection lists through SFTP from now on.
        """
        message = next(iter(run.stderr.strip().splitlines()), _NO_ERROR_TEXT)
        if follow_symlinks and _only_dangling_link_errors(run.stderr):
            # Only GNU ``stat -L`` words its failures like this, so no probe is needed.
            # A directory of only links it cannot follow has no followed entries.
            return self._cache_listing((path, follow_symlinks), {})
        try:
            gnu_works = self._gnu_confirmed or self._gnu_probe_succeeds()
        except _ExecRefusedError as exc:
            # The transport is alive but refuses exec: list through SFTP (DEC-32).
            return self._switch_to_sftp(path, follow_symlinks, str(exc))
        if gnu_works:
            raise OSError(errno.EIO, message, path)
        return self._switch_to_sftp(path, follow_symlinks, message)

    def _switch_to_sftp(self, path: str, follow_symlinks: bool, reason: str) -> dict[str, StatEntry]:
        with self._listing_lock:
            first = not self._listing_via_sftp
            self._listing_via_sftp = True
        if first:
            _logger.info("Listing SSH directories through SFTP: GNU find or stat unavailable (%s)", reason)
        return self._sftp_dir_stat(path, follow_symlinks)

    def _gnu_probe_succeeds(self) -> bool:
        """Run the probe; a transport error raises ``OSError`` (a broken connection must not demote the listing method)."""
        run = self._run_command(_PROBE_COMMAND)
        works = run.exit_code == 0 and len(_parse_stat_output(run.stdout)) == 1
        if works:
            self._gnu_confirmed = True
        return works

    def _store_listing(self, path: str, lstat_entries: dict[str, StatEntry], follow_entries: dict[str, StatEntry], follow_symlinks: bool) -> dict[str, StatEntry]:
        """Cache both passes of one listing and return the requested one."""
        with self._stat_cache_lock:
            self._stat_cache[(path, False)] = lstat_entries
            self._stat_cache[(path, True)] = follow_entries
        return follow_entries if follow_symlinks else lstat_entries

    def _sftp_link_target(self, link_path: bytes, name: str) -> StatEntry | None:
        """Return the followed entry of a symlink, or ``None`` when it cannot be followed (missing, loop, unreadable)."""
        try:
            with self._sftp_lock:
                attrs = self._sftp_client.stat(link_path)
        except OSError:
            return None
        except paramiko.SSHException as exc:
            raise OSError(str(exc)) from exc
        return _entry_from_attrs(name, _attrs_of(attrs))

    def _sftp_is_not_a_directory(self, raw_path: bytes) -> bool:
        """True when *raw_path* exists and is not a directory (the generic failure of OPENDIR on a file)."""
        try:
            with self._sftp_lock:
                attrs = self._sftp_client.stat(raw_path)
        except (OSError, paramiko.SSHException):
            return False
        return attrs.st_mode is not None and not stat.S_ISDIR(attrs.st_mode)

    def _sftp_dir_stat(self, path: str, follow_symlinks: bool) -> dict[str, StatEntry]:
        """List *path* through SFTP, filling the cache for both follow modes in one pass.

        Round-trips: one OPENDIR, one READDIR per packet, one CLOSE and one ``stat`` per symlink.
        A missing path and a path that is not a directory list as empty, like the GNU command.
        """
        raw_path = os.fsencode(path)
        try:
            raw_entries = _sftp_readdir(_LockedRawSftp(cast("_RawSftp", self._sftp_client), self._sftp_lock), raw_path)
        except PermissionError as exc:
            raise PermissionError(13, "Permission denied", path) from exc
        except FileNotFoundError:
            return self._store_listing(path, {}, {}, follow_symlinks)
        except (paramiko.SSHException, paramiko.SFTPError) as exc:
            # SFTPError (a protocol violation such as an unexpected reply type) is neither an SSHException nor an OSError.
            raise OSError(str(exc)) from exc
        except OSError:
            if self._sftp_is_not_a_directory(raw_path):
                return self._store_listing(path, {}, {}, follow_symlinks)
            raise
        lstat_entries: dict[str, StatEntry] = {}
        follow_entries: dict[str, StatEntry] = {}
        prefix = raw_path.rstrip(b"/") + b"/"
        for raw_name, attrs in raw_entries:
            name = _decode(raw_name)
            entry = _entry_from_attrs(name, attrs)
            lstat_entries[name] = entry
            if not entry.is_symlink:
                follow_entries[name] = entry
                continue
            target = self._sftp_link_target(prefix + raw_name, name)
            if target is not None:
                follow_entries[name] = target
        return self._store_listing(path, lstat_entries, follow_entries, follow_symlinks)

    @override
    def refresh(self, path: VPath | None = None) -> None:
        with self._stat_cache_lock:
            if path is None:
                self._stat_cache.clear()
            else:
                p = str(path)
                self._stat_cache.pop((p, True), None)
                self._stat_cache.pop((p, False), None)

    @property
    @override
    def capabilities(self) -> FilesystemCapabilities:
        return FilesystemCapabilities(
            streaming_iterdir=False,
            watch=False,
            symlinks=True,
            permissions=True,
            commands=True,
        )

    @property
    @override
    def scheme(self) -> str:
        return "ssh"

    @override
    def exec_command(
        self,
        command: str,
        cwd: VPath,
        should_cancel: Callable[[], bool] | None = None,
    ) -> ExecResult:
        """Run *command* with ``sh -c`` in *cwd* over SSH and wait for it to finish.

        Cancelling (via *should_cancel*) closes the SSH channel; since no PTY is
        requested, the remote shell receives no ``SIGHUP``, so the remote process
        may keep running detached from the closed channel.
        """
        self._assert_vpath(cwd)
        prelude, quoted = _quote_path(cwd.path.as_posix())
        prelude = f"{prelude}\n" if prelude else ""
        script = f"{prelude}cd -- {quoted} || exit 1\n{command}"
        try:
            stdin, stdout, _ = self._ssh_client.exec_command(f"sh -c {shlex.quote(script)} 2>&1")
        except paramiko.SSHException as exc:
            raise OSError(str(exc)) from exc
        stdin.close()
        channel = stdout.channel
        tail = TailBuffer(OUTPUT_TAIL_LIMIT)
        try:
            exit_code = self._drain_channel(channel, tail, should_cancel)
        except (paramiko.SSHException, OSError, EOFError) as exc:
            channel.close()
            raise OSError(str(exc)) from exc
        if exit_code is None:
            channel.close()
            return ExecResult(exit_code=-1, output=tail.text())
        return ExecResult(exit_code=exit_code, output=tail.text())

    @staticmethod
    def _drain_channel(
        channel: paramiko.Channel,
        tail: TailBuffer,
        should_cancel: Callable[[], bool] | None,
    ) -> int | None:
        """Poll *channel* into *tail* until the command exits or cancellation is requested.

        Returns the exit code, or ``None`` if *should_cancel* returned True.
        """
        while True:
            if should_cancel is not None and should_cancel():
                return None
            if channel.recv_ready():
                tail.append(channel.recv(_EXEC_CHUNK_SIZE))
            elif channel.exit_status_ready():
                break
            else:
                time.sleep(_EXEC_POLL_INTERVAL)
        while channel.recv_ready():
            tail.append(channel.recv(_EXEC_CHUNK_SIZE))
        return channel.recv_exit_status()

    @override
    async def iterdir(
        self,
        path: VPath,
        *,
        cancel: threading.Event | None = None,
    ) -> AsyncIterator[VPath]:
        self._assert_vpath(path)
        if cancel is not None and cancel.is_set():
            return

        def _fetch_both() -> tuple[dict[str, StatEntry], dict[str, StatEntry]]:
            return self._dir_stat(str(path), True), self._dir_stat(str(path), False)

        file_list_stat, file_list_lstat = await asyncio.to_thread(_fetch_both)

        for name, lstat_entry in file_list_lstat.items():
            if cancel is not None and cancel.is_set():
                return
            stat_entry = file_list_stat.get(name)
            if stat_entry is None:
                if not lstat_entry.is_symlink:
                    continue
                vp = path / name
                vp._stat = _broken_link_stat(name, lstat_entry)
                yield vp
                continue
            vp = path / name
            vp._stat = Stat(
                size=stat_entry.size or 0,
                modified=stat_entry.modified_time or 0,
                mode=lstat_entry.permissions,
                is_hidden=name.startswith("."),
                is_directory=stat_entry.is_directory,
                is_executable=stat_entry.permissions & 0o111 != 0,
                is_symlink=lstat_entry.is_symlink,
            )
            yield vp

    @override
    def parent(self, path: VPath) -> VPath:
        return VPath(path.path.parent, self)

    @override
    def stat(self, path: VPath) -> Stat:
        if path.path.parent == path.path:
            return Stat(is_directory=True)

        file_list_stat = self._dir_stat(str(path.parent), follow_symlinks=True)
        file_list_lstat = self._dir_stat(str(path.parent), follow_symlinks=False)

        stat = file_list_stat.get(path.path.name)
        lstat = file_list_lstat.get(path.path.name)
        if lstat is None:
            raise FileNotFoundError(f"No such file or directory: {path.path!r}")
        return self._stat_from(path, stat, lstat)

    @staticmethod
    def _stat_from(path: VPath, stat_entry: StatEntry | None, lstat_entry: StatEntry) -> Stat:
        """Build the ``Stat`` of *path* from its followed entry (``None`` when the link cannot be followed) and its lstat entry."""
        if stat_entry is None:
            if lstat_entry.is_symlink:
                return _broken_link_stat(path.name, lstat_entry)
            raise FileNotFoundError(f"No such file or directory: {path.path!r}")
        return Stat(
            size=stat_entry.size or 0,
            modified=stat_entry.modified_time or 0,
            mode=lstat_entry.permissions,
            is_hidden=path.name.startswith("."),
            is_directory=stat_entry.is_directory,
            is_executable=stat_entry.permissions & 0o111 != 0,
            is_symlink=lstat_entry.is_symlink,
        )

    @override
    def stat_uncached(self, path: VPath) -> Stat:
        """Answer with one SFTP ``lstat`` (plus one ``stat`` for a symlink), never from or into the listing cache."""
        if path.path.parent == path.path:
            return Stat(is_directory=True)
        raw_path = _sftp_path(path)
        try:
            with self._sftp_lock:
                attrs = self._sftp_client.lstat(raw_path)
        except (paramiko.SSHException, paramiko.SFTPError) as exc:
            raise OSError(str(exc)) from exc
        lstat_entry = _entry_from_attrs(path.name, _attrs_of(attrs))
        if not lstat_entry.is_symlink:
            return self._stat_from(path, lstat_entry, lstat_entry)
        return self._stat_from(path, self._sftp_link_target(raw_path, path.name), lstat_entry)

    @override
    def read(self, path: VPath) -> StreamReaderLike:
        with self._sftp_lock:
            f = self._sftp_client.open(_sftp_path(path), "rb")
            return _SftpDownload(f, self._sftp_lock)

    @override
    def write(self, path: VPath) -> StreamWriterLike:
        with self._sftp_lock:
            f = self._sftp_client.open(_sftp_path(path), "wb")
            f.set_pipelined(True)
            locked = _LockedSftpFile(f, self._sftp_lock, self._unacknowledged_writes)
        return self._PipelinedWriter(locked, lambda: self.refresh(self.parent(path)))

    @override
    def write_atomic(self, path: VPath) -> AtomicWriterLike:
        self._assert_vpath(path)
        return _SftpAtomicWriter(self, path)

    @override
    def remove(self, path: VPath) -> None:
        self._assert_vpath(path)
        with self._sftp_lock:
            self._sftp_client.remove(_sftp_path(path))
        self.refresh(self.parent(path))

    @override
    def rename(self, src_path: VPath, dst_path: VPath) -> None:
        self._assert_vpath(src_path)
        self._assert_vpath(dst_path)
        with self._sftp_lock:
            self._sftp_client.rename(_sftp_path(src_path), _sftp_path(dst_path))
        self.refresh(self.parent(src_path))
        self.refresh(self.parent(dst_path))

    @override
    def rmdir(self, path: VPath) -> None:
        self._assert_vpath(path)
        with self._sftp_lock:
            self._sftp_client.rmdir(_sftp_path(path))
        self.refresh(self.parent(path))

    @override
    def mkdir(self, path: VPath) -> None:
        self._assert_vpath(path)
        p = _sftp_path(path)
        try:
            with self._sftp_lock:
                self._sftp_client.mkdir(p)
        except OSError as exc:
            # SFTP reports an existing path as a generic failure; the Filesystem contract wants FileExistsError.
            # lstat does not follow links, so a dangling link also counts as existing.
            try:
                with self._sftp_lock:
                    self._sftp_client.lstat(p)
            except OSError:
                raise exc from None
            raise FileExistsError(errno.EEXIST, "File exists", str(path.path)) from None
        self.refresh(self.parent(path))

    @override
    def copy_stat(self, path: VPath, stat: Stat) -> None:
        self._assert_vpath(path)
        p = _sftp_path(path)
        with self._sftp_lock:
            if stat.modified >= 0:
                self._sftp_client.utime(p, (stat.modified, stat.modified))
            if stat.mode >= 0:
                self._sftp_client.chmod(p, stat.mode)

    @override
    def readlink(self, path: VPath) -> str:
        self._assert_vpath(path)
        try:
            with self._sftp_lock:
                result = self._sftp_client.readlink(_sftp_path(path))
        except UnicodeDecodeError:
            # paramiko decodes the reply strictly as UTF-8; read the raw bytes through the shell instead.
            return self._readlink_bytes(path)
        if result is None:
            raise OSError(f"{path} is not a symbolic link")
        return result

    def _readlink_bytes(self, path: VPath) -> str:
        """Read a link target that SFTP cannot decode, via ``readlink -n`` printed as hex.

        ``exec_command`` output is decoded with ``errors="replace"``, which would destroy the bytes, so the script prints hex.
        The trailing ``x`` keeps a trailing newline of the target alive through command substitution.
        """
        prelude, quoted = _quote_path(path.path.as_posix())
        prelude = f"{prelude}\n" if prelude else ""
        script = f'{prelude}t=$(readlink -n -- {quoted} && printf x) || exit 1\nprintf %s "${{t%x}}" | od -An -v -tx1'
        result = self.exec_command(script, self.root())
        if result.exit_code != 0:
            raise OSError(f"{path} is not a symbolic link")
        try:
            raw = bytes.fromhex(result.output)
        except ValueError as exc:
            raise OSError(f"{path}: link target reply was truncated or malformed") from exc
        return _decode(raw)

    @override
    def real_path(self, path: VPath) -> VPath | None:
        self._assert_vpath(path)
        try:
            with self._sftp_lock:
                resolved = self._sftp_client.normalize(_sftp_path(path))
                # normalize resolves a dangling link to its missing target without an error; lstat reports it.
                self._sftp_client.lstat(resolved)
        except UnicodeDecodeError:
            return None  # a non-UTF-8 path cannot be read back; callers rely on the copy depth guard
        except (paramiko.SSHException, paramiko.SFTPError) as exc:
            raise OSError(str(exc)) from exc
        return VPath(resolved, self)

    @override
    def supports_symlinks(self) -> bool:
        return True

    @override
    def symlink(self, path: VPath, target: str) -> None:
        self._assert_vpath(path)
        # SFTPClient.symlink(source, dest) sends the target text first (the OpenSSH order), then the link path.
        with self._sftp_lock:
            self._sftp_client.symlink(os.fsencode(target), _sftp_path(path))
        self.refresh(self.parent(path))
