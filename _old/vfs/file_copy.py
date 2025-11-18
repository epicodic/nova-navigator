import os
import shutil
import threading
from collections.abc import Callable

from .interaction_context import InteractionContext

ProgressCallback = Callable[[int, int], None]


class Error(OSError):
    pass


class SameFileError(Error):
    """Raised when source and destination are the same file."""


def samefile(src: str, dst: str) -> bool:
    return shutil._samefile(src, dst)  # type: ignore[attr-defined]


def _copytree(
    ctx: InteractionContext,
    src: str,
    dst: str,
    symlinks: bool,
    ignore_dangling_symlinks: bool,
    dirs_exist_ok: bool,
) -> None:
    ctx.check_cancelled()

    with os.scandir(src) as itr:
        entries = list(itr)

    ctx.update_progress(inc_total=len(entries))

    os.makedirs(dst, exist_ok=dirs_exist_ok)

    errors = []

    for srcentry in entries:
        ctx.check_cancelled()
        srcname = os.path.join(src, srcentry.name)
        dstname = os.path.join(dst, srcentry.name)
        try:
            is_symlink = srcentry.is_symlink()
            if is_symlink and os.name == "nt":
                # Special check for directory junctions, which appear as
                # symlinks but we want to recurse.
                lstat = srcentry.stat(follow_symlinks=False)
                if lstat.st_reparse_tag == shutil.stat.IO_REPARSE_TAG_MOUNT_POINT:  # type: ignore[attr-defined]
                    is_symlink = False
            if is_symlink:
                linkto = os.readlink(srcname)
                if symlinks:
                    # We can't just leave it to `copy_function` because legacy
                    # code with a custom `copy_function` may rely on copytree
                    # doing the right thing.
                    os.symlink(linkto, dstname)
                    shutil.copystat(srcname, dstname, follow_symlinks=not symlinks)
                else:
                    # ignore dangling symlink if the flag is on
                    if not os.path.exists(linkto) and ignore_dangling_symlinks:
                        continue
                    # otherwise let the copy occur. copy2 will raise an error
                    if srcentry.is_dir():
                        _copytree(ctx, srcname, dstname, symlinks, ignore_dangling_symlinks, dirs_exist_ok)
                    else:
                        copyfile(ctx, srcname, dstname)
            elif srcentry.is_dir():
                _copytree(ctx, srcname, dstname, symlinks, ignore_dangling_symlinks, dirs_exist_ok)
            else:
                # Will raise a SpecialFileError for unsupported file types
                copyfile(ctx, srcname, dstname)
        # catch the Error from the recursive copytree so that we can
        # continue with other files
        except Error as err:
            errors.extend(err.args[0])
        except OSError as why:
            errors.append((srcname, dstname, str(why)))

        ctx.update_progress(inc_completed=1)

    try:
        shutil.copystat(src, dst)
    except OSError as why:
        # Copying file access times may fail on Windows
        if getattr(why, "winerror", None) is None:
            errors.append((src, dst, str(why)))
    if errors:
        raise Error(errors)


def copytree(
    ctx: InteractionContext,
    src_path: str,
    dst_path: str,
    *,
    symlinks: bool = False,
    ignore_dangling_symlinks: bool = False,
    dirs_exist_ok: bool = False,
) -> None:
    """Recursively copy a directory tree from src to dst.

    In comparison to shutil.copytree, this function supports cancellation and progress reporting.
    The total number of files is not scanned in advance for performance reasons, instead the progress
    is updated as files are copied.
    """
    # sys.audit("nova_navigator.vfs.copytree", src_path, dst_path)

    _copytree(
        ctx,
        src_path,
        dst_path,
        symlinks,
        ignore_dangling_symlinks,
        dirs_exist_ok,
    )


def copy_files_and_directories(
    src_paths: list[str],
    dst_path: str,
    *,
    symlinks: bool = False,
    ignore_dangling_symlinks: bool = False,
    dirs_exist_ok: bool = False,
    cancel_event: threading.Event | None = None,
    progress_callback: ProgressCallback | None = None,
) -> None:
    progress_helper = ProgressHelper(cancel_event=cancel_event, progress_callback=progress_callback)
    progress_helper.update_progress(inc_total=len(src_paths))
    for src_path in src_paths:
        progress_helper.check_cancelled()
        if os.path.isdir(src_path):
            _copytree(
                src_path,
                dst_path,
                symlinks,
                ignore_dangling_symlinks,
                dirs_exist_ok,
                progress_helper,
            )
        else:
            dest_file_path = os.path.join(dst_path, os.path.basename(src_path))
            copyfile(
                src_path,
                dest_file_path,
                cancel_event=cancel_event,
                progress_callback=progress_callback,
            )
        progress_helper.update_progress(inc_completed=1)


def copyfile(
    ctx: InteractionContext,
    src_path: str,
    dst_path: str,
    *,
    follow_symlinks: bool = True,
) -> None:
    if samefile(src_path, dst_path):
        raise SameFileError(f"'{src_path}' and '{dst_path}' are the same file")

    if not follow_symlinks and os.path.islink(src_path):
        os.symlink(os.readlink(src_path), dst_path)
    else:
        _copy_sendfile(
            src_path,
            dst_path,
            progress_helper=ProgressHelper(cancel_event=cancel_event, progress_callback=progress_callback),
        )
    shutil.copystat(src_path, dst_path, follow_symlinks=follow_symlinks)


def _copy_sendfile(
    src_path: str,
    dst_path: str,
    progress_helper: ProgressHelper,
    block_size: int = 2**27,  # 128MiB,
) -> None:
    total_size = os.path.getsize(src_path)
    progress_helper.update_progress(inc_total=total_size)
    with open(src_path, "rb") as fsrc, open(dst_path, "wb") as fdst:
        offset = 0
        srcfd = fsrc.fileno()
        dstfd = fdst.fileno()
        while True:
            progress_helper.check_cancelled()
            sent = os.sendfile(dstfd, srcfd, offset, block_size)

            if sent == 0:
                break  # EOF
            offset += sent
            progress_helper.update_progress(inc_completed=sent)

    progress_helper.set_progress(total_size, total_size)
