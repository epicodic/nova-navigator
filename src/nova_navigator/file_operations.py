from .dialogs import CopyMoveFilesDialog, DefaultButton, DeleteFilesDialog
from .operation import Operation
from .vfs import VPath
from .vfs.operations import delete_files, move_or_copy_files


class CopyOrMoveOperation(Operation):
    def __init__(self, source_paths: list[VPath], destination_path: VPath, move: bool = False) -> None:
        super().__init__()
        self.source_paths = source_paths
        self.destination_path = destination_path
        self.move = move

    def _runner(self) -> None:
        move_or_copy_files(
            source_paths=self.source_paths,
            destination_path=self.destination_path,
            move=self.move,
        )


async def copy_or_move_files_operation(
    source_paths: list[VPath],
    destination_path: VPath,
    move: bool = False,
) -> CopyOrMoveOperation | None:
    """Copy or move files from source paths to destination path.

    Args:
        source_paths (list[VFSPath]): List of source file paths.
        destination_path (VFSPath): Destination directory path.
        move (bool, optional): If True, move files instead of copying. Defaults to False.
    """
    dialog = CopyMoveFilesDialog(
        source_paths=source_paths,
        destination_path=destination_path,
        move=move,
    )

    result = await dialog.run()
    if result != DefaultButton.OK:
        return None

    return await CopyOrMoveOperation(
        source_paths=source_paths,
        destination_path=destination_path,
        move=move,
    ).start()


class DeleteOperation(Operation):
    def __init__(self, paths: list[VPath]) -> None:
        super().__init__()
        self.paths = paths

    def _runner(self) -> None:
        delete_files(self.paths)


async def delete_files_operation(
    paths: list[VPath],
) -> DeleteOperation | None:
    """Delete files at given paths.

    Args:
        paths (list[VFSPath]): List of file paths.
    """
    dialog = DeleteFilesDialog(
        paths=[path.compact_path_str for path in paths],
    )

    result = await dialog.run()
    if result != DefaultButton.YES:
        return None

    return await DeleteOperation(
        paths=paths,
    ).start()
