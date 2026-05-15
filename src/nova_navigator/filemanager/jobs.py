import logging

from nova_navigator.decision import Decision
from nova_navigator.dialogs import DeleteFilesDialog
from nova_navigator.vfs import VPath

from ..dialogs.files_dialog import CopyMoveFilesDialog
from ..scheduler import Job
from .tasks import copy_files, erase_files, move_files

_logger = logging.getLogger(__name__)


async def copy_or_move_files_job(src_paths: list[VPath], dst_path: VPath, move: bool = False) -> Job | None:
    """Create a Job that copies or moves files from *src_paths* to *dst_path*.

    The user will be prompted to confirm the operation if any destination file already exists.

    Args:
        src_paths (list[VFSPath]): List of source file paths.
        dst_path (VFSPath): Destination directory path.
        move (bool, optional): If True, move files instead of copying. Defaults to False.
    """
    dialog = CopyMoveFilesDialog(
        source_paths=src_paths,
        destination_path=dst_path,
        move=move,
    )

    result = await dialog.run()
    if result != Decision.OK:
        _logger.info("%s cancelled by user", "Move" if move else "Copy")
        return None

    # When copying/moving a single file, use the (possibly edited) filename.
    if len(src_paths) == 1 and dialog.filename is not None:
        dst_path = dst_path / dialog.filename

    verb = "Move" if move else "Copy"
    names = ", ".join(p.name for p in src_paths)
    _logger.info("%s %d item(s) [%s] -> %s", verb, len(src_paths), names, dst_path.path)
    if move:
        return Job("Move Files", move_files, src_paths, dst_path)
    return Job("Copy Files", copy_files, src_paths, dst_path)


async def delete_files_job(paths: list[VPath]) -> Job | None:
    """Create a Job that erases the specified files."""
    dialog = DeleteFilesDialog(paths=paths)
    result = await dialog.run()
    if result != Decision.YES:
        _logger.info("Delete cancelled by user")
        return None

    names = ", ".join(p.name for p in paths)
    _logger.info("Delete %d item(s) [%s]", len(paths), names)
    return Job("Erase Files", erase_files, paths)
