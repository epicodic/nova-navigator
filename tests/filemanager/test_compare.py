"""Unit tests for compare_directories()."""

from __future__ import annotations

from pathlib import PurePosixPath

from nova_navigator.filemanager.compare import (
    DIFFERENT_COLOR,
    LEFT_ONLY_COLOR,
    RIGHT_ONLY_COLOR,
    CompareMode,
    compare_directories,
)
from nova_navigator.vfs.vpath import VPath
from tests._utils.mock_filesystem import MockFilesystem, _FileNode  # type: ignore[attr-defined]


def _fs(*paths: str) -> MockFilesystem:
    """Create a MockFilesystem with files at the given absolute paths (empty content)."""
    return MockFilesystem(dict.fromkeys(paths, b""))


def _vpath(fs: MockFilesystem, path_str: str) -> VPath:
    """Return a VPath with stat pre-populated."""
    vp = VPath(PurePosixPath(path_str), fs)
    vp._stat = fs.stat(vp)
    return vp


def _set_size(fs: MockFilesystem, path_str: str, size: int) -> None:
    """Resize the file at path_str to exactly *size* bytes."""
    node = fs._nodes[PurePosixPath(path_str)]
    assert isinstance(node, _FileNode)
    node.content = bytearray(size)


def _set_mtime(fs: MockFilesystem, path_str: str, mtime: float) -> None:
    """Set the modification timestamp of the file at path_str."""
    node = fs._nodes[PurePosixPath(path_str)]
    assert isinstance(node, _FileNode)
    node.modified = mtime


# ---------------------------------------------------------------------------
# Presence-only tests (mode=None)
# ---------------------------------------------------------------------------


def test_file_only_in_left_gets_left_only_color() -> None:
    left_fs = _fs("/dir/alpha.txt")
    left_items = [_vpath(left_fs, "/dir/alpha.txt")]
    right_items: list[VPath] = []

    left_colors, right_colors = compare_directories(left_items, right_items, mode=None)

    assert left_colors == {"alpha.txt": LEFT_ONLY_COLOR}
    assert right_colors == {}


def test_file_only_in_right_gets_right_only_color() -> None:
    right_fs = _fs("/dir/beta.txt")
    left_items: list[VPath] = []
    right_items = [_vpath(right_fs, "/dir/beta.txt")]

    left_colors, right_colors = compare_directories(left_items, right_items, mode=None)

    assert left_colors == {}
    assert right_colors == {"beta.txt": RIGHT_ONLY_COLOR}


def test_file_in_both_with_no_mode_gets_no_color() -> None:
    left_fs = _fs("/dir/gamma.txt")
    right_fs = _fs("/dir/gamma.txt")
    left_items = [_vpath(left_fs, "/dir/gamma.txt")]
    right_items = [_vpath(right_fs, "/dir/gamma.txt")]

    left_colors, right_colors = compare_directories(left_items, right_items, mode=None)

    assert left_colors == {}
    assert right_colors == {}


# ---------------------------------------------------------------------------
# By-size tests
# ---------------------------------------------------------------------------


def test_same_name_different_size_both_get_different_color() -> None:
    left_fs = _fs("/dir/data.bin")
    right_fs = _fs("/dir/data.bin")
    _set_size(left_fs, "/dir/data.bin", 100)
    _set_size(right_fs, "/dir/data.bin", 200)
    left_items = [_vpath(left_fs, "/dir/data.bin")]
    right_items = [_vpath(right_fs, "/dir/data.bin")]

    left_colors, right_colors = compare_directories(left_items, right_items, mode=CompareMode.BY_SIZE)

    assert left_colors == {"data.bin": DIFFERENT_COLOR}
    assert right_colors == {"data.bin": DIFFERENT_COLOR}


def test_same_name_same_size_gets_no_color() -> None:
    left_fs = _fs("/dir/data.bin")
    right_fs = _fs("/dir/data.bin")
    _set_size(left_fs, "/dir/data.bin", 42)
    _set_size(right_fs, "/dir/data.bin", 42)
    left_items = [_vpath(left_fs, "/dir/data.bin")]
    right_items = [_vpath(right_fs, "/dir/data.bin")]

    left_colors, right_colors = compare_directories(left_items, right_items, mode=CompareMode.BY_SIZE)

    assert left_colors == {}
    assert right_colors == {}


def test_unknown_size_falls_back_to_no_color() -> None:
    """When size == -1 on either side, treat as equal."""
    from nova_navigator.vfs.types import Stat

    left_fs = _fs("/dir/x.bin")
    right_fs = _fs("/dir/x.bin")
    left_vp = _vpath(left_fs, "/dir/x.bin")
    right_vp = _vpath(right_fs, "/dir/x.bin")
    left_vp._stat = Stat(size=-1)
    right_vp._stat = Stat(size=99)

    left_colors, right_colors = compare_directories([left_vp], [right_vp], mode=CompareMode.BY_SIZE)

    assert left_colors == {}
    assert right_colors == {}


# ---------------------------------------------------------------------------
# By-mtime tests
# ---------------------------------------------------------------------------


def test_same_name_different_mtime_both_get_different_color() -> None:
    left_fs = _fs("/dir/log.txt")
    right_fs = _fs("/dir/log.txt")
    _set_mtime(left_fs, "/dir/log.txt", 1000.0)
    _set_mtime(right_fs, "/dir/log.txt", 2000.0)
    left_items = [_vpath(left_fs, "/dir/log.txt")]
    right_items = [_vpath(right_fs, "/dir/log.txt")]

    left_colors, right_colors = compare_directories(left_items, right_items, mode=CompareMode.BY_MODIFICATION_TIME)

    assert left_colors == {"log.txt": DIFFERENT_COLOR}
    assert right_colors == {"log.txt": DIFFERENT_COLOR}


def test_same_name_same_mtime_gets_no_color() -> None:
    left_fs = _fs("/dir/log.txt")
    right_fs = _fs("/dir/log.txt")
    _set_mtime(left_fs, "/dir/log.txt", 5000.0)
    _set_mtime(right_fs, "/dir/log.txt", 5000.0)
    left_items = [_vpath(left_fs, "/dir/log.txt")]
    right_items = [_vpath(right_fs, "/dir/log.txt")]

    left_colors, right_colors = compare_directories(left_items, right_items, mode=CompareMode.BY_MODIFICATION_TIME)

    assert left_colors == {}
    assert right_colors == {}


def test_unknown_mtime_falls_back_to_no_color() -> None:
    from nova_navigator.vfs.types import Stat

    left_fs = _fs("/dir/x.txt")
    right_fs = _fs("/dir/x.txt")
    left_vp = _vpath(left_fs, "/dir/x.txt")
    right_vp = _vpath(right_fs, "/dir/x.txt")
    left_vp._stat = Stat(modified=-1.0)
    right_vp._stat = Stat(modified=9999.0)

    left_colors, right_colors = compare_directories([left_vp], [right_vp], mode=CompareMode.BY_MODIFICATION_TIME)

    assert left_colors == {}
    assert right_colors == {}


# ---------------------------------------------------------------------------
# Directory tests
# ---------------------------------------------------------------------------


def test_directory_in_both_never_gets_different_color_even_with_mode() -> None:
    left_fs = MockFilesystem({"/dir/subdir": None})
    right_fs = MockFilesystem({"/dir/subdir": None})
    left_items = [_vpath(left_fs, "/dir/subdir")]
    right_items = [_vpath(right_fs, "/dir/subdir")]

    for mode in (CompareMode.BY_SIZE, CompareMode.BY_MODIFICATION_TIME):
        left_colors, right_colors = compare_directories(left_items, right_items, mode=mode)
        assert left_colors == {}, f"Expected no color for left dir with mode={mode}"
        assert right_colors == {}, f"Expected no color for right dir with mode={mode}"


def test_directory_only_in_left_gets_left_only_color() -> None:
    left_fs = MockFilesystem({"/dir/subdir": None})
    left_items = [_vpath(left_fs, "/dir/subdir")]
    right_items: list[VPath] = []

    left_colors, right_colors = compare_directories(left_items, right_items, mode=None)

    assert left_colors == {"subdir": LEFT_ONLY_COLOR}
    assert right_colors == {}


# ---------------------------------------------------------------------------
# Mixed listing
# ---------------------------------------------------------------------------


def test_mixed_listing_returns_correct_colors_for_all_entries() -> None:
    left_fs = _fs("/dir/common.txt", "/dir/left_only.txt")
    right_fs = _fs("/dir/common.txt", "/dir/right_only.txt")
    _set_size(left_fs, "/dir/common.txt", 10)
    _set_size(right_fs, "/dir/common.txt", 20)

    left_items = [_vpath(left_fs, "/dir/common.txt"), _vpath(left_fs, "/dir/left_only.txt")]
    right_items = [_vpath(right_fs, "/dir/common.txt"), _vpath(right_fs, "/dir/right_only.txt")]

    left_colors, right_colors = compare_directories(left_items, right_items, mode=CompareMode.BY_SIZE)

    assert left_colors == {"common.txt": DIFFERENT_COLOR, "left_only.txt": LEFT_ONLY_COLOR}
    assert right_colors == {"common.txt": DIFFERENT_COLOR, "right_only.txt": RIGHT_ONLY_COLOR}
