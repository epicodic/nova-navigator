from dataclasses import dataclass
from typing import Literal

from nova_navigator.task import DecisionRequest, Task, TaskStatus

from . import VPath

CHUNK_SIZE = 64 * 1024  # 64 KB


OverwritePolicy = Literal["overwrite", "skip", "ask"]


@dataclass
class FileCopyOptions:
    overwrite: OverwritePolicy = "ask"


def copy_file(status: TaskStatus, src_path: VPath, dst_path: VPath, options: FileCopyOptions | None = None) -> Task:
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
                # destination file exists
                if options.overwrite == "skip":
                    status.set_completed()
                    return
                elif options.overwrite == "ask":
                    decision = yield DecisionRequest("File '{dst}' already exists. Overwrite?", {"dst": dst_path.path})
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
