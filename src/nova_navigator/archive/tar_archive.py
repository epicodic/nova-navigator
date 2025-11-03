import tarfile
from pathlib import PurePath
from typing import override

from .archive import Archive, PathStats


class TarArchive(Archive):
    """A class providing an abstraction for TAR archive files."""

    _tar_file: tarfile.TarFile
    _members: list[tarfile.TarInfo]

    def __init__(self, archive_path: PurePath, mode: Archive.Mode) -> None:
        super().__init__(archive_path, mode)
        self._tar_file = tarfile.open(name=archive_path, mode=mode + ":*")  # type: ignore # noqa: SIM115
        self._members = self._tar_file.getmembers()

    # TODO: implement faster lookup for members and directory listing

    def _find_member(self, path: PurePath) -> tarfile.TarInfo | None:
        normalized_path = path.as_posix().lstrip("/")

        for member in self._members:
            if member.name == normalized_path:
                return member

        return None

    @override
    def listdir(self, path: PurePath) -> list[PurePath]:
        normalized_path = path.as_posix().lstrip("/")

        contents = set()
        for member in self._members:
            member_path = member.name
            if not member_path.startswith(normalized_path):
                continue

            relative_path = member_path[len(normalized_path) :].lstrip("/")
            parts = relative_path.split("/", 1)
            if parts[0]:
                contents.add(parts[0])

        return [PurePath(part) for part in contents]

    @override
    def stats(self, path: PurePath) -> PathStats:
        if path == path.parent:
            # Root directory of the archive
            return PathStats(
                size=0,
                modified=0,
                is_hidden=False,
                is_directory=True,
                is_executable=False,
                is_symlink=False,
            )

        member = self._find_member(path)
        if member is None:
            raise FileNotFoundError(f"Path '{path}', {path.name} not found in archive '{self._archive_path}'")

        return PathStats(
            size=member.size,
            modified=member.mtime,
            is_hidden=member.name.startswith("."),
            is_directory=member.isdir(),
            is_executable=member.mode & 0o111 != 0,
            is_symlink=member.issym() or member.islnk(),
        )
