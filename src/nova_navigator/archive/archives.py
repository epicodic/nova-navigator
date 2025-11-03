from pathlib import PurePath
from typing import NamedTuple

from .archive import Archive
from .tar_archive import TarArchive
from .zip_archive import ZipArchive


class SupportedArchive(NamedTuple):
    archive_class: type[Archive]
    read_only: bool


_SUPPORTED_ARCHIVES: dict[str, SupportedArchive] = {
    ".tar": SupportedArchive(TarArchive, read_only=False),
    ".tar.gz": SupportedArchive(TarArchive, read_only=False),
    ".tgz": SupportedArchive(TarArchive, read_only=False),
    ".tar.bz2": SupportedArchive(TarArchive, read_only=False),
    ".tbz2": SupportedArchive(TarArchive, read_only=False),
    ".tar.xz": SupportedArchive(TarArchive, read_only=False),
    ".txz": SupportedArchive(TarArchive, read_only=False),
    ".zip": SupportedArchive(ZipArchive, read_only=False),
    ".jar": SupportedArchive(ZipArchive, read_only=True),
    ".war": SupportedArchive(ZipArchive, read_only=True),
    ".ear": SupportedArchive(ZipArchive, read_only=True),
    ".apk": SupportedArchive(ZipArchive, read_only=True),
    ".whl": SupportedArchive(ZipArchive, read_only=True),
}


def is_supported_archive(archive_path: str | PurePath) -> bool:
    """Check if the given path corresponds to a supported archive format."""
    archive_path = PurePath(archive_path)
    return any(archive_path.name.endswith(ext) for ext in _SUPPORTED_ARCHIVES)


def open_archive(archive_path: str | PurePath, mode: Archive.Mode) -> Archive:
    """Open an archive file and return the appropriate Archive subclass instance."""
    archive_path = PurePath(archive_path)

    for ext, archive in _SUPPORTED_ARCHIVES.items():
        if archive_path.name.endswith(ext):
            return archive.archive_class(archive_path, mode)
    raise ValueError(f"Unsupported archive format for file: {archive_path}")
