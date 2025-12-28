import zipfile
from pathlib import PurePath
from typing import override

from .archive import Archive, Stat


class ZipArchive(Archive):
    """A class providing an abstraction for ZIP archive files."""

    _zip_file: zipfile.ZipFile
    _members: list[zipfile.ZipInfo]

    def __init__(self, archive_path: PurePath, mode: Archive.Mode) -> None:
        super().__init__(archive_path, mode)
        self._zip_file = zipfile.ZipFile(archive_path, mode=mode)
        self._members = self._zip_file.infolist()

    # TODO: implement faster lookup for members and directory listing

    def _find_member(self, path: PurePath) -> zipfile.ZipInfo | None:
        normalized_path = path.as_posix().lstrip("/")

        for member in self._members:
            normalized_filename = member.filename.rstrip("/")
            if normalized_filename == normalized_path:
                return member

        return None

    @override
    def listdir(self, path: PurePath) -> list[PurePath]:
        normalized_path = path.as_posix().lstrip("/")

        contents = set()
        for member in self._members:
            member_path = member.filename
            if not member_path.startswith(normalized_path):
                continue

            relative_path = member_path[len(normalized_path) :].lstrip("/")
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
            raise FileNotFoundError(f"Path '{path}', {path.name} not found in archive '{self._archive_path}'")

        return Stat(
            size=member.file_size,
            modified=0.0,  # member.date_time,
            is_hidden=member.filename.startswith("."),
            is_directory=member.is_dir(),
            is_executable=member.external_attr & 0o111 != 0,
            is_symlink=False,  # ZIP format does not support symlinks
        )
