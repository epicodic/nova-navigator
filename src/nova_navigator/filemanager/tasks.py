import contextlib
import logging
from dataclasses import dataclass
from typing import Literal

from nova_navigator.decision import Decision
from nova_navigator.task import DecisionRequest, Task, TaskStatus

from ..vfs2 import VPath

CHUNK_SIZE = 64 * 1024  # 64 KB


OverwritePolicy = Literal["overwrite", "skip", "ask"]

_logger = logging.getLogger(__name__)


@dataclass
class FileCopyOptions:
    """Options that control the behaviour of file copy operations."""

    overwrite: OverwritePolicy = "ask"


def copy_file(status: TaskStatus, src_path: VPath, dst_path: VPath, options: FileCopyOptions | None = None) -> Task:
    """Task that copies a single file from *src_path* to *dst_path*.

    Reads the source in :data:`CHUNK_SIZE` chunks and writes them to the
    destination, updating step-level progress as bytes are transferred.  When
    the destination already exists the action taken depends on
    ``options.overwrite``: ``"overwrite"`` replaces it unconditionally,
    ``"skip"`` leaves it untouched, and ``"ask"`` yields a
    :class:`~nova_navigator.task.DecisionRequest` to prompt the user.
    """
    if options is None:
        options = FileCopyOptions()

    reader = None
    writer = None
    try:
        src_stat = src_path.stat
        reader = src_path.filesystem.read(src_path)

        if options.overwrite != "overwrite":
            dst_stat = dst_path.stat_or_none
            if dst_stat is not None:
                # destination file exists
                if options.overwrite == "skip":
                    status.set_completed()
                    return
                elif options.overwrite == "ask":
                    decision = yield DecisionRequest(
                        "Overwrite",
                        expected_decisions=[Decision.YES, Decision.NO, Decision.ALL, Decision.NONE],
                        message=f"File '{dst_path.path}' already exists. Overwrite?",
                    )
                    if decision.is_negative:
                        status.set_completed()
                        return

        writer = dst_path.filesystem.write(dst_path)

        status.set_step_progress(0, src_stat.size)

        while True:
            status.check_cancelled()
            chunk = reader.read(CHUNK_SIZE)
            if not chunk:
                break
            writer.write(chunk)
            status.update_step_progress(inc_completed=len(chunk))

        status.set_step_completed()
    finally:
        if reader:
            reader.close()
        if writer:
            writer.close()


def copy_files(
    status: TaskStatus, src_paths: list[VPath], destination: VPath, options: FileCopyOptions | None = None
) -> Task:
    """Task that copies each path in *src_paths* into *destination*.

    If copying a single file and *destination* is not an existing directory,
    *destination* is used as the target filename. Otherwise, files are placed
    inside *destination* under their original name. Directories are copied
    recursively, preserving the relative sub-directory structure.
    Overall progress is incremented by one for each element of *src_paths* regardless of whether
    it is a file or directory.  *options* is forwarded to every
    :func:`copy_file` call.
    """
    dst_filesystem = destination.filesystem
    status.update_progress(inc_total=len(src_paths))

    # Check if destination is an existing directory
    dst_stat = destination.stat_or_none
    dst_is_directory = dst_stat is not None and dst_stat.is_directory

    # Special case: copying a single file to a non-directory destination. Use destination as target filename.
    if len(src_paths) == 1 and not dst_is_directory:
        yield copy_file(status, src_paths[0], destination, options)
        status.update_progress(inc_completed=1)
        return

    for src_path in src_paths:
        status.check_cancelled()
        dst_path = destination / src_path.name
        if src_path.stat.is_directory:
            for src_root, _src_dirs, src_files in src_path.walk():
                status.check_cancelled()
                dst_root = dst_path / src_root.path.relative_to(src_path.path)
                with contextlib.suppress(FileExistsError):
                    dst_filesystem.mkdir(dst_root)
                status.update_progress(inc_total=len(src_files))
                for f in src_files:
                    status.check_cancelled()
                    yield copy_file(status, f, dst_root / f.name, options)
                    status.update_progress(inc_completed=1)
        else:
            yield copy_file(status, src_path, dst_path, options)
        status.update_progress(inc_completed=1)


def move_files(
    status: TaskStatus, src_paths: list[VPath], dst_path: VPath, options: FileCopyOptions | None = None
) -> Task:
    """Task that moves each path in *src_paths* into *destination*.

    * if a source and destination are on the same device, the source is renamed to the destination path,
      otherwise the source is copied to the destination and then removed from the source.
    * if destination already exists and is a non-empty directory, the source is moved inside the destination
      directory, leaving the rest of the destination directory intact.
    * if destination exists already, the user is prompted to confirm the overwrite,
      unless ``options.overwrite`` is set to ``"overwrite"`` or ``"skip"``.
    """
    if options is None:
        options = FileCopyOptions()

    status.update_progress(inc_total=len(src_paths))

    for src_path in src_paths:
        status.check_cancelled()

        # Determine the actual destination path
        # If dst_path exists and is a directory, move source inside it
        dst_stat = dst_path.stat_or_none
        if dst_stat is not None and dst_stat.is_directory:
            actual_dst = dst_path / src_path.name
        else:
            actual_dst = dst_path

        # Check if source and destination are on the same device
        same_device = src_path.filesystem.is_same_device(src_path, actual_dst)

        if same_device:
            # Same device: use rename for atomic move

            # Check if destination exists and handle overwrite policy
            actual_dst_stat = actual_dst.stat_or_none
            if actual_dst_stat is not None:
                if options.overwrite == "skip":
                    status.update_progress(inc_completed=1)
                    continue
                elif options.overwrite == "ask":
                    decision = yield DecisionRequest(
                        "Overwrite",
                        expected_decisions=[Decision.YES, Decision.NO, Decision.ALL, Decision.NONE],
                        message=f"'{actual_dst.path}' already exists. Overwrite?",
                    )
                    if decision.is_negative:
                        status.update_progress(inc_completed=1)
                        continue
                # If overwrite == "overwrite", remove the destination before rename
                # Remove the existing destination (directory or file)
                if actual_dst_stat.is_directory:
                    # Remove directory recursively
                    yield from erase_files(status, [actual_dst], EraseFilesOptions(ask_before_erase=False))
                else:
                    # Remove file
                    actual_dst.filesystem.remove(actual_dst)

            # Perform the rename/move
            src_path.filesystem.rename(src_path, actual_dst)

        else:
            # Different devices: copy then remove

            # Copy the file or directory to destination
            if src_path.stat.is_directory:
                # For directories, we need to copy recursively
                # Create the destination directory
                with contextlib.suppress(FileExistsError):
                    actual_dst.filesystem.mkdir(actual_dst)

                # Copy all contents
                for src_root, _src_dirs, src_files in src_path.walk():
                    status.check_cancelled()
                    dst_root = actual_dst / src_root.path.relative_to(src_path.path)
                    with contextlib.suppress(FileExistsError):
                        actual_dst.filesystem.mkdir(dst_root)
                    for f in src_files:
                        status.check_cancelled()
                        yield from copy_file(status, f, dst_root / f.name, options)

                # Remove the source directory recursively
                yield from erase_files(status, [src_path], EraseFilesOptions(ask_before_erase=False))
            else:
                # For files, copy then remove
                yield from copy_file(status, src_path, actual_dst, options)
                src_path.filesystem.remove(src_path)

        status.update_progress(inc_completed=1)


@dataclass
class EraseFilesOptions:
    """Options that control the behaviour of file erase operations."""

    ask_before_erase: bool = True


def erase_files(status: TaskStatus, paths: list[VPath], options: EraseFilesOptions | None = None) -> Task:
    """Task that erases each path in *paths*.

    Directories are removed recursively.  Progress is incremented by one for
    each element of *paths* regardless of whether it is a file or directory.
    """
    if options is None:
        options = EraseFilesOptions()

    _logger.warning("erase_files: options=%s", options)

    status.update_progress(inc_total=len(paths))
    for path in paths:
        status.check_cancelled()
        if path.stat.is_directory:
            if len(path.iterdir()) > 0 and options.ask_before_erase:
                decision = yield DecisionRequest(
                    "Delete non-empty directory",
                    expected_decisions=[Decision.YES, Decision.NO, Decision.ALL, Decision.NONE],
                    message=f"Directory '{path.path}' is not empty. Delete it recursively?",
                )
                if decision.is_negative:
                    continue

            yield from erase_files(status, list(path.iterdir()), EraseFilesOptions(ask_before_erase=False))
            path.filesystem.rmdir(path)

        else:
            path.filesystem.remove(path)

        status.update_progress(inc_completed=1)
