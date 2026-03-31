from collections.abc import Generator
from dataclasses import dataclass
from typing import Literal

from nova_navigator.task import DecisionRequest, Task, TaskStatus

from ..vfs2 import VPath

CHUNK_SIZE = 64 * 1024  # 64 KB


def _iterate_files(status: TaskStatus, path: VPath) -> Generator[VPath, None, None]:
    if path.stat.is_directory:
        paths = path.iterdir()
        status.update_progress(inc_total=len(paths))
        for child in paths:
            status.check_cancelled()
            yield from _iterate_files(status, child)
            status.update_progress(inc_completed=1)
    else:
        yield path


OverwritePolicy = Literal["overwrite", "skip", "ask"]


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
                    decision = yield DecisionRequest("File '{dst}' already exists. Overwrite?", dst=dst_path.path)
                    if decision.is_no:
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
    status: TaskStatus, src_paths: list[VPath], dst_path: VPath, options: FileCopyOptions | None = None
) -> Task:
    """Task that copies each path in *src_paths* into *dst_path*.

    Plain files are placed directly inside *dst_path* under their original
    name.  Directories are copied recursively: all leaf files discovered by
    :func:`_iterate_files` are mirrored under ``dst_path / src_dir_name``,
    preserving the relative sub-directory structure.  Overall progress is
    incremented by one for each element of *src_paths* regardless of whether
    it is a file or directory.  *options* is forwarded to every
    :func:`copy_file` call.
    """
    status.update_progress(inc_total=len(src_paths))
    for src_path in src_paths:
        status.check_cancelled()
        if src_path.stat.is_directory:
            dst_dir = dst_path / src_path.name
            for file_path in _iterate_files(status, src_path):
                rel = file_path.path.relative_to(src_path.path)
                dst_file = dst_dir / rel
                yield from copy_file(status, file_path, dst_file, options)
        else:
            yield from copy_file(status, src_path, dst_path / src_path.name, options)
        status.update_progress(inc_completed=1)
