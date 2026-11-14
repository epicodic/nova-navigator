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
from collections.abc import AsyncIterator, Callable, Mapping, Sequence
from dataclasses import dataclass
from pathlib import Path, PurePath
from typing import Self, cast, override

import paramiko

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
    """Raw stat data parsed from a remote ``stat`` command for a single file."""

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


def _build_list_command(path: str, follow_symlinks: bool) -> str:
    """Build the remote command that lists *path* in ``_STAT_FORMAT``.

    The path is only ever a single quoted argument of ``cd --``; the whole script is quoted
    again for ``sh -c`` so the login shell of the remote user does not interpret it.
    """
    prelude, quoted = _quote_path(path)
    prelude = f"{prelude}\n" if prelude else ""
    stat_opts = "-L " if follow_symlinks else ""
    script = (
        f"{prelude}cd -- {quoted} 2>/dev/null || {{ [ -d {quoted} ] && exit {_CD_FAILED_EXIT}; exit {_CD_MISSING_EXIT}; }}\n"
        f"find . -maxdepth 1 -mindepth 1 -exec stat {stat_opts}--printf '{_STAT_FORMAT}' -- {{}} +"
    )
    return f"sh -c {shlex.quote(script)}"


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
    credentials: Mapping[int, HopCredentials]
    target_password: str | None


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
        target = _sftp_path(path)
        try:
            mode = client.lstat(target).st_mode
        except FileNotFoundError:
            mode = None
        if mode is not None and stat.S_ISLNK(mode):
            raise ValueError(f"Cannot atomically replace a symlink: {path}")
        self._filesystem = filesystem
        self._path = path
        self._client = client
        self._target = target
        self._tmp = os.fsencode((path.path.parent / f".{path.name}.{uuid.uuid4().hex}.nn-tmp").as_posix())
        self._file = client.open(self._tmp, "wx")
        self._file.set_pipelined(True)
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
                mode = self._client.stat(self._target).st_mode
            except FileNotFoundError:
                mode = None
            if mode is not None:
                self._client.chmod(self._tmp, stat.S_IMODE(mode))
            try:
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
            self._file.close()
            self._remove_tmp()

    def _remove_tmp(self) -> None:
        with contextlib.suppress(OSError, paramiko.SSHException):
            self._client.remove(self._tmp)


class SSHFilesystem(Filesystem):
    """Filesystem implementation for remote hosts accessed over SSH/SFTP."""

    class _PipelinedWriter:
        """Wraps a paramiko SFTPFile for pipelined writes with a close callback."""

        def __init__(self, f: paramiko.SFTPFile, on_close: Callable[[], None]) -> None:
            self._f = f
            self._on_close = on_close

        def write(self, data: bytes) -> int:
            result = self._f.write(data)
            return result if isinstance(result, int) else len(data)

        def close(self) -> None:
            self._f.close()
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
        self._stat_cache: dict[tuple[str, bool], dict[str, StatEntry]] = {}
        self._stat_cache_lock = threading.Lock()
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
        cwd = self._sftp_client.getcwd()
        if cwd is None:
            cwd = "/"
        return VPath(cwd, self)

    @override
    def root(self) -> VPath:
        return VPath("/", self)

    @override
    def home(self) -> VPath:
        return VPath(self._login_dir, self)

    def _dir_stat(self, path: str, follow_symlinks: bool) -> dict[str, StatEntry]:
        key = (path, follow_symlinks)
        with self._stat_cache_lock:
            if key in self._stat_cache:
                return self._stat_cache[key]
        command = _build_list_command(path, follow_symlinks)
        try:
            _logger.debug("Running SSH stat command: %s", command)
            _, stdout, stderr = self._ssh_client.exec_command(command)
            output = stdout.read()
            stderr_output = stderr.read().decode("utf-8", "replace")
            _logger.debug("stderr: %s", stderr_output)
            exit_code = stdout.channel.recv_exit_status()
        except paramiko.SSHException as exc:
            raise OSError(str(exc)) from exc
        if exit_code == _CD_FAILED_EXIT:
            raise PermissionError(13, "Permission denied", path)
        if not output and exit_code != 0:
            err = stderr_output.lower()
            if "permission denied" in err or "operation not permitted" in err:
                raise PermissionError(13, "Permission denied", path)
        result = _parse_stat_output(output)
        with self._stat_cache_lock:
            self._stat_cache[key] = result
        return result

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
        if stat is None:
            if lstat.is_symlink:
                return _broken_link_stat(path.name, lstat)
            raise FileNotFoundError(f"No such file or directory: {path.path!r}")

        is_hidden = path.name.startswith(".")

        return Stat(
            size=stat.size or 0,
            modified=stat.modified_time or 0,
            mode=lstat.permissions,
            is_hidden=is_hidden,
            is_directory=stat.is_directory,
            is_executable=stat.permissions & 0o111 != 0,
            is_symlink=lstat.is_symlink,
        )

    @override
    def read(self, path: VPath) -> StreamReaderLike:
        f = self._sftp_client.open(_sftp_path(path), "rb")
        f.prefetch()
        return f

    @override
    def write(self, path: VPath) -> StreamWriterLike:
        f = self._sftp_client.open(_sftp_path(path), "wb")
        f.set_pipelined(True)
        return self._PipelinedWriter(f, lambda: self.refresh(self.parent(path)))

    @override
    def write_atomic(self, path: VPath) -> AtomicWriterLike:
        self._assert_vpath(path)
        return _SftpAtomicWriter(self, path)

    @override
    def remove(self, path: VPath) -> None:
        self._assert_vpath(path)
        self._sftp_client.remove(_sftp_path(path))
        self.refresh(self.parent(path))

    @override
    def rename(self, src_path: VPath, dst_path: VPath) -> None:
        self._assert_vpath(src_path)
        self._assert_vpath(dst_path)
        self._sftp_client.rename(_sftp_path(src_path), _sftp_path(dst_path))
        self.refresh(self.parent(src_path))
        self.refresh(self.parent(dst_path))

    @override
    def rmdir(self, path: VPath) -> None:
        self._assert_vpath(path)
        self._sftp_client.rmdir(_sftp_path(path))
        self.refresh(self.parent(path))

    @override
    def mkdir(self, path: VPath) -> None:
        self._assert_vpath(path)
        p = _sftp_path(path)
        try:
            self._sftp_client.mkdir(p)
        except OSError as exc:
            # SFTP reports an existing path as a generic failure; the Filesystem contract wants FileExistsError.
            # lstat does not follow links, so a dangling link also counts as existing.
            try:
                self._sftp_client.lstat(p)
            except OSError:
                raise exc from None
            raise FileExistsError(errno.EEXIST, "File exists", str(path.path)) from None
        self.refresh(self.parent(path))

    @override
    def copy_stat(self, path: VPath, stat: Stat) -> None:
        self._assert_vpath(path)
        p = _sftp_path(path)
        if stat.modified >= 0:
            self._sftp_client.utime(p, (stat.modified, stat.modified))
        if stat.mode >= 0:
            self._sftp_client.chmod(p, stat.mode)

    @override
    def readlink(self, path: VPath) -> str:
        self._assert_vpath(path)
        try:
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
            resolved = self._sftp_client.normalize(_sftp_path(path))
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
        self._sftp_client.symlink(os.fsencode(target), _sftp_path(path))
        self.refresh(self.parent(path))
