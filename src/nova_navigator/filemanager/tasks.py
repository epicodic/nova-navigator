import asyncio
import contextlib
import logging
import os
from collections.abc import Iterator
from dataclasses import dataclass, replace
from pathlib import PurePath, PurePosixPath
from typing import Literal

from nova_navigator.scheduler import TaskContext
from nova_widgets import Response

from ..file_filter import FileFilter
from ..vfs import VPath
from ..vfs.filesystem import Filesystem, StreamReaderLike, StreamWriterLike
from ..vfs.filesystems.local import LocalFilesystem
from ..vfs.types import Stat
from .link_target import stable_link_target

_logger = logging.getLogger(__name__)


def _is_real_dir(stat: Stat) -> bool:
    """Return whether *stat* describes a directory that is not a symbolic link (link stat first)."""
    return stat.is_directory and not stat.is_symlink


SKIP_BROKEN_LINK = "broken link"
SKIP_LINK_LOOP = "link loop"
SKIP_UNREADABLE_LINK = "unreadable link"
SKIP_FOLDER_EXISTS = "folder exists"
SKIP_SAME_FILE = "same file"
SKIP_INTO_ITSELF = "into itself"
_MAX_LINK_DEPTH = 16
_MAX_LINK_HOPS = 40  # Linux refuses to open a longer chain of links (ELOOP), so no write can go through one


@dataclass(frozen=True)
class _LinkChain:
    """Real paths of the directories that hold the links entered on the current descent (outermost first)."""

    positions: tuple[PurePosixPath, ...] = ()
    depth: int = 0


_NO_CHAIN = _LinkChain()


CHUNK_SIZE = 64 * 1024  # 64 KB


OverwritePolicy = Literal["overwrite", "skip", "ask"]


@dataclass
class FileCopyOptions:
    """Options that control the behaviour of file copy operations."""

    overwrite: OverwritePolicy = "ask"
    filter: FileFilter | None = None
    follow_links: bool = False
    """Copy a link's target (file content or directory tree) instead of recreating the link (REQ-3)."""
    stable_symlinks: bool = False
    """Rewrite relative link targets that point outside the copied item when the link is recreated (REQ-4)."""


@dataclass
class EraseFilesOptions:
    """Options that control the behaviour of file erase operations."""

    ask_before_erase: bool = True
    refresh_stat: bool = True
    """Refresh the cached stat of each path before erasing; internal children listings turn it off."""


def _is_same_storage(a: VPath, b: VPath) -> bool:
    """Return whether *a* and *b* are paths on the same storage."""
    return a.filesystem.unwrap().is_same_storage(b.filesystem.unwrap()) or a.filesystem.is_same_device(a, b)


def _is_same_entry(a: VPath, b: VPath) -> bool:
    """Return whether *a* and *b* name the same existing entry; a final link is not followed.

    Local paths compare by inode, so symlinked parent folders and hard links count.
    Elsewhere the paths must be equal, or the names equal and the parent folders resolve to the same folder.
    """
    if not _is_same_storage(a, b):
        return False
    if a.path == b.path:  # same storage and same path: the same entry, so no stat is needed
        return True
    try:
        if isinstance(a.filesystem.unwrap(), LocalFilesystem):
            return os.path.samestat(os.lstat(a.path), os.lstat(b.path))
        if a.name != b.name:
            return False
        b.filesystem.stat(b)  # raises when there is nothing at *b*; does not cache on the VPath
        real_a = a.filesystem.real_path(a.parent)
        real_b = b.filesystem.real_path(b.parent)
    except OSError:
        return False
    return real_a is not None and real_b is not None and real_a.path == real_b.path


def _is_onto_itself(src: VPath, dst: VPath) -> bool:
    """Return whether writing *src* to *dst* would destroy what is being copied or moved.

    That is the case when *dst* is *src* itself, or when *src* is a link and *dst* is on its way to the target.
    """
    if not src.stat.is_symlink:
        return _is_same_entry(src, dst)
    return any(_is_same_entry(hop, dst) for hop in _walk_links(src))


def _skip_onto_itself(ctx: TaskContext, src: VPath, dst: VPath) -> bool:
    """Report *src* as skipped and return True when it must not be written to *dst*."""
    if not _is_onto_itself(src, dst):
        return False
    ctx.report_skipped(src.uri, SKIP_SAME_FILE)
    return True


def _walk_links(path: VPath) -> Iterator[VPath]:
    """Yield *path* and then, while it is a link, each next step towards its target (at most _MAX_LINK_HOPS steps)."""
    yield path
    for _ in range(_MAX_LINK_HOPS):
        try:
            path = path.parent / path.filesystem.readlink(path)
        except OSError:  # the end of the chain: not a link any more
            return
        yield path


def _link_chain(path: VPath) -> Iterator[VPath]:
    """Yield *path* and then, on a filesystem with links, each next step towards its target."""
    if not path.filesystem.supports_symlinks():
        yield path
        return
    yield from _walk_links(path)


def would_overwrite_source(src: VPath, dst: VPath) -> bool:
    """Return whether writing through *dst* would overwrite what is read through *src*; links are followed on both."""
    targets = list(_link_chain(dst))
    return any(_is_same_entry(source, target) for source in _link_chain(src) for target in targets)


def _resolved(path: VPath) -> PurePath:
    """Return *path* with links resolved as far as it exists; the rest is appended as written."""
    for existing in (path.path, *path.path.parents):
        try:
            real = path.filesystem.real_path(VPath(existing, path.filesystem))
        except OSError:
            continue
        if real is None:
            break
        return real.path / path.path.relative_to(existing)
    return path.path


def is_into_itself(src: VPath, dst: VPath) -> bool:
    """Return whether *dst* is the folder *src* or lies inside it."""
    return _is_same_storage(src, dst) and _resolved(dst).is_relative_to(_resolved(src))


def _is_into_itself_for_write(src: VPath, dst: VPath) -> bool:
    """Return whether writing the folder *src* to *dst* would put it inside itself.

    Unlike ``is_into_itself``, a final *dst* that is a link is not followed: writing a folder replaces that link
    (see ``_ensure_directory``), so only its parent is resolved.
    """
    if not _is_same_storage(src, dst):
        return False
    dst_stat = _fresh_stat_or_none(dst.filesystem, dst)
    if dst_stat is not None and dst_stat.is_symlink:
        destination = _resolved(dst.parent) / dst.name
    else:
        destination = _resolved(dst)
    return destination.is_relative_to(_resolved(src))


def _skip_into_itself(ctx: TaskContext, src: VPath, dst: VPath) -> bool:
    """Report the folder *src* as skipped and return True when *dst* lies inside it."""
    if not _is_into_itself_for_write(src, dst):
        return False
    ctx.report_skipped(src.uri, SKIP_INTO_ITSELF)
    return True


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


@dataclass(frozen=True)
class _Claim:
    """Outcome of ``_claim_file_destination``.

    ``proceed`` tells whether to write.
    ``removed_link_text`` is the text of a destination link that was removed, if any.
    """

    proceed: bool
    removed_link_text: str | None = None


async def _claim_file_destination(
    ctx: TaskContext,
    src: VPath,
    dst: VPath,
    options: FileCopyOptions,
    label: str,
) -> _Claim:
    """Decide what happens at *dst* before a file is written there.

    An absent destination proceeds.
    A real folder is skipped as "folder exists" without a question under any policy.
    Such a skip does not consume an ALL/NONE answer.
    Anything else goes through the overwrite policy once.
    A destination link is removed itself, never followed, so the write cannot reach its target.
    """
    stat = dst.stat_or_none
    if stat is None:
        return _Claim(proceed=True)
    if _is_real_dir(stat):
        ctx.report_skipped(src.uri, SKIP_FOLDER_EXISTS)
        return _Claim(proceed=False)
    if not await _overwrite_allowed(ctx, src, dst.with_stat(stat), options, label):
        return _Claim(proceed=False)
    if not (stat.is_symlink and dst.filesystem.supports_symlinks()):
        return _Claim(proceed=True)
    text: str | None = None
    with contextlib.suppress(OSError):
        text = dst.filesystem.readlink(dst)
    dst.filesystem.remove(dst)  # unlinks the link itself, also when it points to a folder or is broken
    return _Claim(proceed=True, removed_link_text=text)


def _restore_link(dst: VPath, claim: _Claim) -> None:
    """Put back a destination link that ``_claim_file_destination`` removed, best effort."""
    if claim.removed_link_text is None:
        return
    with contextlib.suppress(Exception):
        dst.filesystem.symlink(dst, claim.removed_link_text)


def _close_streams(reader: StreamReaderLike | None, writer: StreamWriterLike | None) -> None:
    """Close the reader and then the writer; the writer is closed even if the reader close raises."""
    try:
        if reader:
            reader.close()
    finally:
        if writer:
            writer.close()


DirOutcome = Literal["created", "merged", "skipped"]


def _fresh_stat_or_none(filesystem: Filesystem, path: VPath) -> Stat | None:
    """Return a fresh stat of *path* (dropping the cached parent listing first), or None if it does not exist."""
    filesystem.refresh(path.parent)
    try:
        return filesystem.stat(path)
    except FileNotFoundError:
        return None


async def _ensure_directory(ctx: TaskContext, src: VPath, dst: VPath, options: FileCopyOptions) -> DirOutcome:
    """Make *dst* a usable real directory for the folder *src* (REQ-6, REQ-7).

    One decision point for copy and both move paths.
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
    destination already exists (a regular file, a link or a link to a folder)
    the action depends on ``options.overwrite``: ``"overwrite"`` replaces
    unconditionally, ``"skip"`` leaves it untouched, ``"ask"`` requests a user
    response. A destination link is replaced by the file, never written through.

    Returns ``True`` if the file was actually written, ``False`` if the copy
    was skipped (destination exists and overwrite policy did not proceed).
    """
    if options is None:
        options = FileCopyOptions()

    _logger.debug("copy_file %s -> %s", src_path.path, dst_path.path)
    ctx.status.set_current_item(src_path.uri)
    if _skip_onto_itself(ctx, src_path, dst_path):
        return False
    reader = None
    writer = None
    claim = _Claim(proceed=False)
    _failed = False
    try:
        src_stat = src_path.stat
        reader = src_path.filesystem.read(src_path)

        claim = await _claim_file_destination(ctx, src_path, dst_path, options, "copy_file")
        if not claim.proceed:
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
        try:
            try:
                _close_streams(reader, writer)
            except BaseException:
                if not _failed:  # an earlier error stays the one that is raised
                    _failed = True
                    raise
        finally:
            if _failed:
                if writer:
                    with contextlib.suppress(Exception):
                        dst_path.filesystem.remove(dst_path)
                _restore_link(dst_path, claim)
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
    """Apply the overwrite policy to an existing *dst*.

    Return True when the link may be created; *dst* is then removed.
    A real folder at *dst* is skipped ("folder exists") without a question.
    """
    stat = dst.stat_or_none
    if stat is None:
        return True
    if _is_real_dir(stat):
        ctx.report_skipped(link.uri, SKIP_FOLDER_EXISTS)
        return False
    if not await _overwrite_allowed(ctx, link, dst, options, "copy_link"):
        return False
    dst.filesystem.remove(dst)  # removes a file or link itself, never follows
    return True


def _stable_text(link: VPath, dst: VPath, text: str, source_root: PurePosixPath | None) -> str:
    """Apply the stable-symlink rule; a path the rule rejects (not absolute) keeps the text unchanged."""
    try:
        return stable_link_target(text, link.path, dst.path, source_root)
    except ValueError:
        _logger.warning(
            "Stable symlinks: keeping the target text of %s (path is not absolute)",
            link.path,
            exc_info=True,
        )
        return text


async def _recreate_link(
    ctx: TaskContext,
    link: VPath,
    dst: VPath,
    options: FileCopyOptions,
    *,
    source_root: PurePosixPath | None,
) -> bool:
    """Create at *dst* a link with the same target text as *link*; never reads or descends into the target."""
    if _skip_onto_itself(ctx, link, dst):
        return False
    try:
        text = link.filesystem.readlink(link)
    except OSError:
        _logger.warning("Skipping link %s: its target cannot be read", link.path, exc_info=True)
        ctx.report_skipped(link.uri, SKIP_UNREADABLE_LINK)
        return False
    if options.stable_symlinks and link.filesystem == dst.filesystem:
        text = _stable_text(link, dst, text, source_root)
    if not await _confirm_link_replace(ctx, link, dst, options):
        return False
    dst.filesystem.symlink(dst, text)
    return True


def _followed_view(link: VPath) -> VPath:
    """Return a VPath at *link*'s own path whose stat no longer says "link".

    The stat change lets ``walk`` enter it (there is no resolved-path API).
    """
    return link.with_stat(replace(link.stat, is_symlink=False))


def _needs_holder(link: VPath, dst_filesystem: Filesystem, options: FileCopyOptions) -> bool:
    """Return True if copying *link* needs the real position of the directory holding it (loop test)."""
    stat = link.stat
    follows = options.follow_links or not dst_filesystem.supports_symlinks()
    return follows and stat.is_directory and not stat.is_broken_symlink


class _HolderResolver:
    """Lazily resolves the real (canonical) position of directories below a walk root.

    ``None`` when the filesystem cannot tell.
    """

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
    """Return True if *target* is a directory already being copied.

    That is, it is an ancestor-or-self of the holder or of a chain position.
    """
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


async def _link_target_missing(link: VPath) -> bool:
    """Return True if the target of *link* no longer exists (fresh stat, parent listing dropped first)."""
    link.filesystem.refresh(link.parent)
    try:
        stat = await link.fresh_stat()
    except FileNotFoundError:
        return True
    return stat.is_broken_symlink


def _target_mode_view(link: VPath) -> VPath:
    """Return *link* carrying the permission bits of its target instead of the link's own (``0o777``).

    Falls back to *link* itself when the filesystem cannot resolve the target.
    """
    try:
        real = link.filesystem.real_path(link)
        if real is None:
            return link
        target = link.filesystem.stat(real)
    except OSError:
        return link
    return link.with_stat(replace(link.stat, mode=target.mode))


async def _follow_link(
    ctx: TaskContext,
    link: VPath,
    dst: VPath,
    options: FileCopyOptions,
    chain: _LinkChain,
    holder: PurePosixPath | None,
) -> bool:
    """Copy what *link* points to under *dst*.

    A file is copied as content and a directory as a tree; broken and looping links are skipped.
    A file target that vanished after the link was listed (for example moved earlier in the same move) is also
    reported as broken.
    A vanished directory target is caught only when ``real_path`` resolves it; see docs/symlinks.md.
    """
    stat = link.stat
    if stat.is_broken_symlink:
        ctx.report_skipped(link.uri, SKIP_BROKEN_LINK)
        return False
    if stat.is_directory:
        return await _copy_linked_directory(ctx, link, dst, options, chain, holder)
    try:
        source = _target_mode_view(link) if options.follow_links else link
        return await copy_file(ctx, source, dst, options)
    except FileNotFoundError:
        if await _link_target_missing(link):
            ctx.report_skipped(link.uri, SKIP_BROKEN_LINK)
            return False
        raise


async def _copy_link(
    ctx: TaskContext,
    link: VPath,
    dst: VPath,
    options: FileCopyOptions,
    chain: _LinkChain = _NO_CHAIN,
    holder: PurePosixPath | None = None,
    *,
    source_root: PurePosixPath | None,
) -> bool:
    """Copy the symbolic link *link* to *dst*; return True if something was created at the destination.

    One decision point for copy and for cross-device move (REQ-3, REQ-9).
    ``follow_links`` selects the follow path for every destination.
    Otherwise a link-capable destination gets a link with the same target text.
    Never touches the ``completed`` counter: the caller counts the link as one unit of work.
    """
    ctx.status.set_current_item(link.uri)
    if not options.follow_links and dst.filesystem.supports_symlinks():
        return await _recreate_link(ctx, link, dst, options, source_root=source_root)
    return await _follow_link(ctx, link, dst, options, chain, holder)


async def _copy_link_step(
    ctx: TaskContext,
    link: VPath,
    dst: VPath,
    options: FileCopyOptions,
    chain: _LinkChain = _NO_CHAIN,
    holder: PurePosixPath | None = None,
    *,
    source_root: PurePosixPath | None,
) -> None:
    if options.filter is not None and not options.filter.matches(link):
        ctx.status.update_progress(inc_completed=1)
        return
    await _copy_link(ctx, link, dst, options, chain, holder, source_root=source_root)
    ctx.status.update_progress(inc_completed=1)


async def _copy_leaf_step(ctx: TaskContext, src: VPath, dst: VPath, options: FileCopyOptions) -> None:
    """Copy a top-level item that is not a real directory.

    A link recreates or copies as a link; anything else is copied as a file.
    """
    if src.stat.is_symlink:
        await _copy_link_step(ctx, src, dst, options, source_root=None)
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
    """Copy the folder *src_path* into *dst_path*.

    Return False only when the root was skipped, by the overwrite policy or because *dst_path* is the folder
    itself or lies inside it.
    """
    if _skip_onto_itself(ctx, src_path, dst_path) or _skip_into_itself(ctx, src_path, dst_path):
        if count_root:
            ctx.status.update_progress(inc_completed=1)
        return False
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
            holder = resolver.holder(src_root) if _needs_holder(link, dst_filesystem, options) else None
            link_step = _copy_link_step(
                ctx,
                link,
                dst_root / link.name,
                options,
                chain,
                holder,
                source_root=src_path.path,
            )
            file_tasks.append(await ctx.subtask(link_step))
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
    """Run ``_copy_dir`` as a subtask body.

    Exists so ``ctx.subtask`` gets a coroutine that returns None, matching ``list[asyncio.Task[None]]``.
    The bool from ``_copy_dir`` is discarded here.
    """
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
    # The claim stats each destination entry from the cached listing, so drop stale listings once per job.
    dst_filesystem.refresh(destination)
    dst_filesystem.refresh(destination.parent)

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


async def _move_entry(
    ctx: TaskContext,
    src_root: VPath,
    f: VPath,
    f_dst: VPath,
    options: FileCopyOptions,
    resolver: _HolderResolver,
    *,
    same_device: bool,
    item_root: PurePosixPath,
) -> None:
    """Move one file or link *f* of the folder *src_root* to *f_dst*; the caller counts it as completed.

    *item_root* is the selected item the link belongs to (the stable-symlink boundary).
    """
    ctx.status.set_current_item(f.uri)
    if options.filter is not None and not options.filter.matches(f):
        return
    if _skip_onto_itself(ctx, f, f_dst):
        return
    if same_device:
        f_dst_stat = f_dst.stat_or_none
        if f_dst_stat is not None and _is_real_dir(f_dst_stat):
            ctx.report_skipped(f.uri, SKIP_FOLDER_EXISTS)
            return
        if f_dst_stat is not None:
            if options.overwrite == "skip":
                return
            if options.overwrite == "ask":
                response = await ctx.request_response(
                    "Overwrite",
                    expected_responses=[Response.YES, Response.NO, Response.ALL, Response.NONE],
                    message=f"File '{f_dst.path}' already exists. Overwrite?",
                )
                if response.is_rejected:
                    return
            f_dst.filesystem.remove(f_dst)
        f.filesystem.rename(f, f_dst)
        return
    if f.stat.is_symlink:
        holder = resolver.holder(src_root) if _needs_holder(f, f_dst.filesystem, options) else None
        copied = await _copy_link(ctx, f, f_dst, options, holder=holder, source_root=item_root)
    else:
        copied = await copy_file(ctx, f, f_dst, options)
    if copied:
        f.filesystem.remove(f)  # unlinks a link itself, never follows it


async def _move_dir_contents(
    ctx: TaskContext,
    src_path: VPath,
    actual_dst: VPath,
    options: FileCopyOptions,
    *,
    same_device: bool,
) -> None:
    """Walk *src_path* and move each file into the mirrored path under *actual_dst*.

    Used when the destination directory may already exist (so an atomic rename of
    the whole tree is not possible). Every folder goes through ``_ensure_directory``;
    a folder the overwrite policy skips stays in the source together with its subtree.
    Files are processed individually so that the overwrite policy is applied at file
    granularity. Source directories are removed bottom-up after their contents are moved,
    except the skipped ones. Links are renamed on the same device;
    across filesystems they are copied by ``_copy_link`` and removed from the source only if copied.
    """
    root_outcome = await _ensure_directory(ctx, src_path, actual_dst, options)
    if root_outcome == "skipped":
        return
    resolver = _HolderResolver(src_path)
    src_dirs: list[VPath] = []
    dir_stats: list[tuple[VPath, Stat]] = []  # only folders this move created get the source attributes
    skipped_roots: list[PurePosixPath] = []
    async for src_root, walk_subdirs, src_files, src_links in src_path.walk():
        ctx.status.check_cancelled()
        if any(src_root.path.is_relative_to(skipped) for skipped in skipped_roots):
            continue
        dst_root = actual_dst / src_root.path.relative_to(src_path.path)
        if src_root.path == src_path.path:
            outcome = root_outcome
        else:
            outcome = await _ensure_directory(ctx, src_root, dst_root, options)
            # The root is counted by the caller; a subfolder completes a unit of work when it is entered,
            # also when the overwrite policy skips it.
            ctx.status.update_progress(inc_completed=1)
            if outcome == "skipped":
                skipped_roots.append(src_root.path)
                continue
        if outcome == "created":
            dir_stats.append((dst_root, src_root.stat))
        src_dirs.append(src_root)
        ctx.status.update_progress(inc_total=len(walk_subdirs) + len(src_files) + len(src_links))
        for f in (*src_files, *src_links):
            ctx.status.check_cancelled()
            await _move_entry(
                ctx,
                src_root,
                f,
                dst_root / f.name,
                options,
                resolver,
                same_device=same_device,
                item_root=src_path.path,
            )
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

    dst_stat = _fresh_stat_or_none(dst_path.filesystem, dst_path)
    actual_dst = dst_path / src_path.name if (dst_stat is not None and dst_stat.is_directory) else dst_path

    same_device = src_path.filesystem.is_same_device(src_path, actual_dst)

    # Filter check — skip non-matching files (directories are always traversed).
    if not is_dir and options.filter is not None and not options.filter.matches(src_path):
        _logger.debug("move_path filter skip %s", src_path.path)
        ctx.status.update_progress(inc_completed=1)
        return

    # Before any branch: each of them removes or truncates the destination.
    if _skip_onto_itself(ctx, src_path, actual_dst) or (is_dir and _skip_into_itself(ctx, src_path, actual_dst)):
        ctx.status.update_progress(inc_completed=1)
        return

    if same_device:
        actual_dst_stat = _fresh_stat_or_none(actual_dst.filesystem, actual_dst)
        if is_dir and actual_dst_stat is not None and _is_real_dir(actual_dst_stat):
            # Destination directory already exists: walk per-file for fine-grained overwrite policy.
            # _move_dir_contents adds total and completed incrementally.
            await _move_dir_contents(ctx, src_path, actual_dst, options, same_device=True)
        else:
            if actual_dst_stat is not None and _is_real_dir(actual_dst_stat):
                # A file or link never replaces a folder; a folder source onto a folder took the merge branch above.
                ctx.report_skipped(src_path.uri, SKIP_FOLDER_EXISTS)
                ctx.status.update_progress(inc_completed=1)
                return
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
                actual_dst.filesystem.remove(actual_dst)
            src_path.filesystem.rename(src_path, actual_dst)
            _logger.debug("move_path renamed %s -> %s", src_path.path, actual_dst.path)
    else:
        # The copy claim stats the destination from the cached listing, so drop a stale one first.
        actual_dst.filesystem.refresh(actual_dst.parent)
        if is_dir:
            # _move_dir_contents adds total and completed incrementally.
            await _move_dir_contents(ctx, src_path, actual_dst, options, same_device=False)
        elif src_path.stat.is_symlink:
            if await _copy_link(ctx, src_path, actual_dst, options, source_root=None):
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


def _remove_if_link_now(path: VPath) -> bool:
    """Stat *path* again, bypassing listing caches; if it is a link now, remove the link and return True."""
    if not path.filesystem.stat_uncached(path).is_symlink:
        return False
    path.filesystem.remove(path)
    return True


async def _erase_directory(ctx: TaskContext, path: VPath, options: EraseFilesOptions) -> None:
    """Erase the directory *path* and its content, unless the user declines.

    The entry is checked again before it is listed and again after the question,
    so a link swapped in meanwhile is removed itself and its target stays untouched.
    """
    if _remove_if_link_now(path):
        return
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
            return
        if _remove_if_link_now(path):
            return
    await erase_files(ctx, children, EraseFilesOptions(ask_before_erase=False, refresh_stat=False))
    path.filesystem.rmdir(path)


def _is_gone(path: VPath) -> bool:
    """Return whether *path* really does not exist any more."""
    try:
        path.filesystem.stat_uncached(path)
    except FileNotFoundError:
        return True
    return False


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
    confirm before deletion proceeds. An entry that has vanished meanwhile is
    counted as done. Increments the overall completed counter by one when done
    (including when skipped).
    """
    ctx.status.check_cancelled()
    ctx.status.set_current_item(path.uri)
    _logger.debug("erase_path %s", path.path)
    if options.refresh_stat:
        # The panel's stat may be stale (a directory replaced by a link).
        # SSH caches listings per directory, so drop the cache first.
        path.filesystem.refresh(path.parent)
        try:
            await path.fresh_stat()
        except FileNotFoundError:
            _logger.debug("erase_path vanished %s", path.path)
            ctx.status.update_progress(inc_completed=1)
            return
    try:
        stat = path.stat
        if stat.is_symlink:
            # A link of any kind is removed itself; its target is never listed, stat-followed or touched.
            path.filesystem.remove(path)
        elif stat.is_directory:
            await _erase_directory(ctx, path, options)
        else:
            path.filesystem.remove(path)
    except FileNotFoundError:
        # SFTP also reports other failures as "no such file", so make sure the entry is gone.
        if not _is_gone(path):
            raise
        _logger.debug("erase_path vanished %s", path.path)
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
