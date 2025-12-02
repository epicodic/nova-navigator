"""Tests for MockFilesystem itself."""

import pytest

from tests.mock_filesystem import MockFilesystem

# ---------------------------------------------------------------------------
# Construction
# ---------------------------------------------------------------------------


def test_default_dirs_exist() -> None:
    fs = MockFilesystem()
    assert fs.root().stat.is_directory
    assert fs.home().stat.is_directory
    assert fs.cwd().stat.is_directory


def test_custom_root_home_cwd() -> None:
    fs = MockFilesystem(root="/r", home="/r/h", cwd="/r/h/w")
    assert str(fs.root().path) == "/r"
    assert str(fs.home().path) == "/r/h"
    assert str(fs.cwd().path) == "/r/h/w"


def test_files_created_with_correct_content() -> None:
    fs = MockFilesystem({"/a/b.txt": b"hello"})
    reader = fs.read(fs.path("/a/b.txt"))
    assert reader.read(100) == b"hello"
    reader.close()


def test_parent_dirs_created_implicitly() -> None:
    fs = MockFilesystem({"/a/b/c/file.txt": b"x"})
    for p in ["/a", "/a/b", "/a/b/c"]:
        assert fs.path(p).stat.is_directory


def test_explicit_none_creates_directory() -> None:
    fs = MockFilesystem({"/mydir": None})
    assert fs.path("/mydir").stat.is_directory


# ---------------------------------------------------------------------------
# stat()
# ---------------------------------------------------------------------------


def test_stat_file() -> None:
    fs = MockFilesystem({"/f.txt": b"abc"})
    s = fs.path("/f.txt").stat
    assert s.size == 3
    assert not s.is_directory


def test_stat_directory() -> None:
    fs = MockFilesystem({"/d": None})
    s = fs.path("/d").stat
    assert s.is_directory


def test_stat_hidden_file() -> None:
    fs = MockFilesystem({"/home/user/.hidden": b""})
    assert fs.path("/home/user/.hidden").stat.is_hidden


def test_stat_non_hidden_file() -> None:
    fs = MockFilesystem({"/home/user/visible.txt": b""})
    assert not fs.path("/home/user/visible.txt").stat.is_hidden


def test_stat_missing_raises() -> None:
    fs = MockFilesystem()
    with pytest.raises(FileNotFoundError):
        fs.path("/nonexistent").stat  # noqa: B018


# ---------------------------------------------------------------------------
# iterdir()
# ---------------------------------------------------------------------------


def test_iterdir_empty() -> None:
    fs = MockFilesystem({"/empty": None})
    assert fs.path("/empty").iterdir() == []


def test_iterdir_returns_direct_children_only() -> None:
    fs = MockFilesystem(
        {
            "/d/a.txt": b"a",
            "/d/sub/b.txt": b"b",
        }
    )
    children = {str(p.path) for p in fs.path("/d").iterdir()}
    assert children == {"/d/a.txt", "/d/sub"}


def test_iterdir_on_file_raises() -> None:
    fs = MockFilesystem({"/f.txt": b"x"})
    with pytest.raises(NotADirectoryError):
        fs.path("/f.txt").iterdir()


# ---------------------------------------------------------------------------
# read()
# ---------------------------------------------------------------------------


def test_read_returns_full_content() -> None:
    fs = MockFilesystem({"/f.txt": b"hello world"})
    reader = fs.read(fs.path("/f.txt"))
    assert reader.read(1024) == b"hello world"
    reader.close()


def test_read_sequential_chunks() -> None:
    fs = MockFilesystem({"/f.txt": b"abcdef"})
    reader = fs.read(fs.path("/f.txt"))
    assert reader.read(3) == b"abc"
    assert reader.read(3) == b"def"
    assert reader.read(3) == b""
    reader.close()


def test_read_tracks_reader() -> None:
    fs = MockFilesystem({"/f.txt": b"x"})
    assert len(fs.readers) == 0
    reader = fs.read(fs.path("/f.txt"))
    assert len(fs.readers) == 1
    assert fs.readers[0] is reader


def test_read_close_count() -> None:
    fs = MockFilesystem({"/f.txt": b"x"})
    reader = fs.read(fs.path("/f.txt"))
    assert reader.close_count == 0
    reader.close()
    assert reader.close_count == 1
    reader.close()
    assert reader.close_count == 2


def test_read_error_injection() -> None:
    fs = MockFilesystem(
        {"/f.txt": b"data"},
        read_errors={"/f.txt": OSError("injected error")},
    )
    reader = fs.read(fs.path("/f.txt"))
    with pytest.raises(OSError, match="injected error"):
        reader.read(10)
    reader.close()
    assert reader.close_count == 1


def test_read_on_directory_raises() -> None:
    fs = MockFilesystem({"/d": None})
    with pytest.raises(IsADirectoryError):
        fs.read(fs.path("/d"))


def test_read_on_missing_raises() -> None:
    fs = MockFilesystem()
    with pytest.raises(FileNotFoundError):
        fs.read(fs.path("/missing.txt"))


# ---------------------------------------------------------------------------
# write()
# ---------------------------------------------------------------------------


def test_write_file_exists_immediately() -> None:
    fs = MockFilesystem()
    writer = fs.write(fs.path("/home/user/new.txt"))
    assert not fs.path("/home/user/new.txt").stat.is_directory
    writer.close()


def test_write_content_readable_after_close() -> None:
    fs = MockFilesystem()
    writer = fs.write(fs.path("/home/user/out.txt"))
    writer.write(b"foo")
    writer.write(b"bar")
    writer.close()

    reader = fs.read(fs.path("/home/user/out.txt"))
    assert reader.read(1024) == b"foobar"
    reader.close()


def test_write_content_visible_before_close() -> None:
    """Written bytes are live in the node immediately, not only after close."""
    fs = MockFilesystem()
    writer = fs.write(fs.path("/home/user/live.txt"))
    writer.write(b"live")

    reader = fs.read(fs.path("/home/user/live.txt"))
    assert reader.read(1024) == b"live"
    reader.close()
    writer.close()


def test_write_tracks_writer() -> None:
    fs = MockFilesystem()
    assert len(fs.writers) == 0
    writer = fs.write(fs.path("/home/user/f.txt"))
    assert len(fs.writers) == 1
    assert fs.writers[0] is writer
    writer.close()


def test_write_close_count() -> None:
    fs = MockFilesystem()
    writer = fs.write(fs.path("/home/user/f.txt"))
    assert writer.close_count == 0
    writer.close()
    assert writer.close_count == 1


def test_write_missing_parent_raises() -> None:
    fs = MockFilesystem()
    with pytest.raises((FileNotFoundError, NotADirectoryError)):
        fs.write(fs.path("/no/such/parent/f.txt"))


def test_write_overwrites_existing_file() -> None:
    fs = MockFilesystem({"/home/user/f.txt": b"old"})
    writer = fs.write(fs.path("/home/user/f.txt"))
    writer.write(b"new")
    writer.close()

    reader = fs.read(fs.path("/home/user/f.txt"))
    assert reader.read(1024) == b"new"
    reader.close()


# ---------------------------------------------------------------------------
# remove()
# ---------------------------------------------------------------------------


def test_remove_deletes_file() -> None:
    fs = MockFilesystem({"/home/user/f.txt": b"x"})
    fs.remove(fs.path("/home/user/f.txt"))
    with pytest.raises(FileNotFoundError):
        fs.path("/home/user/f.txt").stat  # noqa: B018


def test_remove_missing_raises() -> None:
    fs = MockFilesystem()
    with pytest.raises(FileNotFoundError):
        fs.remove(fs.path("/home/user/missing.txt"))


def test_remove_directory_raises() -> None:
    fs = MockFilesystem({"/home/user/d": None})
    with pytest.raises(IsADirectoryError):
        fs.remove(fs.path("/home/user/d"))


# ---------------------------------------------------------------------------
# rmdir()
# ---------------------------------------------------------------------------


def test_rmdir_removes_empty_directory() -> None:
    fs = MockFilesystem({"/home/user/empty": None})
    fs.rmdir(fs.path("/home/user/empty"))
    with pytest.raises(FileNotFoundError):
        fs.path("/home/user/empty").stat  # noqa: B018


def test_rmdir_non_empty_raises() -> None:
    fs = MockFilesystem({"/home/user/d/f.txt": b"x"})
    with pytest.raises(OSError, match="not empty"):
        fs.rmdir(fs.path("/home/user/d"))


def test_rmdir_on_file_raises() -> None:
    fs = MockFilesystem({"/home/user/f.txt": b"x"})
    with pytest.raises(NotADirectoryError):
        fs.rmdir(fs.path("/home/user/f.txt"))


# ---------------------------------------------------------------------------
# parent()
# ---------------------------------------------------------------------------


def test_parent_of_file() -> None:
    fs = MockFilesystem({"/a/b/f.txt": b"x"})
    assert str(fs.path("/a/b/f.txt").parent.path) == "/a/b"


def test_parent_of_root() -> None:
    fs = MockFilesystem()
    assert str(fs.root().parent.path) == "/"


# ---------------------------------------------------------------------------
# Additional / cross-cutting
# ---------------------------------------------------------------------------


def test_stat_size_reflects_bytes_written_before_close() -> None:
    """stat.size is live: it reflects bytes written even before close()."""
    fs = MockFilesystem()
    writer = fs.write(fs.path("/home/user/f.txt"))
    writer.write(b"hello")
    assert fs.path("/home/user/f.txt").stat.size == 5
    writer.write(b"!!")
    assert fs.path("/home/user/f.txt").stat.size == 7
    writer.close()


def test_multiple_readers_have_independent_positions() -> None:
    fs = MockFilesystem({"/f.txt": b"abcdef"})
    r1 = fs.read(fs.path("/f.txt"))
    r2 = fs.read(fs.path("/f.txt"))
    assert r1.read(3) == b"abc"
    assert r2.read(3) == b"abc"  # r2 unaffected by r1's advance
    assert r1.read(3) == b"def"
    r1.close()
    r2.close()


def test_vpath_from_wrong_filesystem_raises() -> None:
    fs1 = MockFilesystem({"/f.txt": b"x"})
    fs2 = MockFilesystem()
    foreign = fs1.path("/f.txt")
    with pytest.raises(ValueError, match="does not belong"):
        fs2.stat(foreign)


def test_iterdir_on_missing_path_raises() -> None:
    fs = MockFilesystem()
    with pytest.raises(FileNotFoundError):
        fs.path("/no/such/dir").iterdir()


def test_read_errors_only_affect_specified_path() -> None:
    fs = MockFilesystem(
        {"/bad.txt": b"bad", "/good.txt": b"good"},
        read_errors={"/bad.txt": OSError("injected")},
    )
    reader = fs.read(fs.path("/good.txt"))
    assert reader.read(1024) == b"good"
    reader.close()
