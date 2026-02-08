import asyncio
import contextlib
import logging
from dataclasses import dataclass
from typing import Literal

from nova_navigator.decision import Decision
from nova_navigator.scheduler import TaskContext

from ..vfs import VPath
from ..vfs.filesystem import Filesystem

_logger = logging.getLogger(__name__)

CHUNK_SIZE = 64 * 1024  # 64 KB

OverwritePolicy = Literal["overwrite", "skip", "ask"]


@dataclass
class FileCopyOptions:
    """Options that control the behaviour of file copy operations."""

    overwrite: OverwritePolicy = "ask"


@dataclass
class EraseFilesOptions:
    """Options that control the behaviour of file erase operations."""

    ask_before_erase: bool = True


async def copy_file(
    ctx: TaskContext,
    src_path: VPath,
    dst_path: VPath,
    options: FileCopyOptions | None = None,
) -> bool:
    """Copy a single file from *src_path* to *dst_path*.

    Reads in CHUNK_SIZE chunks, updating step-level progress. When the
    destination already exists the action depends on ``options.overwrite``:
    ``"overwrite"`` replaces unconditionally, ``"skip"`` leaves it untouched,
    ``"ask"`` requests a user decision.

    Returns ``True`` if the file was actually written, ``False`` if the copy
    was skipped (destination exists and overwrite policy did not proceed).
    """
    if options is None:
        options = FileCopyOptions()

    _logger.debug("copy_file %s -> %s", src_path.path, dst_path.path)
    reader = None
    writer = None
    _failed = False
    try:
        src_stat = src_path.stat
        reader = src_path.filesystem.read(src_path)

        if options.overwrite != "overwrite":
            dst_stat = dst_path.stat_or_none
            if dst_stat is not None:
                if options.overwrite == "skip":
                    _logger.debug("copy_file skip (exists) %s", dst_path.path)
                    return False
                elif options.overwrite == "ask":
                    decision = await ctx.request_decision(
                        "Overwrite",
                        expected_decisions=[Decision.YES, Decision.NO, Decision.ALL, Decision.NONE],
                        message=f"File '{dst_path.path}' already exists. Overwrite?",
                    )
                    if decision.is_negative:
                        _logger.debug("copy_file skip (user declined) %s", dst_path.path)
                        return False

        writer = dst_path.filesystem.write(dst_path)
        ctx.status.set_step_progress(0, src_stat.size)

        while True:
            ctx.status.check_cancelled()
            chunk = reader.read(CHUNK_SIZE)
            if not chunk:
                break
            writer.write(chunk)
            ctx.status.update_step_progress(inc_completed=len(chunk))

        ctx.status.set_step_completed()
        _logger.debug("copy_file done %s -> %s", src_path.path, dst_path.path)
    except BaseException:
        _failed = True
        _logger.debug("copy_file failed %s -> %s", src_path.path, dst_path.path)
        raise
    finally:
        if reader:
            reader.close()
        if writer:
            writer.close()
            if _failed:
                with contextlib.suppress(Exception):
                    dst_path.filesystem.remove(dst_path)
    return True


async def _copy_file_step(
    ctx: TaskContext,
    src: VPath,
    dst: VPath,
    options: FileCopyOptions,
) -> None:
    await copy_file(ctx, src, dst, options)
    ctx.status.update_progress(inc_completed=1)


async def _copy_dir(
    ctx: TaskContext,
    src_path: VPath,
    dst_path: VPath,
    dst_filesystem: Filesystem,
    options: FileCopyOptions,
) -> None:
    file_tasks: list[asyncio.Task[bool]] = []
    for src_root, _src_dirs, src_files in src_path.walk():
        ctx.status.check_cancelled()
        dst_root = dst_path / src_root.path.relative_to(src_path.path)
        with contextlib.suppress(FileExistsError):
            dst_filesystem.mkdir(dst_root)
        for f in src_files:
            ctx.status.check_cancelled()
            t = await ctx.subtask(copy_file(ctx, f, dst_root / f.name, options))
            file_tasks.append(t)
    await asyncio.gather(*file_tasks)
    ctx.status.update_progress(inc_completed=1)


async def copy_files(
    ctx: TaskContext,
    src_paths: list[VPath],
    destination: VPath,
    options: FileCopyOptions | None = None,
) -> None:
    """Copy each path in *src_paths* into *destination*.

    Single-file-to-non-directory uses *destination* as the target filename.
    Otherwise each path is placed inside *destination* under its original name.
    Directories are copied recursively. File copies within a directory run
    concurrently; overall progress increments by one per element of *src_paths*.
    """
    if options is None:
        options = FileCopyOptions()

    dst_filesystem = destination.filesystem
    ctx.status.update_progress(inc_total=len(src_paths))

    dst_stat = destination.stat_or_none
    dst_is_directory = dst_stat is not None and dst_stat.is_directory

    if len(src_paths) == 1 and not dst_is_directory and not src_paths[0].stat.is_directory:
        await copy_file(ctx, src_paths[0], destination, options)
        ctx.status.update_progress(inc_completed=1)
        return

    if len(src_paths) == 1 and not dst_is_directory and src_paths[0].stat.is_directory:
        await _copy_dir(ctx, src_paths[0], destination, dst_filesystem, options)
        ctx.status.update_progress(inc_completed=1)
        return

    subtasks: list[asyncio.Task[None]] = []
    for src_path in src_paths:
        ctx.status.check_cancelled()
        dst_path = destination / src_path.name
        if src_path.stat.is_directory:
            t = await ctx.subtask(_copy_dir(ctx, src_path, dst_path, dst_filesystem, options))
        else:
            t = await ctx.subtask(_copy_file_step(ctx, src_path, dst_path, options))
        subtasks.append(t)

    await asyncio.gather(*subtasks)


async def _move_dir_contents(
    ctx: TaskContext,
    src_path: VPath,
    actual_dst: VPath,
    options: FileCopyOptions,
    *,
    same_device: bool,
) -> None:
    """Walk *src_path* and move each file into the mirrored path under *actual_dst*.

    Used when the destination directory already exists (so an atomic rename of
    the whole tree is not possible). Files are processed individually so that
    the overwrite policy is applied at file granularity. Source directories are
    removed bottom-up after their contents are moved.
    """
    with contextlib.suppress(FileExistsError):
        actual_dst.filesystem.mkdir(actual_dst)
    src_dirs: list[VPath] = []
    for src_root, _src_dirs, src_files in src_path.walk():
        ctx.status.check_cancelled()
        dst_root = actual_dst / src_root.path.relative_to(src_path.path)
        with contextlib.suppress(FileExistsError):
            actual_dst.filesystem.mkdir(dst_root)
        src_dirs.append(src_root)
        for f in src_files:
            ctx.status.check_cancelled()
            f_dst = dst_root / f.name
            if same_device:
                f_dst_stat = f_dst.stat_or_none
                if f_dst_stat is not None:
                    if options.overwrite == "skip":
                        continue
                    if options.overwrite == "ask":
                        decision = await ctx.request_decision(
                            "Overwrite",
                            expected_decisions=[Decision.YES, Decision.NO, Decision.ALL, Decision.NONE],
                            message=f"File '{f_dst.path}' already exists. Overwrite?",
                        )
                        if decision.is_negative:
                            continue
                    f_dst.filesystem.remove(f_dst)
                f.filesystem.rename(f, f_dst)
            else:
                copied = await copy_file(ctx, f, f_dst, options)
                if copied:
                    f.filesystem.remove(f)
    for d in reversed(src_dirs):
        with contextlib.suppress(OSError):
            d.filesystem.rmdir(d)


async def _move_path(
    ctx: TaskContext,
    src_path: VPath,
    dst_path: VPath,
    options: FileCopyOptions,
) -> None:
    """Move a single *src_path* to or into *dst_path*.

    If *dst_path* is an existing directory the source is placed inside it under
    its original name; otherwise it is used as the exact destination.
    Same-device moves use an atomic rename after clearing any existing
    destination. Cross-device moves copy the content then remove the source.
    Increments the overall completed counter by one when done (including skip).
    """
    ctx.status.check_cancelled()

    dst_stat = dst_path.stat_or_none
    actual_dst = dst_path / src_path.name if (dst_stat is not None and dst_stat.is_directory) else dst_path

    same_device = src_path.filesystem.is_same_device(src_path, actual_dst)

    if same_device:
        actual_dst_stat = actual_dst.stat_or_none
        if src_path.stat.is_directory and actual_dst_stat is not None and actual_dst_stat.is_directory:
            # Destination directory already exists: walk per-file for fine-grained overwrite policy.
            await _move_dir_contents(ctx, src_path, actual_dst, options, same_device=True)
        else:
            if actual_dst_stat is not None:
                if options.overwrite == "skip":
                    _logger.debug("move_path skip (exists) %s", actual_dst.path)
                    ctx.status.update_progress(inc_completed=1)
                    return
                if options.overwrite == "ask":
                    decision = await ctx.request_decision(
                        "Overwrite",
                        expected_decisions=[Decision.YES, Decision.NO, Decision.ALL, Decision.NONE],
                        message=f"'{actual_dst.path}' already exists. Overwrite?",
                    )
                    if decision.is_negative:
                        _logger.debug("move_path skip (user declined) %s", actual_dst.path)
                        ctx.status.update_progress(inc_completed=1)
                        return
                if actual_dst_stat.is_directory:
                    await erase_files(ctx, [actual_dst], EraseFilesOptions(ask_before_erase=False))
                else:
                    actual_dst.filesystem.remove(actual_dst)
            src_path.filesystem.rename(src_path, actual_dst)
            _logger.debug("move_path renamed %s -> %s", src_path.path, actual_dst.path)
    else:
        if src_path.stat.is_directory:
            await _move_dir_contents(ctx, src_path, actual_dst, options, same_device=False)
        else:
            copied = await copy_file(ctx, src_path, actual_dst, options)
            if copied:
                src_path.filesystem.remove(src_path)

    _logger.debug("move_path done %s -> %s", src_path.path, actual_dst.path)
    ctx.status.update_progress(inc_completed=1)


async def move_files(
    ctx: TaskContext,
    src_paths: list[VPath],
    dst_path: VPath,
    options: FileCopyOptions | None = None,
) -> None:
    """Move each path in *src_paths* into *dst_path*.

    Same-device moves use rename (atomic). Cross-device moves copy then remove.
    Each path is processed as a concurrent subtask so that a user decision
    blocking one item does not stall the others.
    """
    if options is None:
        options = FileCopyOptions()

    ctx.status.update_progress(inc_total=len(src_paths))
    subtasks: list[asyncio.Task[None]] = []
    for src_path in src_paths:
        ctx.status.check_cancelled()
        t = await ctx.subtask(_move_path(ctx, src_path, dst_path, options))
        subtasks.append(t)
    await asyncio.gather(*subtasks)


async def _erase_path(
    ctx: TaskContext,
    path: VPath,
    options: EraseFilesOptions,
) -> None:
    """Erase a single *path*, prompting the user if it is a non-empty directory.

    Files are removed directly. Directories are erased recursively via
    :func:`erase_files`; if non-empty and *options.ask_before_erase* is set,
    the user is asked to confirm before deletion proceeds. Increments the
    overall completed counter by one when done (including when skipped).
    """
    ctx.status.check_cancelled()
    _logger.debug("erase_path %s", path.path)
    if path.stat.is_directory:
        if len(path.iterdir()) > 0 and options.ask_before_erase:
            decision = await ctx.request_decision(
                "Delete non-empty directory",
                expected_decisions=[Decision.YES, Decision.NO, Decision.ALL, Decision.NONE],
                message=f"Directory '{path.path}' is not empty. Delete it recursively?",
            )
            if decision.is_negative:
                _logger.debug("erase_path skip (user declined) %s", path.path)
                ctx.status.update_progress(inc_completed=1)
                return
        await erase_files(ctx, list(path.iterdir()), EraseFilesOptions(ask_before_erase=False))
        path.filesystem.rmdir(path)
    else:
        path.filesystem.remove(path)
    _logger.debug("erase_path done %s", path.path)
    ctx.status.update_progress(inc_completed=1)


async def erase_files(
    ctx: TaskContext,
    paths: list[VPath],
    options: EraseFilesOptions | None = None,
) -> None:
    """Erase each path in *paths*; directories are removed recursively.

    Each path is processed as a concurrent subtask so that a user decision
    blocking one item does not stall the others.
    """
    if options is None:
        options = EraseFilesOptions()

    ctx.status.update_progress(inc_total=len(paths))
    subtasks: list[asyncio.Task[None]] = []
    for path in paths:
        ctx.status.check_cancelled()
        t = await ctx.subtask(_erase_path(ctx, path, options))
        subtasks.append(t)
    await asyncio.gather(*subtasks)
