import os

from nova_navigator.vfs.file_copy import copyfile, copytree
from tests.test_utils import check_file, create_file, temporary_directory


def test_copyfile() -> None:
    with temporary_directory() as tmp_dir:
        src_file = tmp_dir / "source.txt"
        dst_file = tmp_dir / "destination.txt"

        create_file(src_file, "Hello, World!")

        copyfile(src_file, dst_file)

        check_file(dst_file, "Hello, World!")


def test_copytree_trivial() -> None:
    with temporary_directory() as src_dir, temporary_directory() as dst_dir:
        # Create source directory structure
        create_file(src_dir / "test.txt", "123")
        create_file(src_dir / "test2.txt", "456")
        create_file(src_dir / "subdir" / "test.txt", "789")

        # Perform copy
        target_dir = dst_dir / "copied"
        copytree(src_dir, target_dir)

        # Verify files
        check_file(target_dir / "test.txt", "123")
        check_file(target_dir / "test2.txt", "456")
        check_file(target_dir / "subdir" / "test.txt", "789")


def test_copytree_dirs_exist_ok() -> None:
    with temporary_directory() as src_dir, temporary_directory() as dst_dir:
        # Create source directory structure
        create_file(src_dir / "nonexisting.txt", "123")
        create_file(src_dir / "existing_dir" / "existing.txt", "has been replaced")
        create_file(dst_dir / "existing_dir" / "existing.txt", "will be replaced")
        create_file(dst_dir / "existing_dir" / "other_existing.txt", "will not be replaced")

        copytree(src_dir, dst_dir, dirs_exist_ok=True)

        check_file(dst_dir / "nonexisting.txt", "123")
        check_file(dst_dir / "existing_dir" / "existing.txt", "has been replaced")
        check_file(dst_dir / "existing_dir" / "other_existing.txt", "will not be replaced")


def test_copytree_symlink() -> None:
    with temporary_directory() as tmp_dir:
        src_dir = tmp_dir / "src"
        dst_dir = tmp_dir / "dst"
        sub_dir = src_dir / "sub"

        os.makedirs(sub_dir)
        create_file(src_dir / "file.txt", "data")
        src_link = sub_dir / "link"
        os.symlink(src_dir / "file.txt", src_link)

        copytree(src_dir, dst_dir, symlinks=True)

        os.path.islink(dst_dir / "sub" / "link")
        actual = os.readlink(dst_dir / "sub" / "link")
        assert actual == str(src_dir / "file.txt")
