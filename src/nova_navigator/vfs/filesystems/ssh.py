from __future__ import annotations

import asyncio
import base64
import hashlib
import logging
import os
import threading
from collections.abc import AsyncIterator, Callable
from dataclasses import dataclass
from pathlib import PurePath
from typing import override

import paramiko

from ..filesystem import Filesystem, FilesystemCapabilities, Stat, StreamReaderLike, StreamWriterLike
from ..vpath import VPath

_logger = logging.getLogger(__name__)


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


_STAT_COMMAND_ARGS = ["%s", "%a", "%U", "%Y", "%X", "%Z", "%F", "%n"]

_STAT_COMMAND = f"find . -maxdepth 1 -mindepth 1 -exec stat -c '{','.join(_STAT_COMMAND_ARGS)}' {{}} +"
_STAT_COMMAND_FOLLOW_LINKS = f"find . -maxdepth 1 -mindepth 1 -exec stat -L -c '{','.join(_STAT_COMMAND_ARGS)}' {{}} +"


def _parse_stat_output(output: str) -> dict[str, StatEntry]:
    entries: dict[str, StatEntry] = {}
    for line in output.strip().split("\n"):
        parts = line.split(",")
        if len(parts) < len(_STAT_COMMAND_ARGS):
            continue  # malformed line
        name = ",".join(parts[7:]).removeprefix("./")
        entry = StatEntry(
            size=int(parts[0]),
            permissions=int(parts[1], 8),
            owner=parts[2],
            modified_time=float(parts[3]),
            access_time=float(parts[4]),
            change_time=float(parts[5]),
            is_directory=parts[6] == "directory",
            is_symlink=parts[6] == "symbolic link",
            name=name,
        )
        entries[name] = entry
    return entries


_KEY_TYPE_NAMES: dict[str, str] = {
    "ssh-rsa": "RSA",
    "ssh-dss": "DSA",
    "ssh-ed25519": "ED25519",
    "ssh-ed448": "ED448",
    "ecdsa-sha2-nistp256": "ECDSA-256",
    "ecdsa-sha2-nistp384": "ECDSA-384",
    "ecdsa-sha2-nistp521": "ECDSA-521",
}


class UnknownHostKeyError(Exception):
    """Raised when the server's host key is not in known_hosts."""

    def __init__(self, hostname: str, key: paramiko.PKey) -> None:
        super().__init__(f"Server {hostname!r} not found in known_hosts")
        self.hostname = hostname
        self.key = key

    @property
    def fingerprint(self) -> str:
        """SHA-256 fingerprint of the host key in OpenSSH display format."""
        digest = hashlib.sha256(self.key.asbytes()).digest()
        return "SHA256:" + base64.b64encode(digest).decode().rstrip("=")

    @property
    def key_type(self) -> str:
        """Normalised key-type name, e.g. ``'ED25519'`` or ``'RSA'``."""
        return _KEY_TYPE_NAMES.get(self.key.get_name(), self.key.get_name().upper())


class _CaptureUnknownHostPolicy(paramiko.MissingHostKeyPolicy):
    """Host-key policy that raises :class:`UnknownHostKeyError` for unknown hosts."""

    def missing_host_key(self, client: paramiko.SSHClient, hostname: str, key: paramiko.PKey) -> None:
        raise UnknownHostKeyError(hostname, key)


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
        accept_host_key: bool = False,
    ) -> None:
        """Connect to *hostname*:*port* over SSH, reusing *ssh_client* if provided.

        Args:
            hostname: Remote host to connect to.
            port: SSH port (default 22).
            username: Remote username, or ``None`` to use the local username.
            key_filename: Path to private key file, or ``None`` for default discovery.
            password: Password for password-based or key passphrase authentication.
            ssh_client: Pre-configured :class:`paramiko.SSHClient` to reuse.
            accept_host_key: If ``True``, accept and persist unknown host keys to
                ``~/.ssh/known_hosts``.  If ``False`` (default), raise
                :class:`UnknownHostKeyError` for unknown hosts.
        """
        if ssh_client is None:
            self._ssh_client = paramiko.SSHClient()
            self._ssh_client.load_system_host_keys()
            if accept_host_key:
                known_hosts = os.path.expanduser("~/.ssh/known_hosts")
                if os.path.exists(known_hosts):
                    self._ssh_client.load_host_keys(known_hosts)
                self._ssh_client.set_missing_host_key_policy(paramiko.AutoAddPolicy())
            else:
                self._ssh_client.set_missing_host_key_policy(_CaptureUnknownHostPolicy())
        else:
            self._ssh_client = ssh_client

        self._ssh_client.connect(hostname, port=port, username=username, key_filename=key_filename, password=password)
        if accept_host_key and ssh_client is None:
            self._ssh_client.save_host_keys(os.path.expanduser("~/.ssh/known_hosts"))
        self._sftp_client = self._ssh_client.open_sftp()
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
        # TODO
        return VPath("/home", self)

    def _dir_stat(self, path: str, follow_symlinks: bool) -> dict[str, StatEntry]:
        key = (path, follow_symlinks)
        with self._stat_cache_lock:
            if key in self._stat_cache:
                return self._stat_cache[key]
        command = _STAT_COMMAND_FOLLOW_LINKS if follow_symlinks else _STAT_COMMAND
        try:
            _logger.debug("Running SSH stat command: %s", command)
            _, stdout, stderr = self._ssh_client.exec_command(f"cd {path} && {command}")
            output = stdout.read().decode()
            _logger.debug("stdou: %s", output)
            stderr_output = stderr.read().decode()
            _logger.debug("stderr: %s", stderr_output)
        except paramiko.SSHException as exc:
            raise OSError(str(exc)) from exc
        if not output:
            exit_code = stdout.channel.recv_exit_status()
            if exit_code != 0:
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
        )

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
        if stat is None or lstat is None:
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

    # @override
    # def scheme(self) -> str | None:
    #     return "ssh"

    # @override
    # def netloc(self) -> str | None:
    #     transport = self._ssh_client.get_transport()
    #     if transport is None:
    #         return None
    #     peername = transport.getpeername()
    #     return f"{peername[0]}:{peername[1]}"

    @override
    def read(self, path: VPath) -> StreamReaderLike:
        f = self._sftp_client.open(path.path.as_posix(), "rb")
        f.prefetch()
        return f

    @override
    def write(self, path: VPath) -> StreamWriterLike:
        f = self._sftp_client.open(path.path.as_posix(), "wb")
        f.set_pipelined(True)
        return self._PipelinedWriter(f, lambda: self.refresh(self.parent(path)))

    @override
    def remove(self, path: VPath) -> None:
        self._assert_vpath(path)
        self._sftp_client.remove(path.path.as_posix())
        self.refresh(self.parent(path))

    @override
    def rename(self, src_path: VPath, dst_path: VPath) -> None:
        self._assert_vpath(src_path)
        self._assert_vpath(dst_path)
        self._sftp_client.rename(src_path.path.as_posix(), dst_path.path.as_posix())
        self.refresh(self.parent(src_path))
        self.refresh(self.parent(dst_path))

    @override
    def rmdir(self, path: VPath) -> None:
        self._assert_vpath(path)
        self._sftp_client.rmdir(path.path.as_posix())
        self.refresh(self.parent(path))

    @override
    def mkdir(self, path: VPath) -> None:
        self._assert_vpath(path)
        self._sftp_client.mkdir(path.path.as_posix())
        self.refresh(self.parent(path))

    @override
    def copy_stat(self, path: VPath, stat: Stat) -> None:
        self._assert_vpath(path)
        p = path.path.as_posix()
        if stat.modified >= 0:
            self._sftp_client.utime(p, (stat.modified, stat.modified))
        if stat.mode >= 0:
            self._sftp_client.chmod(p, stat.mode)

    @override
    def readlink(self, path: VPath) -> str:
        self._assert_vpath(path)
        result = self._sftp_client.readlink(path.path.as_posix())
        if result is None:
            raise OSError(f"{path} is not a symbolic link")
        return result
