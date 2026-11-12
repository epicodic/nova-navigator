"""TarArchive symlink members: readlink, followed stats, read and real_path."""

from __future__ import annotations

import errno
from pathlib import Path, PurePath, PurePosixPath

import pytest

from nova_navigator.archive.tar_archive import TarArchive
from tests._utils.tar_fixtures import build_tar_with_symlink_tree


@pytest.fixture
def tar(tmp_path: Path) -> TarArchive:
    build_tar_with_symlink_tree(tmp_path / "s.tar.gz")
    return TarArchive(tmp_path / "s.tar.gz", mode="r")


def test_readlink_returns_stored_text(tar: TarArchive) -> None:
    assert tar.readlink(PurePath("/lnk")) == "real"
    assert tar.readlink(PurePath("/abs")) == "/etc/passwd"


def test_readlink_on_regular_member_raises_einval(tar: TarArchive) -> None:
    with pytest.raises(OSError, match="Not a symbolic link") as excinfo:
        tar.readlink(PurePath("/real/f.txt"))
    assert excinfo.value.errno == errno.EINVAL


def test_stats_file_link_reports_target_size(tar: TarArchive) -> None:
    st = tar.stats(PurePath("/flnk"))
    assert st.is_symlink
    assert not st.is_broken_symlink
    assert not st.is_directory
    assert st.size == 5


def test_stats_directory_link_is_directory(tar: TarArchive) -> None:
    st = tar.stats(PurePath("/lnk"))
    assert st.is_symlink
    assert st.is_directory
    assert not st.is_broken_symlink


def test_stats_link_to_root_is_directory(tar: TarArchive) -> None:
    st = tar.stats(PurePath("/real/up"))
    assert st.is_symlink
    assert st.is_directory


@pytest.mark.parametrize("name", ["broken", "abs", "esc", "loop1"])
def test_stats_unresolvable_links_are_broken(tar: TarArchive, name: str) -> None:
    st = tar.stats(PurePath("/" + name))
    assert st.is_symlink
    assert st.is_broken_symlink
    assert not st.is_directory


def test_read_file_link_returns_target_content(tar: TarArchive) -> None:
    reader = tar.read(PurePath("/flnk"))
    try:
        assert reader.read(100) == b"hello"
    finally:
        reader.close()


def test_read_directory_link_raises_is_a_directory(tar: TarArchive) -> None:
    with pytest.raises(IsADirectoryError):
        tar.read(PurePath("/lnk"))


def test_read_broken_link_raises_file_not_found(tar: TarArchive) -> None:
    with pytest.raises(FileNotFoundError):
        tar.read(PurePath("/broken"))


def test_real_path_resolves_members(tar: TarArchive) -> None:
    assert tar.real_path(PurePath("/lnk")) == PurePosixPath("/real")
    assert tar.real_path(PurePath("/flnk")) == PurePosixPath("/real/f.txt")
    assert tar.real_path(PurePath("/lnk/f.txt")) == PurePosixPath("/real/f.txt")
    assert tar.real_path(PurePath("/real/up")) == PurePosixPath("/")
    assert tar.real_path(PurePath("/real")) == PurePosixPath("/real")


@pytest.mark.parametrize("name", ["broken", "abs", "esc", "loop1"])
def test_real_path_unresolvable_raises_file_not_found(tar: TarArchive, name: str) -> None:
    with pytest.raises(FileNotFoundError):
        tar.real_path(PurePath("/" + name))
