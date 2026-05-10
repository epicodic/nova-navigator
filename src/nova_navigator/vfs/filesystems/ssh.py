from __future__ import annotations

from dataclasses import dataclass
from functools import lru_cache
from typing import cast, override

import paramiko

from ..filesystem import Filesystem, Stat, StreamReaderLike, StreamWriterLike
from ..vpath import VPath


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

_STAT_COMMAND = f"stat -c '{','.join(_STAT_COMMAND_ARGS)}' *(D)"
_STAT_COMMAND_FOLLOW_LINKS = f"stat -L -c '{','.join(_STAT_COMMAND_ARGS)}' *(D)"


def _parse_stat_output(output: str) -> dict[str, StatEntry]:
    entries: dict[str, StatEntry] = {}
    for line in output.strip().split("\n"):
        parts = line.split(",")
        if len(parts) < len(_STAT_COMMAND_ARGS):
            continue  # malformed line
        name = ",".join(parts[7:])
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


class SSHFilesystem(Filesystem):
    """Filesystem implementation for remote hosts accessed over SSH/SFTP."""

    _ssh_client: paramiko.SSHClient
    _sftp_client: paramiko.SFTPClient

    def __init__(self, hostname: str, port: int = 22, ssh_client: paramiko.SSHClient | None = None) -> None:
        """Connect to *hostname*:*port* over SSH, reusing *ssh_client* if provided."""
        if ssh_client is None:
            self._ssh_client = paramiko.SSHClient()
            self._ssh_client.load_system_host_keys()
        else:
            self._ssh_client = ssh_client

        self._ssh_client.connect(hostname, port=port)
        self._sftp_client = self._ssh_client.open_sftp()

    def __eq__(self, value: object) -> bool:
        return isinstance(value, SSHFilesystem) and self._ssh_client == value._ssh_client

    def __hash__(self) -> int:
        return hash(self._ssh_client)

    def __repr__(self) -> str:
        return f"{self.__class__.__name__}({self._ssh_client!r})"

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

    @lru_cache(maxsize=64)  # noqa: B019
    def _dir_stat(self, path: str, follow_symlinks: bool) -> dict[str, StatEntry]:
        command = _STAT_COMMAND_FOLLOW_LINKS if follow_symlinks else _STAT_COMMAND
        _, stdout, _ = self._ssh_client.exec_command(f"cd {path} && {command}")
        output = stdout.read().decode()

        return _parse_stat_output(output)

    @override
    def iterdir(self, path: VPath) -> list[VPath]:
        file_list = self._dir_stat(str(path), follow_symlinks=False)
        return [path / name for name, _ in file_list.items()]

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
            return Stat()

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
        return self._sftp_client.open(path.path.as_posix(), "rb")

    @override
    def write(self, path: VPath) -> StreamWriterLike:
        return cast("StreamWriterLike", self._sftp_client.open(path.path.as_posix(), "wb"))

    @override
    def remove(self, path: VPath) -> None:
        self._assert_vpath(path)
        self._sftp_client.remove(path.path.as_posix())

    @override
    def rename(self, src_path: VPath, dst_path: VPath) -> None:
        self._assert_vpath(src_path)
        self._assert_vpath(dst_path)
        self._sftp_client.rename(src_path.path.as_posix(), dst_path.path.as_posix())

    @override
    def rmdir(self, path: VPath) -> None:
        self._assert_vpath(path)
        self._sftp_client.rmdir(path.path.as_posix())

    @override
    def mkdir(self, path: VPath) -> None:
        self._assert_vpath(path)
        self._sftp_client.mkdir(path.path.as_posix())

    @override
    def copy_stat(self, path: VPath, src_stat: Stat) -> None:
        self._assert_vpath(path)
        p = path.path.as_posix()
        if src_stat.modified >= 0:
            self._sftp_client.utime(p, (src_stat.modified, src_stat.modified))
        if src_stat.mode >= 0:
            self._sftp_client.chmod(p, src_stat.mode)

    @override
    def readlink(self, path: VPath) -> str:
        self._assert_vpath(path)
        result = self._sftp_client.readlink(path.path.as_posix())
        if result is None:
            raise OSError(f"{path} is not a symbolic link")
        return result
