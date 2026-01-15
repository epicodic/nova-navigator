"""Tests for MockFilesystem itself."""

import pytest

from .mock_filesystem import MockFilesystem

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


# ---------------------------------------------------------------------------
# rename()
# ---------------------------------------------------------------------------


def test_rename_file_in_same_directory() -> None:
    fs = MockFilesystem({"/home/user/old.txt": b"content"})
    fs.rename(fs.path("/home/user/old.txt"), fs.path("/home/user/new.txt"))

    # Old path should not exist
    with pytest.raises(FileNotFoundError):
        fs.path("/home/user/old.txt").stat  # noqa: B018

    # New path should have the content
    reader = fs.read(fs.path("/home/user/new.txt"))
    assert reader.read(1024) == b"content"
    reader.close()


def test_rename_file_to_different_directory() -> None:
    fs = MockFilesystem(
        {
            "/home/user/file.txt": b"data",
            "/home/other": None,
        }
    )
    fs.rename(fs.path("/home/user/file.txt"), fs.path("/home/other/moved.txt"))

    # Old path should not exist
    with pytest.raises(FileNotFoundError):
        fs.path("/home/user/file.txt").stat  # noqa: B018

    # New path should exist with content
    reader = fs.read(fs.path("/home/other/moved.txt"))
    assert reader.read(1024) == b"data"
    reader.close()


def test_rename_empty_directory() -> None:
    fs = MockFilesystem({"/home/user/olddir": None})
    fs.rename(fs.path("/home/user/olddir"), fs.path("/home/user/newdir"))

    # Old path should not exist
    with pytest.raises(FileNotFoundError):
        fs.path("/home/user/olddir").stat  # noqa: B018

    # New path should exist as directory
    assert fs.path("/home/user/newdir").stat.is_directory


def test_rename_directory_with_contents() -> None:
    fs = MockFilesystem(
        {
            "/home/user/olddir/file1.txt": b"one",
            "/home/user/olddir/subdir/file2.txt": b"two",
        }
    )
    fs.rename(fs.path("/home/user/olddir"), fs.path("/home/user/newdir"))

    # Old paths should not exist
    with pytest.raises(FileNotFoundError):
        fs.path("/home/user/olddir").stat  # noqa: B018
    with pytest.raises(FileNotFoundError):
        fs.path("/home/user/olddir/file1.txt").stat  # noqa: B018

    # New paths should exist with content
    assert fs.path("/home/user/newdir").stat.is_directory
    reader1 = fs.read(fs.path("/home/user/newdir/file1.txt"))
    assert reader1.read(1024) == b"one"
    reader1.close()

    reader2 = fs.read(fs.path("/home/user/newdir/subdir/file2.txt"))
    assert reader2.read(1024) == b"two"
    reader2.close()


def test_rename_overwrites_existing_file() -> None:
    """Rename raises FileExistsError if destination exists."""
    fs = MockFilesystem(
        {
            "/home/user/src.txt": b"source",
            "/home/user/dst.txt": b"destination",
        }
    )
    with pytest.raises(FileExistsError, match="File exists"):
        fs.rename(fs.path("/home/user/src.txt"), fs.path("/home/user/dst.txt"))


def test_rename_overwrites_existing_directory() -> None:
    """Rename raises FileExistsError if destination directory exists."""
    fs = MockFilesystem(
        {
            "/home/user/src.txt": b"content",
            "/home/user/dstdir": None,
        }
    )
    with pytest.raises(FileExistsError, match="File exists"):
        fs.rename(fs.path("/home/user/src.txt"), fs.path("/home/user/dstdir"))


def test_rename_missing_source_raises() -> None:
    fs = MockFilesystem()
    with pytest.raises(FileNotFoundError):
        fs.rename(fs.path("/home/user/missing.txt"), fs.path("/home/user/new.txt"))


def test_rename_missing_destination_parent_raises() -> None:
    fs = MockFilesystem({"/home/user/file.txt": b"x"})
    with pytest.raises((FileNotFoundError, NotADirectoryError)):
        fs.rename(fs.path("/home/user/file.txt"), fs.path("/no/such/dir/file.txt"))


def test_rename_preserves_file_content() -> None:
    fs = MockFilesystem({"/home/user/test.txt": b"important data"})
    fs.rename(fs.path("/home/user/test.txt"), fs.path("/home/user/renamed.txt"))

    reader = fs.read(fs.path("/home/user/renamed.txt"))
    assert reader.read(1024) == b"important data"
    reader.close()


def test_rename_directory_updates_all_descendants() -> None:
    fs = MockFilesystem(
        {
            "/a/b/c/d/file.txt": b"deep",
            "/a/b/other.txt": b"shallow",
            "/x": None,  # Create parent directory for destination
        }
    )
    fs.rename(fs.path("/a/b"), fs.path("/x/y"))

    # Old paths should not exist
    with pytest.raises(FileNotFoundError):
        fs.path("/a/b").stat  # noqa: B018
    with pytest.raises(FileNotFoundError):
        fs.path("/a/b/c/d/file.txt").stat  # noqa: B018

    # New paths should exist
    reader1 = fs.read(fs.path("/x/y/c/d/file.txt"))
    assert reader1.read(1024) == b"deep"
    reader1.close()

    reader2 = fs.read(fs.path("/x/y/other.txt"))
    assert reader2.read(1024) == b"shallow"
    reader2.close()


# ---------------------------------------------------------------------------
# write() — additional cases
# ---------------------------------------------------------------------------


def test_write_on_directory_raises() -> None:
    fs = MockFilesystem({"/home/user/d": None})
    with pytest.raises(IsADirectoryError):
        fs.write(fs.path("/home/user/d"))


def test_write_error_injection() -> None:
    fs = MockFilesystem(
        {"/home/user/f.txt": b"original"},
        write_errors={"/home/user/f.txt": OSError("injected write error")},
    )
    writer = fs.write(fs.path("/home/user/f.txt"))
    with pytest.raises(OSError, match="injected write error"):
        writer.write(b"data")
    writer.close()
    assert writer.close_count == 1


def test_write_errors_only_affect_specified_path() -> None:
    fs = MockFilesystem(
        write_errors={"/home/user/bad.txt": OSError("injected")},
    )
    writer = fs.write(fs.path("/home/user/good.txt"))
    assert writer.write(b"ok") == 2
    writer.close()


# ---------------------------------------------------------------------------
# rmdir() — additional cases
# ---------------------------------------------------------------------------


def test_rmdir_missing_raises() -> None:
    fs = MockFilesystem()
    with pytest.raises(FileNotFoundError):
        fs.rmdir(fs.path("/home/user/no_such_dir"))


# ---------------------------------------------------------------------------
# mkdir()
# ---------------------------------------------------------------------------


def test_mkdir_creates_directory() -> None:
    fs = MockFilesystem()
    fs.mkdir(fs.path("/home/user/newdir"))
    assert fs.path("/home/user/newdir").stat.is_directory


def test_mkdir_existing_directory_raises() -> None:
    fs = MockFilesystem({"/home/user/existing": None})
    with pytest.raises(FileExistsError):
        fs.mkdir(fs.path("/home/user/existing"))


def test_mkdir_existing_file_raises() -> None:
    fs = MockFilesystem({"/home/user/f.txt": b"x"})
    with pytest.raises(FileExistsError):
        fs.mkdir(fs.path("/home/user/f.txt"))


def test_mkdir_missing_parent_raises() -> None:
    fs = MockFilesystem()
    with pytest.raises((FileNotFoundError, NotADirectoryError)):
        fs.mkdir(fs.path("/no/such/parent/newdir"))


def test_mkdir_parent_is_file_raises() -> None:
    fs = MockFilesystem({"/home/user/f.txt": b"x"})
    with pytest.raises(NotADirectoryError):
        fs.mkdir(fs.path("/home/user/f.txt/child"))


def test_mkdir_new_dir_appears_in_iterdir() -> None:
    fs = MockFilesystem()
    fs.mkdir(fs.path("/home/user/newdir"))
    children = {str(p.path) for p in fs.path("/home/user").iterdir()}
    assert "/home/user/newdir" in children


# ---------------------------------------------------------------------------
# is_same_device()
# ---------------------------------------------------------------------------


def test_is_same_device_same_filesystem() -> None:
    fs = MockFilesystem({"/a.txt": b"x", "/b.txt": b"y"})
    assert fs.is_same_device(fs.path("/a.txt"), fs.path("/b.txt"))


def test_is_same_device_different_filesystem() -> None:
    fs1 = MockFilesystem({"/a.txt": b"x"})
    fs2 = MockFilesystem({"/a.txt": b"x"})
    assert not fs1.is_same_device(fs1.path("/a.txt"), fs2.path("/a.txt"))
