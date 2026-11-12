import errno
import tarfile
from _thread import LockType
from dataclasses import replace
from pathlib import PurePath, PurePosixPath
from threading import Lock
from typing import override

from ..vfs.filesystem import StreamReaderLike
from .archive import Archive, Stat, _ArchiveReader


def _normalize_member_name(name: str) -> str:
    """Strip a leading './' as produced by e.g. `tar -C dir -cf x.tar .`; '.' or './' is the archive root."""
    if name in (".", "./"):
        return ""
    return name.removeprefix("./")


_MAX_HARD_LINK_DEPTH = 16
_MAX_SYMLINK_HOPS = 40


class TarArchive(Archive):
    """A class providing an abstraction for TAR archive files."""

    _tar_file: tarfile.TarFile
    _members: list[tarfile.TarInfo]
    _read_lock: LockType

    def __init__(self, archive_path: PurePath, mode: Archive.Mode) -> None:
        super().__init__(archive_path, mode)
        self._tar_file = tarfile.open(name=archive_path, mode=mode + ":*")  # type: ignore # noqa: SIM115
        self._members = self._tar_file.getmembers()
        self._read_lock = Lock()

    # TODO: implement faster lookup for members and directory listing

    def _find_member(self, path: PurePath) -> tarfile.TarInfo | None:
        normalized_path = path.as_posix().lstrip("/")

        for member in self._members:
            if _normalize_member_name(member.name) == normalized_path:
                return member

        return None

    @override
    def listdir(self, path: PurePath) -> list[PurePath]:
        normalized_path = path.as_posix().lstrip("/")
        prefix = normalized_path + "/" if normalized_path else ""

        contents = set()
        for member in self._members:
            member_path = _normalize_member_name(member.name)
            if not member_path.startswith(prefix):
                continue

            relative_path = member_path[len(prefix) :]
            parts = relative_path.split("/", 1)
            if parts[0]:
                contents.add(parts[0])

        return [PurePath(part) for part in contents]

    @override
    def stats(self, path: PurePath) -> Stat:
        if path == path.parent:
            # Root directory of the archive
            return Stat(
                size=0,
                modified=0,
                is_hidden=False,
                is_directory=True,
                is_executable=False,
                is_symlink=False,
            )

        member = self._find_member(path)
        if member is None:
            if self.listdir(path):
                return Stat(
                    size=0,
                    modified=0,
                    is_hidden=path.name.startswith("."),
                    is_directory=True,
                    is_executable=False,
                    is_symlink=False,
                )
            raise FileNotFoundError(f"Path '{path}', {path.name} not found in archive '{self._archive_path}'")

        if member.issym():
            return self._symlink_stats(path, member)

        size = member.size
        is_executable = member.mode & 0o111 != 0
        if member.islnk():
            resolved = self._resolve_hard_link(member)
            if resolved is not None:
                size = resolved.size
                is_executable = resolved.mode & 0o111 != 0
        return Stat(
            size=size,
            modified=member.mtime,
            is_hidden=path.name.startswith("."),
            is_directory=member.isdir(),
            is_executable=is_executable,
            is_symlink=False,
        )

    def _symlink_stats(self, path: PurePath, member: tarfile.TarInfo) -> Stat:
        """Stat of a symlink member: the followed target's attributes with ``is_symlink`` set (like the local backend)."""
        is_hidden = path.name.startswith(".")
        try:
            resolved = self._resolve_path(path)
        except FileNotFoundError:
            return Stat(size=0, modified=member.mtime, is_hidden=is_hidden, is_symlink=True, is_broken_symlink=True)
        target = self.stats(PurePosixPath("/") / resolved)
        return replace(target, is_hidden=is_hidden, is_symlink=True)

    def _resolve_path(self, path: PurePath) -> PurePosixPath:
        """Return *path* relative to the archive root with every symlink member resolved against the archive's members.

        Raises ``FileNotFoundError`` for a missing target, an absolute target, a target escaping the archive root or a loop.
        """
        pending = [part for part in path.as_posix().split("/") if part not in ("", ".")]
        current = PurePosixPath()
        hops = 0
        while pending:
            part = pending.pop(0)
            if part == "..":
                if not current.parts:
                    raise FileNotFoundError(f"Link in '{path}' escapes the archive")
                current = current.parent
                continue
            candidate = current / part
            member = self._find_member(candidate)
            if member is not None and member.issym():
                hops += 1
                if hops > _MAX_SYMLINK_HOPS:
                    raise FileNotFoundError(f"Too many levels of symbolic links in '{path}'")
                if member.linkname.startswith("/"):
                    raise FileNotFoundError(f"Absolute link target outside the archive: '{member.linkname}'")
                pending = [p for p in member.linkname.split("/") if p not in ("", ".")] + pending
                continue
            current = candidate
        if current.parts and self._find_member(current) is None and not self.listdir(current):
            raise FileNotFoundError(f"Path '{path}' not found in archive '{self._archive_path}'")
        return current

    @override
    def readlink(self, path: PurePath) -> str:
        member = self._find_member(path)
        if member is None or not member.issym():
            raise OSError(errno.EINVAL, "Not a symbolic link", str(path))
        return member.linkname

    @override
    def real_path(self, path: PurePath) -> PurePath | None:
        return PurePosixPath("/") / self._resolve_path(path)

    def _resolve_hard_link(self, member: tarfile.TarInfo) -> tarfile.TarInfo | None:
        """Follow hard-link members to the regular member holding the data.

        Returns ``None`` if the chain is unresolved (dangling) or loops.
        """
        current = member
        for _ in range(_MAX_HARD_LINK_DEPTH):
            if not current.islnk():
                return current if current.isreg() else None
            target = self._find_member(PurePath(_normalize_member_name(current.linkname)))
            if target is None:
                return None
            current = target
        return None

    @override
    def read(self, path: PurePath) -> StreamReaderLike:
        if path == path.parent:
            raise IsADirectoryError(path)
        member = self._find_member(path)
        if member is None:
            if self.listdir(path):
                raise IsADirectoryError(path)
            raise FileNotFoundError(path)
        if member.issym():
            return self.read(PurePosixPath("/") / self._resolve_path(path))
        if member.isdir():
            raise IsADirectoryError(path)
        if member.islnk():
            resolved = self._resolve_hard_link(member)
            if resolved is not None:
                member = resolved
        try:
            with self._read_lock:
                stream = self._tar_file.extractfile(member)
        except KeyError as exc:
            raise FileNotFoundError(path) from exc
        if stream is None:
            raise OSError(f"Cannot read archive member '{path}'")
        return _ArchiveReader(stream, self._read_lock)

    @override
    def close(self) -> None:
        self._tar_file.close()
