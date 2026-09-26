"""Open local and remote archives with explicit temporary-stage ownership."""

from collections.abc import Callable
from pathlib import Path
from tempfile import TemporaryDirectory

from nova_navigator.vfs.filesystems.archive import ArchiveFilesystem
from nova_navigator.vfs.filesystems.local import LocalFilesystem
from nova_navigator.vfs.vpath import VPath

from .transfer import mirror_file_with_version


def open_archive_mount(source: VPath, stage_dir: Path, should_cancel: Callable[[], bool]) -> ArchiveFilesystem:
    """Open an archive, streaming remote bytes into a private owned directory."""
    if should_cancel():
        raise InterruptedError("Archive opening cancelled")
    local = LocalFilesystem.singleton()
    if isinstance(source.filesystem, LocalFilesystem):
        return ArchiveFilesystem(source.parent, source, source=source)
    stage_dir.mkdir(mode=0o700, parents=True, exist_ok=True)
    stage = TemporaryDirectory(prefix="archive-", dir=stage_dir)
    try:
        destination = Path(stage.name) / source.name
        destination.touch(mode=0o600)
        _, _, source_version = mirror_file_with_version(source, destination, should_cancel)
        if should_cancel():
            raise InterruptedError("Archive opening cancelled")
        return ArchiveFilesystem(source.parent, local.path(destination), source=source, stage=stage, source_version=source_version)
    except BaseException:
        stage.cleanup()
        raise
