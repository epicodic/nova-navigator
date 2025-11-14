import os
import shutil
import threading
import time

from . import LocalFilesystem, VPath
from .file_copy import ProgressCallback, copy_files_and_directories, copyfile


def move_or_copy_files(
    source_paths: list[VPath],
    destination_path: VPath,
    cancel_event: threading.Event,
    progress_callback: ProgressCallback,
    move: bool = False,
) -> None:
    if move:
        for source_path in source_paths:
            assert isinstance(destination_path.filesystem, LocalFilesystem)
            assert isinstance(source_path.filesystem, LocalFilesystem)
            time.sleep(1.0)  # TODO: test delay

            dest_file_path = destination_path.path / source_path.name
            shutil.move(str(source_path.path), str(dest_file_path))

    else:
        if len(source_paths) == 1 and not source_paths[0].stats.is_directory:
            dest_file_path = destination_path.path / source_paths[0].name

            copyfile(
                str(source_paths[0].path),
                str(dest_file_path),
                cancel_event=cancel_event,
                progress_callback=progress_callback,
            )

        else:
            copy_files_and_directories(
                [str(source_path.path) for source_path in source_paths],
                str(destination_path.path),
                dirs_exist_ok=True,
                cancel_event=cancel_event,
                progress_callback=progress_callback,
            )


def delete_files(
    paths: list[VPath],
) -> None:
    for path in paths:
        assert isinstance(path.filesystem, LocalFilesystem)
        path_str = path.path.as_posix()
        if not os.path.exists(path_str) and not os.path.islink(path_str):
            return  # Nothing to delete

        # If path is a symlink (to file or directory)
        if os.path.islink(path_str):
            os.unlink(path_str)

        # If path is a file
        elif os.path.isfile(path_str):
            os.remove(path_str)

        # If path is a directory
        elif os.path.isdir(path_str):
            shutil.rmtree(path_str)
