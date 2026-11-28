"""Zip members report the modification time recorded in the archive."""

import os
import zipfile
from datetime import datetime
from pathlib import Path

import pytest

from nova_navigator.archive.backing import LocalArchiveBacking
from nova_navigator.vfs.filesystems.archive import ArchiveFilesystem
from nova_navigator.vfs.filesystems.local import LocalFilesystem
from nova_navigator.widgets.directory_browser import column_formatter_modified, column_sorter_modified

_OLD = (2001, 2, 3, 4, 5, 7)  # odd second: the format stores 6
_NEW = (2020, 12, 31, 23, 59, 58)
_DIR = (2010, 6, 15, 12, 0, 0)


def _mount(tmp_path: Path) -> ArchiveFilesystem:
    archive = tmp_path / "a.zip"
    with zipfile.ZipFile(archive, "w") as zf:
        for name, date_time in (
            ("new.txt", _NEW),
            ("old.txt", _OLD),
            ("dir/", _DIR),
            ("implicit/inner.txt", _NEW),
            ("bad_date.txt", (2021, 0, 0, 0, 0, 0)),
            ("bad_time.txt", (2021, 1, 1, 31, 0, 0)),
        ):
            zf.writestr(zipfile.ZipInfo(name, date_time=date_time), b"")
    local = LocalFilesystem.singleton()
    return ArchiveFilesystem(local.path(tmp_path), LocalArchiveBacking(local.path(archive)), work_dir=tmp_path / "work")


@pytest.mark.parametrize(("name", "recorded"), [("old.txt", _OLD), ("new.txt", _NEW), ("dir", _DIR)])
def test_zip_times_are_shown_like_local_files(tmp_path: Path, name: str, recorded: tuple[int, int, int, int, int, int]) -> None:
    member = _mount(tmp_path).root() / name
    expected = datetime(*recorded).timestamp()
    assert abs(member.stat.modified - expected) < 2

    local_file = tmp_path / "local"
    local_file.write_bytes(b"")
    os.utime(local_file, (member.stat.modified, member.stat.modified))
    assert column_formatter_modified(member) == column_formatter_modified(LocalFilesystem.singleton().path(local_file))
    assert column_formatter_modified(member) != ""


def test_zip_times_sort_in_recorded_order(tmp_path: Path) -> None:
    root = _mount(tmp_path).root()
    files = [root / "new.txt", root / "old.txt"]
    assert [p.name for p in sorted(files, key=column_sorter_modified)] == ["old.txt", "new.txt"]


@pytest.mark.asyncio
@pytest.mark.parametrize("name", ["bad_date.txt", "bad_time.txt"])
async def test_entry_with_invalid_date_is_listed_without_time(tmp_path: Path, name: str) -> None:
    root = _mount(tmp_path).root()
    listed = {child.name: child async for child in root.iterdir()}
    assert listed[name].stat.modified == -1.0
    assert column_formatter_modified(listed[name]) == ""


def test_directories_without_own_entry_have_no_time(tmp_path: Path) -> None:
    root = _mount(tmp_path).root()
    assert root.stat.modified == -1.0
    assert (root / "implicit").stat.modified == -1.0
