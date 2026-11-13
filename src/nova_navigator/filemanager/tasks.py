import asyncio
import contextlib
import logging
from dataclasses import dataclass, replace
from pathlib import PurePosixPath
from typing import Literal

from nova_navigator.scheduler import TaskContext
from nova_widgets import Response

from ..file_filter import FileFilter
from ..vfs import VPath
from ..vfs.filesystem import Filesystem
from ..vfs.types import Stat

_logger = logging.getLogger(__name__)


def _is_real_dir(stat: Stat) -> bool:
    """Return whether *stat* describes a directory that is not a symbolic link (link stat first)."""
    return stat.is_directory and not stat.is_symlink


SKIP_BROKEN_LINK = "broken link"
SKIP_LINK_LOOP = "link loop"
SKIP_UNREADABLE_LINK = "unreadable link"
_MAX_LINK_DEPTH = 16


@dataclass(frozen=True)
class _LinkChain:
    """Real paths of the directories that hold the links entered on the current descent (outermost first)."""

    positions: tuple[PurePosixPath, ...] = ()
    depth: int = 0


_NO_CHAIN = _LinkChain()


CHUNK_SIZE = 64 * 1024  # 64 KB

_DUMMY_ITEMS = 3
_DUMMY_ITEM_STEPS = 10
_DUMMY_FAIL_INTERVAL = 3 * _DUMMY_ITEMS - 2
_DUMMY_NEXT_FAIL = _DUMMY_FAIL_INTERVAL


async def dummy_task(ctx: TaskContext) -> None:
    """Simulate processing 10 items, each taking 10 seconds with per-second step updates."""
    global _DUMMY_NEXT_FAIL  # noqa: PLW0603
    ctx.status.set_progress(0, _DUMMY_ITEMS)
    for item in range(_DUMMY_ITEMS):
        ctx.status.check_cancelled()
        ctx.status.set_step_progress(0, _DUMMY_ITEM_STEPS)

        _DUMMY_NEXT_FAIL -= 1
        if _DUMMY_NEXT_FAIL == 0:
            _DUMMY_NEXT_FAIL = _DUMMY_FAIL_INTERVAL
            raise RuntimeError("simulated failure")

        for step in range(_DUMMY_ITEM_STEPS):
            ctx.status.check_cancelled()

            await asyncio.sleep(1)
            ctx.status.set_step_progress(step + 1, _DUMMY_ITEM_STEPS)
        ctx.status.set_progress(item + 1, _DUMMY_ITEMS)


OverwritePolicy = Literal["overwrite", "skip", "ask"]


@dataclass
class FileCopyOptions:
    """Options that control the behaviour of file copy operations."""

    overwrite: OverwritePolicy = "ask"
    filter: FileFilter | None = None


@dataclass
class EraseFilesOptions:
    """Options that control the behaviour of file erase operations."""

    ask_before_erase: bool = True
    refresh_stat: bool = True
    """Refresh the cached stat of each path before erasing; internal children listings turn it off."""


async def _overwrite_allowed(ctx: TaskContext, src: VPath, dst: VPath, options: FileCopyOptions, label: str) -> bool:
    """Apply the overwrite policy to *dst*; return True when the copy may proceed (*dst* absent or may be replaced)."""
    if options.overwrite == "overwrite":
        return True
    dst_stat = dst.stat_or_none
    if dst_stat is None:
        return True
    if options.overwrite == "skip":
        _logger.debug("%s skip (exists) %s", label, dst.path)
        return False
    if options.overwrite == "ask":
        response = await ctx.request_response(
            "Overwrite",
            expected_responses=[Response.YES, Response.NO, Response.ALL, Response.NONE],
            message=f"File '{dst.name}' already exists. Overwrite?",
            dialog_type="overwrite",
            details={
                "src_name": src.name,
                "src_size": src.stat.size,
                "dst_name": dst.name,
                "dst_size": dst_stat.size,
            },
        )
        if response.is_rejected:
            _logger.debug("%s skip (user declined) %s", label, dst.path)
            return False
    return True


DirOutcome = Literal["created", "merged", "skipped"]


def _fresh_stat_or_none(filesystem: Filesystem, path: VPath) -> Stat | None:
    """Return a fresh stat of *path* (dropping the cached parent listing first), or None if it does not exist."""
    filesystem.refresh(path.parent)
    try:
        return filesystem.stat(path)
    except FileNotFoundError:
        return None


async def _ensure_directory(ctx: TaskContext, src: VPath, dst: VPath, options: FileCopyOptions) -> DirOutcome:
    """Make *dst* a usable real directory for the folder *src*; one decision point for copy and both move paths (REQ-6, REQ-7).

    Missing: create it ("created").
    A real folder (checked link-first): reuse it without a question ("merged").
    A file or a link of any kind: apply the overwrite policy. "skipped" leaves the entry untouched;
    otherwise the entry itself is removed (never followed) and the folder is created.
    """
    filesystem = dst.filesystem
    stat = _fresh_stat_or_none(filesystem, dst)
    if stat is not None and _is_real_dir(stat):
        filesystem.refresh(dst)
        return "merged"
    if stat is not None:
        if not await _overwrite_allowed(ctx, src, dst.with_stat(stat), options, "ensure_directory"):
            return "skipped"
        filesystem.remove(dst)
    try:
        filesystem.mkdir(dst)
    except FileExistsError:
        filesystem.refresh(dst)
        return "merged"
    return "created"


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
    ``"ask"`` requests a user response.

    Returns ``True`` if the file was actually written, ``False`` if the copy
    was skipped (destination exists and overwrite policy did not proceed).
    """
    if options is None:
        options = FileCopyOptions()

    _logger.debug("copy_file %s -> %s", src_path.path, dst_path.path)
    ctx.status.set_current_item(src_path.uri)
    reader = None
    writer = None
    _failed = False
    try:
        src_stat = src_path.stat
        reader = src_path.filesystem.read(src_path)

        if not await _overwrite_allowed(ctx, src_path, dst_path, options, "copy_file"):
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
    dst_path.filesystem.copy_stat(dst_path, src_stat)
    return True


async def _copy_file_step(
    ctx: TaskContext,
    src: VPath,
    dst: VPath,
    options: FileCopyOptions,
) -> None:
    if options.filter is not None and not options.filter.matches(src):
        ctx.status.update_progress(inc_completed=1)
        return
    await copy_file(ctx, src, dst, options)
    ctx.status.update_progress(inc_completed=1)


async def _confirm_link_replace(ctx: TaskContext, link: VPath, dst: VPath, options: FileCopyOptions) -> bool:
    """Apply the overwrite policy to an existing *dst*; return True when the link may be created (*dst* is then removed)."""
    if dst.stat_or_none is None:
        return True
    if not await _overwrite_allowed(ctx, link, dst, options, "copy_link"):
        return False
    dst.filesystem.remove(dst)  # removes a file or link itself, never follows; a real directory makes it raise
    return True


async def _recreate_link(ctx: TaskContext, link: VPath, dst: VPath, options: FileCopyOptions) -> bool:
    """Create at *dst* a link with the same target text as *link*; never reads or descends into the target."""
    try:
        text = link.filesystem.readlink(link)
    except OSError:
        _logger.warning("Skipping link %s: its target cannot be read", link.path, exc_info=True)
        ctx.report_skipped(link.uri, SKIP_UNREADABLE_LINK)
        return False
    if not await _confirm_link_replace(ctx, link, dst, options):
        return False
    dst.filesystem.symlink(dst, text)
    return True


def _followed_view(link: VPath) -> VPath:
    """Return a VPath at *link*'s own path whose stat no longer says "link", so ``walk`` may enter it (no resolved-path API)."""
    return link.with_stat(replace(link.stat, is_symlink=False))


def _needs_holder(link: VPath, dst_filesystem: Filesystem) -> bool:
    """Return True if copying *link* needs the real position of the directory holding it (loop test)."""
    stat = link.stat
    return not dst_filesystem.supports_symlinks() and stat.is_directory and not stat.is_broken_symlink


class _HolderResolver:
    """Lazily resolves the real (canonical) position of directories below a walk root; ``None`` when the filesystem cannot tell."""

    def __init__(self, root: VPath) -> None:
        self._root = root
        self._real: PurePosixPath | None = None
        self._resolved = False

    def holder(self, walked: VPath) -> PurePosixPath | None:
        """Return the real position of *walked* (a directory at or below the walk root), or None if unknown."""
        if not self._resolved:
            self._resolved = True
            try:
                real = self._root.filesystem.real_path(self._root)
            except OSError:
                real = None
            self._real = None if real is None else real.path
        if self._real is None:
            return None
        return self._real / walked.path.relative_to(self._root.path)


def _is_loop(target: PurePosixPath, holder: PurePosixPath | None, chain: _LinkChain) -> bool:
    """Return True if *target* is a directory already being copied: ancestor-or-self of the holder or of a chain position."""
    positions = chain.positions if holder is None else (*chain.positions, holder)
    return any(position == target or position.is_relative_to(target) for position in positions)


async def _copy_linked_directory(
    ctx: TaskContext,
    link: VPath,
    dst: VPath,
    options: FileCopyOptions,
    chain: _LinkChain,
    holder: PurePosixPath | None,
) -> bool:
    """Copy the directory *link* points to as a tree under *dst* (destination without links), unless it loops."""
    if chain.depth + 1 > _MAX_LINK_DEPTH:
        ctx.report_skipped(link.uri, SKIP_LINK_LOOP)
        return False
    try:
        real = link.filesystem.real_path(link)
    except OSError:
        ctx.report_skipped(link.uri, SKIP_BROKEN_LINK)
        return False
    if real is None:
        source = _followed_view(link)
        next_chain = _LinkChain(chain.positions, chain.depth + 1)
    else:
        if _is_loop(real.path, holder, chain):
            ctx.report_skipped(link.uri, SKIP_LINK_LOOP)
            return False
        source = real
        positions = chain.positions if holder is None else (*chain.positions, holder)
        next_chain = _LinkChain(positions, chain.depth + 1)
    return await _copy_dir(ctx, source, dst, dst.filesystem, options, next_chain, count_root=False)


async def _copy_link(
    ctx: TaskContext,
    link: VPath,
    dst: VPath,
    options: FileCopyOptions,
    chain: _LinkChain = _NO_CHAIN,
    holder: PurePosixPath | None = None,
) -> bool:
    """Copy the symbolic link *link* to *dst*; return True if something was created at the destination.

    One decision point for copy and for cross-device move (REQ-3, REQ-9).
    A link-capable destination gets a link with the same target text.
    Never touches the ``completed`` counter: the caller counts the link as one unit of work.
    """
    ctx.status.set_current_item(link.uri)
    if dst.filesystem.supports_symlinks():
        return await _recreate_link(ctx, link, dst, options)
    stat = link.stat
    if stat.is_broken_symlink:
        ctx.report_skipped(link.uri, SKIP_BROKEN_LINK)
        return False
    if not stat.is_directory:
        return await copy_file(ctx, link, dst, options)
    return await _copy_linked_directory(ctx, link, dst, options, chain, holder)


async def _copy_link_step(
    ctx: TaskContext,
    link: VPath,
    dst: VPath,
    options: FileCopyOptions,
    chain: _LinkChain = _NO_CHAIN,
    holder: PurePosixPath | None = None,
) -> None:
    if options.filter is not None and not options.filter.matches(link):
        ctx.status.update_progress(inc_completed=1)
        return
    await _copy_link(ctx, link, dst, options, chain, holder)
    ctx.status.update_progress(inc_completed=1)


async def _copy_leaf_step(ctx: TaskContext, src: VPath, dst: VPath, options: FileCopyOptions) -> None:
    """Copy a top-level item that is not a real directory: a link recreates/copies as a link, anything else as a file."""
    if src.stat.is_symlink:
        await _copy_link_step(ctx, src, dst, options)
    else:
        await _copy_file_step(ctx, src, dst, options)


async def _copy_dir(
    ctx: TaskContext,
    src_path: VPath,
    dst_path: VPath,
    dst_filesystem: Filesystem,
    options: FileCopyOptions,
    chain: _LinkChain = _NO_CHAIN,
    count_root: bool = True,
) -> bool:
    """Copy the folder *src_path* into *dst_path*; return False only when the root was skipped by the overwrite policy."""
    root_outcome = await _ensure_directory(ctx, src_path, dst_path, options)
    if root_outcome == "skipped":
        if count_root:
            ctx.status.update_progress(inc_completed=1)
        return False
    resolver = _HolderResolver(src_path)
    file_tasks: list[asyncio.Task[None]] = []
    dir_stats: list[tuple[VPath, Stat]] = []  # only folders this copy created get the source attributes
    skipped_roots: list[PurePosixPath] = []
    async for src_root, src_dirs, src_files, src_links in src_path.walk():
        ctx.status.check_cancelled()
        if any(src_root.path.is_relative_to(skipped) for skipped in skipped_roots):
            continue
        dst_root = dst_path / src_root.path.relative_to(src_path.path)
        if src_root.path == src_path.path:
            outcome = root_outcome
        else:
            outcome = await _ensure_directory(ctx, src_root, dst_root, options)
            # A subfolder completes a unit of work when it is entered, also when the overwrite policy skips it.
            ctx.status.update_progress(inc_completed=1)
            if outcome == "skipped":
                skipped_roots.append(src_root.path)
                continue
        if outcome == "created":
            dir_stats.append((dst_root, src_root.stat))
        ctx.status.update_progress(inc_total=len(src_dirs) + len(src_files) + len(src_links))
        for f in src_files:
            ctx.status.check_cancelled()
            file_tasks.append(await ctx.subtask(_copy_file_step(ctx, f, dst_root / f.name, options)))
        for link in src_links:
            ctx.status.check_cancelled()
            holder = resolver.holder(src_root) if _needs_holder(link, dst_filesystem) else None
            file_tasks.append(await ctx.subtask(_copy_link_step(ctx, link, dst_root / link.name, options, chain, holder)))
    await asyncio.gather(*file_tasks)
    # Apply directory attributes bottom-up so that parent mtime is not disturbed
    # by child directory attribute changes.
    for dst_dir, src_stat in reversed(dir_stats):
        dst_filesystem.copy_stat(dst_dir, src_stat)
    if count_root:
        ctx.status.update_progress(inc_completed=1)
    return True


async def _copy_dir_step(
    ctx: TaskContext,
    src_path: VPath,
    dst_path: VPath,
    dst_filesystem: Filesystem,
    options: FileCopyOptions,
) -> None:
    await _copy_dir(ctx, src_path, dst_path, dst_filesystem, options)


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

    dst_stat = destination.stat_or_none
    dst_is_directory = dst_stat is not None and dst_stat.is_directory

    if len(src_paths) == 1 and not dst_is_directory and not _is_real_dir(src_paths[0].stat):
        ctx.status.update_progress(inc_total=1)
        await _copy_leaf_step(ctx, src_paths[0], destination, options)
        return

    if len(src_paths) == 1 and not dst_is_directory and _is_real_dir(src_paths[0].stat):
        ctx.status.update_progress(inc_total=1)
        await _copy_dir(ctx, src_paths[0], destination, dst_filesystem, options)
        return

    # All top-level items are counted upfront so the total is visible immediately.
    # Files within directories are added incrementally as they are discovered.
    ctx.status.update_progress(inc_total=len(src_paths))

    subtasks: list[asyncio.Task[None]] = []
    for src_path in src_paths:
        ctx.status.check_cancelled()
        dst_path = destination / src_path.name
        if _is_real_dir(src_path.stat):
            t = await ctx.subtask(_copy_dir_step(ctx, src_path, dst_path, dst_filesystem, options))
        else:
            t = await ctx.subtask(_copy_leaf_step(ctx, src_path, dst_path, options))
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
    removed bottom-up after their contents are moved. Links are renamed on the same device;
    across filesystems they are copied by ``_copy_link`` and removed from the source only if copied.
    """
    with contextlib.suppress(FileExistsError):
        actual_dst.filesystem.mkdir(actual_dst)
    resolver = _HolderResolver(src_path)
    src_dirs: list[VPath] = []
    dir_stats: list[tuple[VPath, Stat]] = []
    async for src_root, walk_subdirs, src_files, src_links in src_path.walk():
        ctx.status.check_cancelled()
        dst_root = actual_dst / src_root.path.relative_to(src_path.path)
        if src_root.path != src_path.path:  # the root was created above; SFTP reports an existing directory as a generic failure
            with contextlib.suppress(FileExistsError):
                actual_dst.filesystem.mkdir(dst_root)
        # The root directory is already counted by the caller; only subdirectories
        # (src_root != src_path) complete a unit of work when they are entered.
        if src_root.path != src_path.path:
            ctx.status.update_progress(inc_completed=1)
        src_dirs.append(src_root)
        dir_stats.append((dst_root, src_root.stat))
        ctx.status.update_progress(inc_total=len(walk_subdirs) + len(src_files) + len(src_links))
        for f in (*src_files, *src_links):
            ctx.status.check_cancelled()
            ctx.status.set_current_item(f.uri)
            if options.filter is not None and not options.filter.matches(f):
                ctx.status.update_progress(inc_completed=1)
                continue
            f_dst = dst_root / f.name
            if same_device:
                f_dst_stat = f_dst.stat_or_none
                if f_dst_stat is not None:
                    if options.overwrite == "skip":
                        ctx.status.update_progress(inc_completed=1)
                        continue
                    if options.overwrite == "ask":
                        response = await ctx.request_response(
                            "Overwrite",
                            expected_responses=[Response.YES, Response.NO, Response.ALL, Response.NONE],
                            message=f"File '{f_dst.path}' already exists. Overwrite?",
                        )
                        if response.is_rejected:
                            ctx.status.update_progress(inc_completed=1)
                            continue
                    f_dst.filesystem.remove(f_dst)
                f.filesystem.rename(f, f_dst)
            else:
                if f.stat.is_symlink:
                    holder = resolver.holder(src_root) if _needs_holder(f, actual_dst.filesystem) else None
                    copied = await _copy_link(ctx, f, f_dst, options, holder=holder)
                else:
                    copied = await copy_file(ctx, f, f_dst, options)
                if copied:
                    f.filesystem.remove(f)  # unlinks a link itself, never follows it
            ctx.status.update_progress(inc_completed=1)
    for d in reversed(src_dirs):
        with contextlib.suppress(OSError):
            d.filesystem.rmdir(d)
    # Apply directory attributes bottom-up so that parent mtime is not disturbed
    # by child directory attribute changes.
    for dst_dir, src_stat in reversed(dir_stats):
        actual_dst.filesystem.copy_stat(dst_dir, src_stat)


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
    destination. Cross-device moves copy the content then remove the source;
    links are copied by ``_copy_link`` and removed from the source only if copied.
    Increments the overall completed counter by one when done (including skip).
    """
    ctx.status.check_cancelled()
    ctx.status.set_current_item(src_path.uri)

    # Cache is_directory before any rename/remove that might invalidate the path.
    is_dir = _is_real_dir(src_path.stat)

    dst_stat = dst_path.stat_or_none
    actual_dst = dst_path / src_path.name if (dst_stat is not None and dst_stat.is_directory) else dst_path

    same_device = src_path.filesystem.is_same_device(src_path, actual_dst)

    # Filter check — skip non-matching files (directories are always traversed).
    if not is_dir and options.filter is not None and not options.filter.matches(src_path):
        _logger.debug("move_path filter skip %s", src_path.path)
        ctx.status.update_progress(inc_completed=1)
        return

    if same_device:
        actual_dst_stat = actual_dst.stat_or_none
        if is_dir and actual_dst_stat is not None and _is_real_dir(actual_dst_stat):
            # Destination directory already exists: walk per-file for fine-grained overwrite policy.
            # _move_dir_contents adds total and completed incrementally.
            await _move_dir_contents(ctx, src_path, actual_dst, options, same_device=True)
        else:
            if actual_dst_stat is not None:
                if options.overwrite == "skip":
                    _logger.debug("move_path skip (exists) %s", actual_dst.path)
                    ctx.status.update_progress(inc_completed=1)
                    return
                if options.overwrite == "ask":
                    response = await ctx.request_response(
                        "Overwrite",
                        expected_responses=[Response.YES, Response.NO, Response.ALL, Response.NONE],
                        message=f"'{actual_dst.path}' already exists. Overwrite?",
                    )
                    if response.is_rejected:
                        _logger.debug("move_path skip (user declined) %s", actual_dst.path)
                        ctx.status.update_progress(inc_completed=1)
                        return
                if _is_real_dir(actual_dst_stat):
                    await erase_files(ctx, [actual_dst], EraseFilesOptions(ask_before_erase=False))
                else:
                    actual_dst.filesystem.remove(actual_dst)
            src_path.filesystem.rename(src_path, actual_dst)
            _logger.debug("move_path renamed %s -> %s", src_path.path, actual_dst.path)
    else:
        if is_dir:
            # _move_dir_contents adds total and completed incrementally.
            await _move_dir_contents(ctx, src_path, actual_dst, options, same_device=False)
        elif src_path.stat.is_symlink:
            if await _copy_link(ctx, src_path, actual_dst, options):
                src_path.filesystem.remove(src_path)
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
    Each path is processed as a concurrent subtask so that a user response
    blocking one item does not stall the others.
    """
    if options is None:
        options = FileCopyOptions()

    # All top-level items are counted upfront so the total is visible immediately.
    # Files within directories are added incrementally as they are discovered.
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

    Links (to files, directories, broken or looping) are removed themselves
    without being followed or prompted for. Files are removed directly.
    Real directories are erased recursively via :func:`erase_files`; if
    non-empty and *options.ask_before_erase* is set, the user is asked to
    confirm before deletion proceeds. Increments the overall completed
    counter by one when done (including when skipped).
    """
    ctx.status.check_cancelled()
    ctx.status.set_current_item(path.uri)
    _logger.debug("erase_path %s", path.path)
    if options.refresh_stat:
        # The panel's stat may be stale (a directory replaced by a link); SSH caches listings per directory, so drop the cache first.
        path.filesystem.refresh(path.parent)
        try:
            await path.fresh_stat()
        except FileNotFoundError:
            _logger.debug("erase_path vanished %s", path.path)
            ctx.status.update_progress(inc_completed=1)
            return
    stat = path.stat
    if stat.is_symlink:
        # A link of any kind is removed itself; its target is never listed, stat-followed or touched.
        path.filesystem.remove(path)
    elif stat.is_directory:
        path.filesystem.refresh(path)
        children = [child async for child in path.iterdir()]
        if len(children) > 0 and options.ask_before_erase:
            response = await ctx.request_response(
                "Delete non-empty directory",
                expected_responses=[Response.YES, Response.NO, Response.ALL, Response.NONE],
                message=f"Directory '{path.path}' is not empty. Delete it recursively?",
            )
            if response.is_rejected:
                _logger.debug("erase_path skip (user declined) %s", path.path)
                ctx.status.update_progress(inc_completed=1)
                return
        await erase_files(ctx, children, EraseFilesOptions(ask_before_erase=False, refresh_stat=False))
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

    Each path is processed as a concurrent subtask so that a user response
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
