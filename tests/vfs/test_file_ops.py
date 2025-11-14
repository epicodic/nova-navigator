from nova_navigator.vfs2 import InteractionContext, VPath
from nova_navigator.vfs2.file_ops import copy_file
from nova_navigator.vfs2.filesystems import LocalFilesystem, SSHFilesystem
from tests.test_utils import (
    DATA_DIR,
    check_file,
    temporary_directory,
)


def test_copy_file(interaction_context_mock: InteractionContext) -> None:
    ctx = interaction_context_mock

    fs = LocalFilesystem.singleton()

    with temporary_directory() as temp_dir:
        dst_file_path = temp_dir / "copied_file.txt"

        src_vpath = VPath(DATA_DIR / "local" / "dir1" / "file11.txt", fs)
        dst_vpath = VPath(dst_file_path, fs)

        copy_file(ctx, src_vpath, dst_vpath)
        check_file(dst_file_path, "this is file11.txt\n")

        copy_file(ctx, src_vpath, dst_vpath)


def test_copy_file_ssh(interaction_context_mock: InteractionContext) -> None:
    ctx = interaction_context_mock

    local_fs = LocalFilesystem.singleton()
    ssh_fs = SSHFilesystem("localhost")

    with temporary_directory() as temp_dir:
        dst_file_path = temp_dir / "copied_file.txt"

        src_vpath = VPath(DATA_DIR / "local" / "dir1" / "file11.txt", ssh_fs)
        dst_vpath = VPath(dst_file_path, local_fs)

        copy_file(ctx, src_vpath, dst_vpath)
        check_file(dst_file_path, "this is file11.txt\n")

        copy_file(ctx, src_vpath, dst_vpath)
