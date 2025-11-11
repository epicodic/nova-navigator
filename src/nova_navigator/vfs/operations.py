import os
import shutil
import time

from . import LocalFilesystem, VPath


def move_or_copy_files(
    source_paths: list[VPath],
    destination_path: VPath,
    move: bool = False,
) -> None:
    for source_path in source_paths:
        assert isinstance(destination_path.filesystem, LocalFilesystem)
        assert isinstance(source_path.filesystem, LocalFilesystem)
        time.sleep(1.0)  # TODO: test delay

        dest_file_path = destination_path.path / source_path.name
        if move:
            shutil.move(str(source_path.path), str(dest_file_path))
        # copy
        elif source_path.stats.is_directory:
            shutil.copytree(str(source_path.path), str(dest_file_path))
        else:
            shutil.copy2(str(source_path.path), str(dest_file_path))


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
