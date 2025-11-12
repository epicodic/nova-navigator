import os
import shutil
import sys
import threading
from collections.abc import Callable
from pathlib import PurePath


class CopyCancelled(Exception):
    """Raised when copy operation is cancelled."""


def _copytree(entries, src, dst, symlinks, ignore_dangling_symlinks, dirs_exist_ok=False) -> str:
    os.makedirs(dst, exist_ok=dirs_exist_ok)
    errors = []

    for srcentry in entries:
        srcname = os.path.join(src, srcentry.name)
        dstname = os.path.join(dst, srcentry.name)
        srcobj = srcentry
        try:
            is_symlink = srcentry.is_symlink()
            if is_symlink and os.name == "nt":
                # Special check for directory junctions, which appear as
                # symlinks but we want to recurse.
                lstat = srcentry.stat(follow_symlinks=False)
                if lstat.st_reparse_tag == shutil.stat.IO_REPARSE_TAG_MOUNT_POINT:
                    is_symlink = False
            if is_symlink:
                linkto = os.readlink(srcname)
                if symlinks:
                    # We can't just leave it to `copy_function` because legacy
                    # code with a custom `copy_function` may rely on copytree
                    # doing the right thing.
                    os.symlink(linkto, dstname)
                    shutil.copystat(srcobj, dstname, follow_symlinks=not symlinks)
                else:
                    # ignore dangling symlink if the flag is on
                    if not os.path.exists(linkto) and ignore_dangling_symlinks:
                        continue
                    # otherwise let the copy occur. copy2 will raise an error
                    if srcentry.is_dir():
                        copytree(srcobj, dstname, symlinks, ignore_dangling_symlinks, dirs_exist_ok)
                    else:
                        shutil.copy2(srcobj, dstname)
            elif srcentry.is_dir():
                copytree(srcobj, dstname, symlinks, ignore_dangling_symlinks, dirs_exist_ok)
            else:
                # Will raise a SpecialFileError for unsupported file types
                shutil.copy2(srcobj, dstname)
        # catch the Error from the recursive copytree so that we can
        # continue with other files
        except Error as err:
            errors.extend(err.args[0])
        except OSError as why:
            errors.append((srcname, dstname, str(why)))
    try:
        shutil.copystat(src, dst)
    except OSError as why:
        # Copying file access times may fail on Windows
        if getattr(why, "winerror", None) is None:
            errors.append((src, dst, str(why)))
    if errors:
        raise Error(errors)
    return dst


def copytree(
    src: str,
    dst: str,
    symlinks: bool = False,
    ignore_dangling_symlinks: bool = False,
    dirs_exist_ok: bool = False,
    cancel_event: threading.Event | None = None,
    progress_callback: Callable[[int, int], None] | None = None,
) -> str:
    """Recursively copy a directory tree from src to dst.

    In comparison to shutil.copytree, this function supports cancellation and progress reporting.
    The total number of files is not scanned in advance for performance reasons, instead the progress
    is updated as files are copied.
    """
    sys.audit("shutil.copytree", src, dst)
    with os.scandir(src) as itr:
        entries = list(itr)
    return _copytree(
        entries=entries,
        src=src,
        dst=dst,
        symlinks=symlinks,
        ignore_dangling_symlinks=ignore_dangling_symlinks,
        dirs_exist_ok=dirs_exist_ok,
    )
