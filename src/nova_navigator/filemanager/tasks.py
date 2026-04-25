import asyncio
import contextlib
from dataclasses import dataclass
from typing import Literal

from nova_navigator.decision import Decision
from nova_navigator.scheduler import TaskContext

from ..vfs import VPath
from ..vfs.filesystem import Filesystem

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
) -> None:
    """Copy a single file from *src_path* to *dst_path*.

    Reads in CHUNK_SIZE chunks, updating step-level progress. When the
    destination already exists the action depends on ``options.overwrite``:
    ``"overwrite"`` replaces unconditionally, ``"skip"`` leaves it untouched,
    ``"ask"`` requests a user decision.
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
                if options.overwrite == "skip":
                    ctx.status.set_completed()
                    return
                elif options.overwrite == "ask":
                    decision = await ctx.request_decision(
                        "Overwrite",
                        expected_decisions=[Decision.YES, Decision.NO, Decision.ALL, Decision.NONE],
                        message=f"File '{dst_path.path}' already exists. Overwrite?",
                    )
                    if decision.is_negative:
                        ctx.status.set_completed()
                        return

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
    finally:
        if reader:
            reader.close()
        if writer:
            writer.close()


async def _copy_file_with_progress(
    ctx: TaskContext,
    src: VPath,
    dst: VPath,
    options: FileCopyOptions,
) -> None:
    await copy_file(ctx, src, dst, options)
    ctx.status.update_progress(inc_completed=1)


async def _copy_dir_recursive(
    ctx: TaskContext,
    src_path: VPath,
    dst_path: VPath,
    dst_filesystem: Filesystem,
    options: FileCopyOptions,
) -> None:
    file_tasks: list[asyncio.Task[None]] = []
    for src_root, _src_dirs, src_files in src_path.walk():
        ctx.status.check_cancelled()
        dst_root = dst_path / src_root.path.relative_to(src_path.path)
        with contextlib.suppress(FileExistsError):
            dst_filesystem.mkdir(dst_root)
        ctx.status.update_progress(inc_total=len(src_files))
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

    if len(src_paths) == 1 and not dst_is_directory:
        await copy_file(ctx, src_paths[0], destination, options)
        ctx.status.update_progress(inc_completed=1)
        return

    subtasks: list[asyncio.Task[None]] = []
    for src_path in src_paths:
        ctx.status.check_cancelled()
        dst_path = destination / src_path.name
        if src_path.stat.is_directory:
            t = await ctx.subtask(_copy_dir_recursive(ctx, src_path, dst_path, dst_filesystem, options))
        else:
            t = await ctx.subtask(_copy_file_with_progress(ctx, src_path, dst_path, options))
        subtasks.append(t)

    await asyncio.gather(*subtasks)


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
        if actual_dst_stat is not None:
            if options.overwrite == "skip":
                ctx.status.update_progress(inc_completed=1)
                return
            if options.overwrite == "ask":
                decision = await ctx.request_decision(
                    "Overwrite",
                    expected_decisions=[Decision.YES, Decision.NO, Decision.ALL, Decision.NONE],
                    message=f"'{actual_dst.path}' already exists. Overwrite?",
                )
                if decision.is_negative:
                    ctx.status.update_progress(inc_completed=1)
                    return
            if actual_dst_stat.is_directory:
                await erase_files(ctx, [actual_dst], EraseFilesOptions(ask_before_erase=False))
            else:
                actual_dst.filesystem.remove(actual_dst)
        src_path.filesystem.rename(src_path, actual_dst)
    else:
        if src_path.stat.is_directory:
            with contextlib.suppress(FileExistsError):
                actual_dst.filesystem.mkdir(actual_dst)
            for src_root, _src_dirs, src_files in src_path.walk():
                ctx.status.check_cancelled()
                dst_root = actual_dst / src_root.path.relative_to(src_path.path)
                with contextlib.suppress(FileExistsError):
                    actual_dst.filesystem.mkdir(dst_root)
                for f in src_files:
                    ctx.status.check_cancelled()
                    await copy_file(ctx, f, dst_root / f.name, options)
            await erase_files(ctx, [src_path], EraseFilesOptions(ask_before_erase=False))
        else:
            await copy_file(ctx, src_path, actual_dst, options)
            src_path.filesystem.remove(src_path)

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
    if path.stat.is_directory:
        if len(path.iterdir()) > 0 and options.ask_before_erase:
            decision = await ctx.request_decision(
                "Delete non-empty directory",
                expected_decisions=[Decision.YES, Decision.NO, Decision.ALL, Decision.NONE],
                message=f"Directory '{path.path}' is not empty. Delete it recursively?",
            )
            if decision.is_negative:
                ctx.status.update_progress(inc_completed=1)
                return
        await erase_files(ctx, list(path.iterdir()), EraseFilesOptions(ask_before_erase=False))
        path.filesystem.rmdir(path)
    else:
        path.filesystem.remove(path)
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
