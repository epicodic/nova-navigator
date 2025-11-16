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


def erase(status: TaskStatus, paths: list[VPath]) -> Task:
    status.set_progress(0, len(paths))
    for path in paths:
        status.check_cancelled()

        if path.stat.is_directory and len(path.iterdir()) > 0:
            decision = yield DecisionRequest(
                "Directory '{path}' is not empty. Delete it recursively?",
                path=path.path,
            )
            if decision.is_no:
                continue

        path.filesystem.remove(path)
        status.update_progress(inc_completed=1)

    status.set_completed()


OverwritePolicy = Literal["overwrite", "skip", "ask"]


@dataclass
class FileCopyOptions:
    overwrite: OverwritePolicy = "ask"


def copy_file(status: TaskStatus, src_path: VPath, dst_path: VPath, options: FileCopyOptions | None = None) -> Task:
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
