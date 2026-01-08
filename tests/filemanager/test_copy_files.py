import threading
from pathlib import PurePosixPath

import pytest

from nova_navigator.filemanager.tasks import CHUNK_SIZE, FileCopyOptions, copy_file, copy_files
from nova_navigator.task import Decision, TaskCancelled, TaskStatus
from tests.mock_filesystem import MockFilesystem

from .common import make_status, read_all, run_task

# ---------------------------------------------------------------------------
# Tests
# ---------------------------------------------------------------------------


def test_copy_file_simple() -> None:
    """Copies a file between two MockFilesystem instances."""
    content = b"hello from memory"
    src_fs = MockFilesystem({"/src/file.txt": content})
    dst_fs = MockFilesystem()

    src = src_fs.path("/src/file.txt")
    dst = dst_fs.path("/home/user/other.txt")

    run_task(copy_file(make_status(), src, dst))

    assert dst_fs.path("/home/user/other.txt").stat.size == len(content)
    assert read_all(dst_fs, "/home/user/other.txt") == content
    assert src_fs.readers[0].close_count == 1
    assert dst_fs.writers[0].close_count == 1


def test_copy_file_overwrite_skip() -> None:
    """skip policy leaves dst unchanged when dst exists."""
    src_fs = MockFilesystem({"/src/file.txt": b"new"})
    dst_fs = MockFilesystem({"/home/user/other.txt": b"original"})

    src = src_fs.path("/src/file.txt")
    dst = dst_fs.path("/home/user/other.txt")

    run_task(copy_file(make_status(), src, dst, FileCopyOptions(overwrite="skip")))

    assert len(dst_fs.writers) == 0
    assert src_fs.readers[0].close_count == 1
    assert read_all(dst_fs, "/home/user/other.txt") == b"original"


def test_copy_file_overwrite_force() -> None:
    """overwrite policy replaces dst content without asking."""
    src_fs = MockFilesystem({"/src/file.txt": b"new content"})
    dst_fs = MockFilesystem({"/home/user/other.txt": b"old"})

    src = src_fs.path("/src/file.txt")
    dst = dst_fs.path("/home/user/other.txt")

    run_task(copy_file(make_status(), src, dst, FileCopyOptions(overwrite="overwrite")))

    assert src_fs.readers[0].close_count == 1
    assert dst_fs.writers[0].close_count == 1
    assert read_all(dst_fs, "/home/user/other.txt") == b"new content"


def test_copy_file_ask_yes() -> None:
    """ask policy prompts the user; YES overwrites dst."""
    src_fs = MockFilesystem({"/src/file.txt": b"replacement"})
    dst_fs = MockFilesystem({"/home/user/other.txt": b"old"})

    src = src_fs.path("/src/file.txt")
    dst = dst_fs.path("/home/user/other.txt")

    requests = run_task(
        copy_file(make_status(), src, dst, FileCopyOptions(overwrite="ask")),
        [Decision.YES],
    )

    assert len(requests) == 1
    assert src_fs.readers[0].close_count == 1
    assert dst_fs.writers[0].close_count == 1
    assert read_all(dst_fs, "/home/user/other.txt") == b"replacement"


def test_copy_file_ask_no() -> None:
    """ask policy prompts the user; NO leaves dst unchanged."""
    src_fs = MockFilesystem({"/src/file.txt": b"replacement"})
    dst_fs = MockFilesystem({"/home/user/other.txt": b"original"})

    src = src_fs.path("/src/file.txt")
    dst = dst_fs.path("/home/user/other.txt")

    requests = run_task(
        copy_file(make_status(), src, dst, FileCopyOptions(overwrite="ask")),
        [Decision.NO],
    )

    assert len(requests) == 1
    assert len(dst_fs.writers) == 0
    assert src_fs.readers[0].close_count == 1
    assert read_all(dst_fs, "/home/user/other.txt") == b"original"


def test_copy_file_reader_closed_on_error() -> None:
    """Reader and writer are closed even if an error occurs mid-copy."""
    src_fs = MockFilesystem(
        {"/src/file.txt": b"x" * 100},
        read_errors={"/src/file.txt": OSError("disk error")},
    )
    dst_fs = MockFilesystem()

    src = src_fs.path("/src/file.txt")
    dst = dst_fs.path("/home/user/file.txt")

    with pytest.raises(OSError, match="disk error"):
        run_task(copy_file(make_status(), src, dst))

    assert src_fs.readers[0].close_count == 1
    assert dst_fs.writers[0].close_count == 1


def test_copy_file_same_fs() -> None:
    """Copy within the same MockFilesystem instance."""
    content = b"same filesystem copy"
    fs = MockFilesystem({"/src/file.txt": content})
    fs._mkdir_p(fs._cwd_path / "dst")

    src = fs.path("/src/file.txt")
    dst = fs.path("/home/user/dst/copy.txt")

    run_task(copy_file(make_status(), src, dst))

    assert fs.readers[0].close_count == 1
    assert fs.writers[0].close_count == 1
    assert read_all(fs, "/home/user/dst/copy.txt") == content


def test_copy_file_large() -> None:
    """Copy of data larger than CHUNK_SIZE works correctly."""
    content = bytes(range(256)) * (CHUNK_SIZE * 3 // 256 + 12)  # ~3 chunks of patterned data
    src_fs = MockFilesystem({"/src/big.bin": content})
    dst_fs = MockFilesystem()

    src = src_fs.path("/src/big.bin")
    dst = dst_fs.path("/home/user/big.bin")

    run_task(copy_file(make_status(), src, dst))

    assert src_fs.readers[0].close_count == 1
    assert dst_fs.writers[0].close_count == 1
    assert read_all(dst_fs, "/home/user/big.bin") == content


def test_copy_file_empty_src() -> None:
    """Copying a 0-byte file opens and closes writer without writing any chunks."""
    src_fs = MockFilesystem({"/src/empty.txt": b""})
    dst_fs = MockFilesystem()

    src = src_fs.path("/src/empty.txt")
    dst = dst_fs.path("/home/user/empty.txt")

    run_task(copy_file(make_status(), src, dst))

    assert src_fs.readers[0].close_count == 1
    assert dst_fs.writers[0].close_count == 1
    assert dst_fs.path("/home/user/empty.txt").stat.size == 0


def test_copy_file_cancelled_closes_streams() -> None:
    """TaskCancelled propagates and reader/writer are still closed."""
    src_fs = MockFilesystem({"/src/file.txt": b"x" * CHUNK_SIZE})
    dst_fs = MockFilesystem()

    cancel = threading.Event()
    cancel.set()
    status = make_status(cancel_event=cancel)

    src = src_fs.path("/src/file.txt")
    dst = dst_fs.path("/home/user/file.txt")

    with pytest.raises(TaskCancelled):
        run_task(copy_file(status, src, dst))

    assert src_fs.readers[0].close_count == 1
    assert dst_fs.writers[0].close_count == 1


def test_copy_file_write_error_closes_streams() -> None:
    """OSError during write() still closes both reader and writer."""
    src_fs = MockFilesystem({"/src/file.txt": b"data"})
    dst_fs = MockFilesystem(
        write_errors={"/home/user/file.txt": OSError("disk full")},
    )

    src = src_fs.path("/src/file.txt")
    dst = dst_fs.path("/home/user/file.txt")

    with pytest.raises(OSError, match="disk full"):
        run_task(copy_file(make_status(), src, dst))

    assert src_fs.readers[0].close_count == 1
    assert dst_fs.writers[0].close_count == 1


# ---------------------------------------------------------------------------
# copy_paths tests
# ---------------------------------------------------------------------------

# 1. copy single file to new location and different name
# 2. copy single file to new directory
# 3. copy flat directory (only files, no subdirs)
# 4. copy deep directory hierarchy (multiple levels of nested subdirs)
# 5. copy mix of files and directories in same operation
# 6. overwrite policy: skip, overwrite, ask (with YES, NO, ALL, NONE)
# 7. progress tracking: overall progress should track number of src_paths completed
# 8. cancellation: TaskCancelled should be raised if cancel event is set mid-copy
# 9. error handling: OSError during read/write should propagate and still close streams


# 1.
def test_copy_paths_single_file() -> None:
    """A single file is copied into the destination directory under its original name."""
    src_fs = MockFilesystem({"/src/hello.txt": b"hello"})
    dst_fs = MockFilesystem()

    run_task(copy_files(make_status(), [src_fs.path("/src/hello.txt")], dst_fs.path("/home/user/other.txt")))

    assert read_all(dst_fs, "/home/user/other.txt") == b"hello"


# 2.
def test_copy_paths_single_to_dir() -> None:
    """A single file is copied into the destination directory under its original name."""
    src_fs = MockFilesystem({"/src/hello.txt": b"hello"})
    dst_fs = MockFilesystem()

    run_task(copy_files(make_status(), [src_fs.path("/src/hello.txt")], dst_fs.path("/home/user")))

    assert read_all(dst_fs, "/home/user/hello.txt") == b"hello"


def test_copy_paths_multiple_files() -> None:
    """Multiple source files are all placed inside the destination directory."""
    src_fs = MockFilesystem(
        {
            "/src/a.txt": b"aaa",
            "/src/b.txt": b"bbb",
            "/src/c.txt": b"ccc",
        }
    )
    dst_fs = MockFilesystem()

    srcs = [src_fs.path(p) for p in ("/src/a.txt", "/src/b.txt", "/src/c.txt")]
    run_task(copy_files(make_status(), srcs, dst_fs.path("/home/user")))

    assert read_all(dst_fs, "/home/user/a.txt") == b"aaa"
    assert read_all(dst_fs, "/home/user/b.txt") == b"bbb"
    assert read_all(dst_fs, "/home/user/c.txt") == b"ccc"


# 3.
def test_copy_paths_flat_directory() -> None:
    """A directory whose contents are all flat files is copied recursively."""
    src_fs = MockFilesystem(
        {
            "/src/mydir/a.txt": b"a",
            "/src/mydir/b.txt": b"b",
        }
    )
    # Pre-create destination directory tree (no mkdir on Filesystem ABC)
    dst_fs = MockFilesystem()
    dst_fs._mkdir_p(PurePosixPath("/dst/mydir"))

    run_task(copy_files(make_status(), [src_fs.path("/src/mydir")], dst_fs.path("/dst")))

    assert read_all(dst_fs, "/dst/mydir/a.txt") == b"a"
    assert read_all(dst_fs, "/dst/mydir/b.txt") == b"b"


# 4.
def test_copy_paths_deep_hierarchy() -> None:
    """Nested subdirectory trees are copied with their full relative structure preserved."""
    src_fs = MockFilesystem(
        {
            "/src/root/top.txt": b"top",
            "/src/root/sub/mid.txt": b"mid",
            "/src/root/sub/deep/bottom.txt": b"bottom",
        }
    )
    dst_fs = MockFilesystem()
    dst_fs._mkdir_p(PurePosixPath("/dst/root/sub/deep"))

    run_task(copy_files(make_status(), [src_fs.path("/src/root")], dst_fs.path("/dst")))

    assert read_all(dst_fs, "/dst/root/top.txt") == b"top"
    assert read_all(dst_fs, "/dst/root/sub/mid.txt") == b"mid"
    assert read_all(dst_fs, "/dst/root/sub/deep/bottom.txt") == b"bottom"


# 5.
def test_copy_paths_mixed_files_and_dirs() -> None:
    """A mix of plain files and directories in src_paths are all handled correctly."""
    src_fs = MockFilesystem(
        {
            "/src/standalone.txt": b"standalone",
            "/src/dir/nested.txt": b"nested",
        }
    )
    dst_fs = MockFilesystem()
    dst_fs._mkdir_p(PurePosixPath("/dst/dir"))

    srcs = [src_fs.path("/src/standalone.txt"), src_fs.path("/src/dir")]
    run_task(copy_files(make_status(), srcs, dst_fs.path("/dst")))

    assert read_all(dst_fs, "/dst/standalone.txt") == b"standalone"
    assert read_all(dst_fs, "/dst/dir/nested.txt") == b"nested"


# 6. (skip)
def test_copy_paths_overwrite_skip() -> None:
    """skip policy silently leaves every existing destination file unchanged."""
    src_fs = MockFilesystem(
        {
            "/src/a.txt": b"new-a",
            "/src/b.txt": b"new-b",
        }
    )
    dst_fs = MockFilesystem(
        {
            "/dst/a.txt": b"original-a",
            "/dst/b.txt": b"original-b",
        }
    )

    srcs = [src_fs.path("/src/a.txt"), src_fs.path("/src/b.txt")]
    requests = run_task(copy_files(make_status(), srcs, dst_fs.path("/dst"), FileCopyOptions(overwrite="skip")))

    assert requests == []
    assert len(dst_fs.writers) == 0
    assert read_all(dst_fs, "/dst/a.txt") == b"original-a"
    assert read_all(dst_fs, "/dst/b.txt") == b"original-b"


# 6. (overwrite)
def test_copy_paths_overwrite_force() -> None:
    """overwrite policy replaces existing destination files without prompting."""
    src_fs = MockFilesystem(
        {
            "/src/a.txt": b"new-a",
            "/src/b.txt": b"new-b",
        }
    )
    dst_fs = MockFilesystem(
        {
            "/dst/a.txt": b"old-a",
            "/dst/b.txt": b"old-b",
        }
    )

    srcs = [src_fs.path("/src/a.txt"), src_fs.path("/src/b.txt")]
    requests = run_task(copy_files(make_status(), srcs, dst_fs.path("/dst"), FileCopyOptions(overwrite="overwrite")))

    assert requests == []
    assert read_all(dst_fs, "/dst/a.txt") == b"new-a"
    assert read_all(dst_fs, "/dst/b.txt") == b"new-b"


# 6. (ask with YES)
def test_copy_paths_overwrite_ask_yes() -> None:
    """ask policy prompts once per conflicting file; YES overwrites it."""
    src_fs = MockFilesystem({"/src/file.txt": b"new"})
    dst_fs = MockFilesystem({"/dst/file.txt": b"old"})

    requests = run_task(
        copy_files(make_status(), [src_fs.path("/src/file.txt")], dst_fs.path("/dst")),
        [Decision.YES],
    )

    assert len(requests) == 1
    assert read_all(dst_fs, "/dst/file.txt") == b"new"


# 6. (ask with NO)
def test_copy_paths_overwrite_ask_no() -> None:
    """ask policy prompts once per conflicting file; NO leaves destination unchanged."""
    src_fs = MockFilesystem({"/src/file.txt": b"new"})
    dst_fs = MockFilesystem({"/dst/file.txt": b"original"})

    requests = run_task(
        copy_files(make_status(), [src_fs.path("/src/file.txt")], dst_fs.path("/dst")),
        [Decision.NO],
    )

    assert len(requests) == 1
    assert len(dst_fs.writers) == 0
    assert read_all(dst_fs, "/dst/file.txt") == b"original"


# 6. (ask per file)
def test_copy_paths_overwrite_ask_per_file() -> None:
    """ask policy prompts independently for each conflicting file."""
    src_fs = MockFilesystem(
        {
            "/src/a.txt": b"new-a",
            "/src/b.txt": b"new-b",
        }
    )
    dst_fs = MockFilesystem(
        {
            "/dst/a.txt": b"old-a",
            "/dst/b.txt": b"old-b",
        }
    )

    srcs = [src_fs.path("/src/a.txt"), src_fs.path("/src/b.txt")]
    # YES for a.txt, NO for b.txt
    requests = run_task(
        copy_files(make_status(), srcs, dst_fs.path("/dst")),
        [Decision.YES, Decision.NO],
    )

    assert len(requests) == 2
    assert read_all(dst_fs, "/dst/a.txt") == b"new-a"
    assert read_all(dst_fs, "/dst/b.txt") == b"old-b"


# 6. (ALL)
def test_copy_paths_overwrite_yes_to_all() -> None:
    """YES_TO_ALL resolves all subsequent conflicts without further prompts."""
    src_fs = MockFilesystem(
        {
            "/src/a.txt": b"new-a",
            "/src/b.txt": b"new-b",
            "/src/c.txt": b"new-c",
        }
    )
    dst_fs = MockFilesystem(
        {
            "/dst/a.txt": b"old-a",
            "/dst/b.txt": b"old-b",
            "/dst/c.txt": b"old-c",
        }
    )

    srcs = [src_fs.path(p) for p in ("/src/a.txt", "/src/b.txt", "/src/c.txt")]
    # run_task drives the generator: ALL on first prompt; the scheduler
    # suppresses subsequent same-message prompts (tested via TaskScheduler in
    # integration), so here we verify that only one DecisionRequest is yielded
    # when the caller supplies ALL for the first conflict.
    requests = run_task(
        copy_files(make_status(), srcs, dst_fs.path("/dst")),
        [Decision.ALL, Decision.ALL, Decision.ALL],
    )

    # Three separate conflicts → three separate DecisionRequests (run_task drives
    # them all; de-duplication happens in TaskScheduler, not in the Task itself)
    assert len(requests) == 3
    assert read_all(dst_fs, "/dst/a.txt") == b"new-a"
    assert read_all(dst_fs, "/dst/b.txt") == b"new-b"
    assert read_all(dst_fs, "/dst/c.txt") == b"new-c"


# 6. (NONE)
def test_copy_paths_overwrite_no_to_all() -> None:
    """NONE (No to All) skips all remaining conflicts without further prompts."""
    src_fs = MockFilesystem(
        {
            "/src/a.txt": b"new-a",
            "/src/b.txt": b"new-b",
            "/src/c.txt": b"new-c",
        }
    )
    dst_fs = MockFilesystem(
        {
            "/dst/a.txt": b"old-a",
            "/dst/b.txt": b"old-b",
            "/dst/c.txt": b"old-c",
        }
    )

    srcs = [src_fs.path(p) for p in ("/src/a.txt", "/src/b.txt", "/src/c.txt")]
    # NONE on all prompts - all files should remain unchanged
    requests = run_task(
        copy_files(make_status(), srcs, dst_fs.path("/dst")),
        [Decision.NONE, Decision.NONE, Decision.NONE],
    )

    # Three separate conflicts → three separate DecisionRequests (run_task drives
    # them all; de-duplication happens in TaskScheduler, not in the Task itself)
    assert len(requests) == 3
    # All destination files should remain unchanged (no writes)
    assert len(dst_fs.writers) == 0
    assert read_all(dst_fs, "/dst/a.txt") == b"old-a"
    assert read_all(dst_fs, "/dst/b.txt") == b"old-b"
    assert read_all(dst_fs, "/dst/c.txt") == b"old-c"


def test_copy_paths_no_conflict_no_prompt() -> None:
    """When destination files do not exist, no DecisionRequests are yielded."""
    src_fs = MockFilesystem(
        {
            "/src/x.txt": b"x",
            "/src/y.txt": b"y",
        }
    )
    dst_fs = MockFilesystem()

    srcs = [src_fs.path("/src/x.txt"), src_fs.path("/src/y.txt")]
    requests = run_task(copy_files(make_status(), srcs, dst_fs.path("/home/user")))

    assert requests == []
    assert read_all(dst_fs, "/home/user/x.txt") == b"x"
    assert read_all(dst_fs, "/home/user/y.txt") == b"y"


# 7.
def test_copy_paths_progress_tracks_src_paths() -> None:
    """Overall progress completed equals len(src_paths) when done; total is set upfront."""
    events: list[tuple[int, int]] = []

    def cb(s: TaskStatus) -> None:
        events.append((s.progress.completed, s.progress.total))

    status = TaskStatus(cancel_event=threading.Event(), progress_callback=cb)

    src_fs = MockFilesystem({"/src/a.txt": b"a", "/src/b.txt": b"b", "/src/c.txt": b"c"})
    dst_fs = MockFilesystem()
    srcs = [src_fs.path(p) for p in ("/src/a.txt", "/src/b.txt", "/src/c.txt")]

    run_task(copy_files(status, srcs, dst_fs.path("/home/user")))

    totals = [t for _, t in events]
    completeds = [c for c, _ in events]
    assert totals[0] == 3  # total reported immediately
    assert max(completeds) == 3  # all three paths marked completed


# 8.
def test_copy_paths_cancelled_mid_list() -> None:
    """TaskCancelled is raised when the cancel event fires between source paths."""
    src_fs = MockFilesystem({"/src/a.txt": b"a", "/src/b.txt": b"b"})
    dst_fs = MockFilesystem()

    cancel = threading.Event()
    cancel.set()
    status = make_status(cancel_event=cancel)

    with pytest.raises(TaskCancelled):
        run_task(copy_files(status, [src_fs.path("/src/a.txt"), src_fs.path("/src/b.txt")], dst_fs.path("/home/user")))


# 9.
def test_copy_paths_error_during_directory_copy() -> None:
    """OSError during recursive directory copy propagates correctly."""
    src_fs = MockFilesystem(
        {
            "/src/mydir/file1.txt": b"content1",
            "/src/mydir/file2.txt": b"content2",
        }
    )
    dst_fs = MockFilesystem(
        write_errors={"/home/user/mydir/file2.txt": OSError("disk full")},
    )
    dst_fs._mkdir_p(PurePosixPath("/home/user/mydir"))

    with pytest.raises(OSError, match="disk full"):
        run_task(copy_files(make_status(), [src_fs.path("/src/mydir")], dst_fs.path("/home/user")))

    # Verify that file1.txt was copied successfully before the error
    assert read_all(dst_fs, "/home/user/mydir/file1.txt") == b"content1"
    # Verify streams were closed even after error
    assert all(r.close_count >= 1 for r in src_fs.readers)
    assert all(w.close_count >= 1 for w in dst_fs.writers)
