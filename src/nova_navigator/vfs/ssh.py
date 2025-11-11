from __future__ import annotations

from dataclasses import dataclass
from functools import lru_cache
from typing import override

import paramiko

from .filesystem import Filesystem, PathStats, VPath


@dataclass
class StatEntry:
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
    _ssh_client: paramiko.SSHClient
    _sftp_client: paramiko.SFTPClient

    def __init__(self, ssh_client: paramiko.SSHClient) -> None:
        self._ssh_client = ssh_client
        self._sftp_client = ssh_client.open_sftp()

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
    def stat(self, path: VPath) -> PathStats:
        if path.path.parent == path.path:
            return PathStats(is_directory=True)

        file_list_stat = self._dir_stat(str(path.parent), follow_symlinks=True)
        file_list_lstat = self._dir_stat(str(path.parent), follow_symlinks=False)

        stat = file_list_stat.get(path.path.name)
        lstat = file_list_lstat.get(path.path.name)
        if stat is None or lstat is None:
            return PathStats()

        is_hidden = path.name.startswith(".")

        return PathStats(
            size=stat.size or 0,
            modified=stat.modified_time or 0,
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

    def __eq__(self, value: object) -> bool:
        return isinstance(value, SSHFilesystem) and self._ssh_client == value._ssh_client

    def __hash__(self) -> int:
        return hash(self._ssh_client)
