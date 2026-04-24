from pathlib import Path

from nova_navigator.archive import tar_archive, zip_archive
from tests.test_utils import DATA_DIR


def test_listdir_tar_archive() -> None:
    # Create a sample TAR archive for testing
    tar_path = DATA_DIR / "test_archive.tar.gz"
    # Test listing contents of the TAR archive
    a = tar_archive.TarArchive(archive_path=tar_path, mode="r")

    # contents = tar_archive.listdir(Path("dir2/dir21"))
    # print(contents)
    for entry in a._members:
        print(entry.isdir())


def test_listdir_zip_archive() -> None:
    # Create a sample ZIP archive for testing
    zip_path = DATA_DIR / "test_archive.zip"
    # Test listing contents of the ZIP archive
    a = zip_archive.ZipArchive(archive_path=zip_path, mode="r")

    contents = a.listdir(Path("dir2/dir21"))
    print(contents)
    for entry in a._members:
        print(entry)
