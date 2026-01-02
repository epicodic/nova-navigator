from dataclasses import dataclass
from typing import Literal

from nova_navigator.vfs2.types import Stat

from . import InteractionContext, VPath

CHUNK_SIZE = 64 * 1024  # 64 KB


OverwritePolicy = Literal["overwrite", "skip", "ask"]


@dataclass
class FileCopyOptions:
    overwrite: OverwritePolicy = "ask"


def copy_file(
    ctx: InteractionContext, src_path: VPath, dst_path: VPath, options: FileCopyOptions | None = None
) -> None:
    if options is None:
        options = FileCopyOptions()

    try:
        reader = None
        writer = None
        src_stat = src_path.stat
        reader = src_path.filesystem.read(src_path)

        if options.overwrite != "overwrite":
            dst_stat = dst_path.stat_or_none
            if dst_stat is not None:
                if options.overwrite == "skip":
                    ctx.set_completed()
                    return
                elif options.overwrite == "ask":
                    raise FileExistsError(f"Destination file {dst_path} already exists.")

        writer = dst_path.filesystem.write(dst_path)

        ctx.set_progress(0, src_stat.size)

        while True:
            ctx.check_cancelled()
            chunk = reader.read(CHUNK_SIZE)
            if not chunk:
                break
            writer.write(chunk)
            ctx.update_progress(inc_completed=len(chunk))

        ctx.set_completed()
    finally:
        if reader:
            reader.close()
        if writer:
            writer.close()
