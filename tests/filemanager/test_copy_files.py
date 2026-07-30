import threading
from pathlib import PurePosixPath

import pytest

from nova_navigator.file_filter import FilenamePatternFilter
from nova_navigator.filemanager.tasks import CHUNK_SIZE, FileCopyOptions, copy_file, copy_files
from nova_navigator.response import Response
from nova_navigator.scheduler import TaskCancelled, TaskStatus
from tests._utils.mock_filesystem import MockFilesystem

from .common import make_status, read_all, run_task


@pytest.mark.asyncio
async def test_copy_file_simple() -> None:
    """Copies a file between two MockFilesystem instances."""
    content = b"hello from memory"
    src_fs = MockFilesystem({"/src/file.txt": content})
    dst_fs = MockFilesystem()

    src = src_fs.path("/src/file.txt")
    dst = dst_fs.path("/home/user/other.txt")

    await run_task(lambda ctx: copy_file(ctx, src, dst))

    assert dst_fs.path("/home/user/other.txt").stat.size == len(content)
    assert read_all(dst_fs, "/home/user/other.txt") == content
    assert src_fs.readers[0].close_count == 1
    assert dst_fs.writers[0].close_count == 1


@pytest.mark.asyncio
async def test_copy_file_overwrite_skip() -> None:
    """skip policy leaves dst unchanged when dst exists."""
    src_fs = MockFilesystem({"/src/file.txt": b"new"})
    dst_fs = MockFilesystem({"/home/user/other.txt": b"original"})

    src = src_fs.path("/src/file.txt")
    dst = dst_fs.path("/home/user/other.txt")

    await run_task(lambda ctx: copy_file(ctx, src, dst, FileCopyOptions(overwrite="skip")))

    assert len(dst_fs.writers) == 0
    assert src_fs.readers[0].close_count == 1
    assert read_all(dst_fs, "/home/user/other.txt") == b"original"


@pytest.mark.asyncio
async def test_copy_file_overwrite_force() -> None:
    """overwrite policy replaces dst content without asking."""
    src_fs = MockFilesystem({"/src/file.txt": b"new content"})
    dst_fs = MockFilesystem({"/home/user/other.txt": b"old"})

    src = src_fs.path("/src/file.txt")
    dst = dst_fs.path("/home/user/other.txt")

    await run_task(lambda ctx: copy_file(ctx, src, dst, FileCopyOptions(overwrite="overwrite")))

    assert src_fs.readers[0].close_count == 1
    assert dst_fs.writers[0].close_count == 1
    assert read_all(dst_fs, "/home/user/other.txt") == b"new content"


@pytest.mark.asyncio
async def test_copy_file_ask_yes() -> None:
    """ask policy prompts the user; YES overwrites dst."""
    src_fs = MockFilesystem({"/src/file.txt": b"replacement"})
    dst_fs = MockFilesystem({"/home/user/other.txt": b"old"})

    src = src_fs.path("/src/file.txt")
    dst = dst_fs.path("/home/user/other.txt")

    requests = await run_task(
        lambda ctx: copy_file(ctx, src, dst, FileCopyOptions(overwrite="ask")),
        [Response.YES],
    )

    assert len(requests) == 1
    assert src_fs.readers[0].close_count == 1
    assert dst_fs.writers[0].close_count == 1
    assert read_all(dst_fs, "/home/user/other.txt") == b"replacement"


@pytest.mark.asyncio
async def test_copy_file_ask_no() -> None:
    """ask policy prompts the user; NO leaves dst unchanged."""
    src_fs = MockFilesystem({"/src/file.txt": b"replacement"})
    dst_fs = MockFilesystem({"/home/user/other.txt": b"original"})

    src = src_fs.path("/src/file.txt")
    dst = dst_fs.path("/home/user/other.txt")

    requests = await run_task(
        lambda ctx: copy_file(ctx, src, dst, FileCopyOptions(overwrite="ask")),
        [Response.NO],
    )

    assert len(requests) == 1
    assert len(dst_fs.writers) == 0
    assert src_fs.readers[0].close_count == 1
    assert read_all(dst_fs, "/home/user/other.txt") == b"original"


@pytest.mark.asyncio
async def test_copy_file_reader_closed_on_error() -> None:
    """Reader and writer are closed even if an error occurs mid-copy."""
    src_fs = MockFilesystem(
        {"/src/file.txt": b"x" * 100},
        read_errors={"/src/file.txt": OSError("disk error")},
    )
    dst_fs = MockFilesystem()

    src = src_fs.path("/src/file.txt")
    dst = dst_fs.path("/home/user/file.txt")

    with pytest.raises(OSError, match="disk error"):
        await run_task(lambda ctx: copy_file(ctx, src, dst))

    assert src_fs.readers[0].close_count == 1
    assert dst_fs.writers[0].close_count == 1


@pytest.mark.asyncio
async def test_copy_file_same_fs() -> None:
    """Copy within the same MockFilesystem instance."""
    content = b"same filesystem copy"
    fs = MockFilesystem({"/src/file.txt": content})
    fs._mkdir_p(fs._cwd_path / "dst")

    src = fs.path("/src/file.txt")
    dst = fs.path("/home/user/dst/copy.txt")

    await run_task(lambda ctx: copy_file(ctx, src, dst))

    assert fs.readers[0].close_count == 1
    assert fs.writers[0].close_count == 1
    assert read_all(fs, "/home/user/dst/copy.txt") == content


@pytest.mark.asyncio
async def test_copy_file_large() -> None:
    """Copy of data larger than CHUNK_SIZE works correctly."""
    content = bytes(range(256)) * (CHUNK_SIZE * 3 // 256 + 12)
    src_fs = MockFilesystem({"/src/big.bin": content})
    dst_fs = MockFilesystem()

    src = src_fs.path("/src/big.bin")
    dst = dst_fs.path("/home/user/big.bin")

    await run_task(lambda ctx: copy_file(ctx, src, dst))

    assert src_fs.readers[0].close_count == 1
    assert dst_fs.writers[0].close_count == 1
    assert read_all(dst_fs, "/home/user/big.bin") == content


@pytest.mark.asyncio
async def test_copy_file_empty_src() -> None:
    """Copying a 0-byte file opens and closes writer without writing any chunks."""
    src_fs = MockFilesystem({"/src/empty.txt": b""})
    dst_fs = MockFilesystem()

    src = src_fs.path("/src/empty.txt")
    dst = dst_fs.path("/home/user/empty.txt")

    await run_task(lambda ctx: copy_file(ctx, src, dst))

    assert src_fs.readers[0].close_count == 1
    assert dst_fs.writers[0].close_count == 1
    assert dst_fs.path("/home/user/empty.txt").stat.size == 0


@pytest.mark.asyncio
async def test_copy_file_cancelled_closes_streams() -> None:
    """TaskCancelled propagates and reader/writer are still closed."""
    src_fs = MockFilesystem({"/src/file.txt": b"x" * CHUNK_SIZE})
    dst_fs = MockFilesystem()

    cancel = threading.Event()
    cancel.set()
    status = make_status(cancel_event=cancel)

    src = src_fs.path("/src/file.txt")
    dst = dst_fs.path("/home/user/file.txt")

    with pytest.raises(TaskCancelled):
        await run_task(lambda ctx: copy_file(ctx, src, dst), status=status)

    assert src_fs.readers[0].close_count == 1
    assert dst_fs.writers[0].close_count == 1


@pytest.mark.asyncio
async def test_copy_file_write_error_closes_streams() -> None:
    """OSError during write() still closes both reader and writer."""
    src_fs = MockFilesystem({"/src/file.txt": b"data"})
    dst_fs = MockFilesystem(write_errors={"/home/user/file.txt": OSError("disk full")})

    src = src_fs.path("/src/file.txt")
    dst = dst_fs.path("/home/user/file.txt")

    with pytest.raises(OSError, match="disk full"):
        await run_task(lambda ctx: copy_file(ctx, src, dst))

    assert src_fs.readers[0].close_count == 1
    assert dst_fs.writers[0].close_count == 1


@pytest.mark.asyncio
async def test_copy_paths_single_file() -> None:
    """A single file copied to a non-directory destination uses destination as target path."""
    src_fs = MockFilesystem({"/src/hello.txt": b"hello"})
    dst_fs = MockFilesystem()

    await run_task(lambda ctx: copy_files(ctx, [src_fs.path("/src/hello.txt")], dst_fs.path("/home/user/other.txt")))

    assert read_all(dst_fs, "/home/user/other.txt") == b"hello"


@pytest.mark.asyncio
async def test_copy_paths_single_to_dir() -> None:
    """A single file copied into an existing directory is placed under its original name."""
    src_fs = MockFilesystem({"/src/hello.txt": b"hello"})
    dst_fs = MockFilesystem()

    await run_task(lambda ctx: copy_files(ctx, [src_fs.path("/src/hello.txt")], dst_fs.path("/home/user")))

    assert read_all(dst_fs, "/home/user/hello.txt") == b"hello"


@pytest.mark.asyncio
async def test_copy_paths_multiple_files() -> None:
    """Multiple source files are all placed inside the destination directory."""
    src_fs = MockFilesystem({"/src/a.txt": b"aaa", "/src/b.txt": b"bbb", "/src/c.txt": b"ccc"})
    dst_fs = MockFilesystem()

    srcs = [src_fs.path(p) for p in ("/src/a.txt", "/src/b.txt", "/src/c.txt")]
    await run_task(lambda ctx: copy_files(ctx, srcs, dst_fs.path("/home/user")))

    assert read_all(dst_fs, "/home/user/a.txt") == b"aaa"
    assert read_all(dst_fs, "/home/user/b.txt") == b"bbb"
    assert read_all(dst_fs, "/home/user/c.txt") == b"ccc"


@pytest.mark.asyncio
async def test_copy_paths_flat_directory() -> None:
    """A directory whose contents are all flat files is copied recursively."""
    src_fs = MockFilesystem({"/src/mydir/a.txt": b"a", "/src/mydir/b.txt": b"b"})
    dst_fs = MockFilesystem()
    dst_fs._mkdir_p(PurePosixPath("/dst/mydir"))

    await run_task(lambda ctx: copy_files(ctx, [src_fs.path("/src/mydir")], dst_fs.path("/dst")))

    assert read_all(dst_fs, "/dst/mydir/a.txt") == b"a"
    assert read_all(dst_fs, "/dst/mydir/b.txt") == b"b"


@pytest.mark.asyncio
async def test_copy_paths_deep_hierarchy() -> None:
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

    await run_task(lambda ctx: copy_files(ctx, [src_fs.path("/src/root")], dst_fs.path("/dst")))

    assert read_all(dst_fs, "/dst/root/top.txt") == b"top"
    assert read_all(dst_fs, "/dst/root/sub/mid.txt") == b"mid"
    assert read_all(dst_fs, "/dst/root/sub/deep/bottom.txt") == b"bottom"


@pytest.mark.asyncio
async def test_copy_paths_mixed_files_and_dirs() -> None:
    """A mix of plain files and directories in src_paths are all handled correctly."""
    src_fs = MockFilesystem({"/src/standalone.txt": b"standalone", "/src/dir/nested.txt": b"nested"})
    dst_fs = MockFilesystem()
    dst_fs._mkdir_p(PurePosixPath("/dst/dir"))

    srcs = [src_fs.path("/src/standalone.txt"), src_fs.path("/src/dir")]
    await run_task(lambda ctx: copy_files(ctx, srcs, dst_fs.path("/dst")))

    assert read_all(dst_fs, "/dst/standalone.txt") == b"standalone"
    assert read_all(dst_fs, "/dst/dir/nested.txt") == b"nested"


@pytest.mark.asyncio
async def test_copy_paths_overwrite_skip() -> None:
    """skip policy silently leaves every existing destination file unchanged."""
    src_fs = MockFilesystem({"/src/a.txt": b"new-a", "/src/b.txt": b"new-b"})
    dst_fs = MockFilesystem({"/dst/a.txt": b"original-a", "/dst/b.txt": b"original-b"})

    srcs = [src_fs.path("/src/a.txt"), src_fs.path("/src/b.txt")]
    requests = await run_task(lambda ctx: copy_files(ctx, srcs, dst_fs.path("/dst"), FileCopyOptions(overwrite="skip")))

    assert requests == []
    assert len(dst_fs.writers) == 0
    assert read_all(dst_fs, "/dst/a.txt") == b"original-a"
    assert read_all(dst_fs, "/dst/b.txt") == b"original-b"


@pytest.mark.asyncio
async def test_copy_paths_overwrite_force() -> None:
    """overwrite policy replaces existing destination files without prompting."""
    src_fs = MockFilesystem({"/src/a.txt": b"new-a", "/src/b.txt": b"new-b"})
    dst_fs = MockFilesystem({"/dst/a.txt": b"old-a", "/dst/b.txt": b"old-b"})

    srcs = [src_fs.path("/src/a.txt"), src_fs.path("/src/b.txt")]
    requests = await run_task(lambda ctx: copy_files(ctx, srcs, dst_fs.path("/dst"), FileCopyOptions(overwrite="overwrite")))

    assert requests == []
    assert read_all(dst_fs, "/dst/a.txt") == b"new-a"
    assert read_all(dst_fs, "/dst/b.txt") == b"new-b"


@pytest.mark.asyncio
async def test_copy_paths_overwrite_ask_yes() -> None:
    """ask policy prompts once per conflicting file; YES overwrites it."""
    src_fs = MockFilesystem({"/src/file.txt": b"new"})
    dst_fs = MockFilesystem({"/dst/file.txt": b"old"})

    requests = await run_task(
        lambda ctx: copy_files(ctx, [src_fs.path("/src/file.txt")], dst_fs.path("/dst")),
        [Response.YES],
    )

    assert len(requests) == 1
    assert read_all(dst_fs, "/dst/file.txt") == b"new"


@pytest.mark.asyncio
async def test_copy_paths_overwrite_ask_no() -> None:
    """ask policy prompts once per conflicting file; NO leaves destination unchanged."""
    src_fs = MockFilesystem({"/src/file.txt": b"new"})
    dst_fs = MockFilesystem({"/dst/file.txt": b"original"})

    requests = await run_task(
        lambda ctx: copy_files(ctx, [src_fs.path("/src/file.txt")], dst_fs.path("/dst")),
        [Response.NO],
    )

    assert len(requests) == 1
    assert len(dst_fs.writers) == 0
    assert read_all(dst_fs, "/dst/file.txt") == b"original"


@pytest.mark.asyncio
async def test_copy_paths_overwrite_ask_per_file() -> None:
    """ask policy prompts independently for each conflicting file."""
    src_fs = MockFilesystem({"/src/a.txt": b"new-a", "/src/b.txt": b"new-b"})
    dst_fs = MockFilesystem({"/dst/a.txt": b"old-a", "/dst/b.txt": b"old-b"})

    srcs = [src_fs.path("/src/a.txt"), src_fs.path("/src/b.txt")]
    requests = await run_task(
        lambda ctx: copy_files(ctx, srcs, dst_fs.path("/dst")),
        [Response.YES, Response.NO],
    )

    assert len(requests) == 2
    assert read_all(dst_fs, "/dst/a.txt") == b"new-a"
    assert read_all(dst_fs, "/dst/b.txt") == b"old-b"


@pytest.mark.asyncio
async def test_copy_paths_overwrite_yes_to_all() -> None:
    """ALL on the first conflict suppresses all subsequent prompts for the same title."""
    src_fs = MockFilesystem({"/src/a.txt": b"new-a", "/src/b.txt": b"new-b", "/src/c.txt": b"new-c"})
    dst_fs = MockFilesystem({"/dst/a.txt": b"old-a", "/dst/b.txt": b"old-b", "/dst/c.txt": b"old-c"})

    srcs = [src_fs.path(p) for p in ("/src/a.txt", "/src/b.txt", "/src/c.txt")]
    # Scheduler caches ALL after the first prompt; sub-tasks 2 and 3 get the cached response.
    requests = await run_task(
        lambda ctx: copy_files(ctx, srcs, dst_fs.path("/dst")),
        [Response.ALL],
    )

    assert len(requests) == 1
    assert read_all(dst_fs, "/dst/a.txt") == b"new-a"
    assert read_all(dst_fs, "/dst/b.txt") == b"new-b"
    assert read_all(dst_fs, "/dst/c.txt") == b"new-c"


@pytest.mark.asyncio
async def test_copy_paths_overwrite_no_to_all() -> None:
    """NONE on the first conflict suppresses all subsequent prompts; all files left unchanged."""
    src_fs = MockFilesystem({"/src/a.txt": b"new-a", "/src/b.txt": b"new-b", "/src/c.txt": b"new-c"})
    dst_fs = MockFilesystem({"/dst/a.txt": b"old-a", "/dst/b.txt": b"old-b", "/dst/c.txt": b"old-c"})

    srcs = [src_fs.path(p) for p in ("/src/a.txt", "/src/b.txt", "/src/c.txt")]
    requests = await run_task(
        lambda ctx: copy_files(ctx, srcs, dst_fs.path("/dst")),
        [Response.NONE],
    )

    assert len(requests) == 1
    assert len(dst_fs.writers) == 0
    assert read_all(dst_fs, "/dst/a.txt") == b"old-a"
    assert read_all(dst_fs, "/dst/b.txt") == b"old-b"
    assert read_all(dst_fs, "/dst/c.txt") == b"old-c"


@pytest.mark.asyncio
async def test_copy_paths_no_conflict_no_prompt() -> None:
    """When destination files do not exist, no ResponseRequests are issued."""
    src_fs = MockFilesystem({"/src/x.txt": b"x", "/src/y.txt": b"y"})
    dst_fs = MockFilesystem()

    srcs = [src_fs.path("/src/x.txt"), src_fs.path("/src/y.txt")]
    requests = await run_task(lambda ctx: copy_files(ctx, srcs, dst_fs.path("/home/user")))

    assert requests == []
    assert read_all(dst_fs, "/home/user/x.txt") == b"x"
    assert read_all(dst_fs, "/home/user/y.txt") == b"y"


@pytest.mark.asyncio
async def test_copy_files_filter_skips_non_matching() -> None:
    """Files not matching the filter are skipped; matching files are copied."""
    src_fs = MockFilesystem({"/src/a.txt": b"text", "/src/b.py": b"python"})
    dst_fs = MockFilesystem()
    dst_fs._mkdir_p(PurePosixPath("/dst"))

    options = FileCopyOptions(filter=FilenamePatternFilter(patterns=["*.txt"]))
    await run_task(
        lambda ctx: copy_files(ctx, [src_fs.path("/src/a.txt"), src_fs.path("/src/b.py")], dst_fs.path("/dst"), options),
    )

    assert read_all(dst_fs, "/dst/a.txt") == b"text"
    assert dst_fs.path("/dst/b.py").stat_or_none is None


@pytest.mark.asyncio
async def test_copy_dir_filter_skips_non_matching_files() -> None:
    """When copying a directory tree, only matching files are copied; dirs are still created."""
    src_fs = MockFilesystem(
        {
            "/src/mydir/a.txt": b"text",
            "/src/mydir/b.py": b"python",
            "/src/mydir/sub/c.txt": b"subtext",
        }
    )
    dst_fs = MockFilesystem()
    dst_fs._mkdir_p(PurePosixPath("/dst"))

    options = FileCopyOptions(filter=FilenamePatternFilter(patterns=["*.txt"]))
    await run_task(
        lambda ctx: copy_files(ctx, [src_fs.path("/src/mydir")], dst_fs.path("/dst/mydir"), options),
    )

    assert read_all(dst_fs, "/dst/mydir/a.txt") == b"text"
    assert dst_fs.path("/dst/mydir/b.py").stat_or_none is None
    assert read_all(dst_fs, "/dst/mydir/sub/c.txt") == b"subtext"
    # sub/ directory must exist even though b.py was skipped
    assert dst_fs.path("/dst/mydir/sub").stat.is_directory


@pytest.mark.asyncio
async def test_copy_filter_none_copies_all() -> None:
    """filter=None (default) copies all files — unchanged behaviour."""
    src_fs = MockFilesystem({"/src/a.txt": b"a", "/src/b.py": b"b"})
    dst_fs = MockFilesystem()
    dst_fs._mkdir_p(PurePosixPath("/dst"))

    await run_task(
        lambda ctx: copy_files(ctx, [src_fs.path("/src/a.txt"), src_fs.path("/src/b.py")], dst_fs.path("/dst")),
    )

    assert read_all(dst_fs, "/dst/a.txt") == b"a"
    assert read_all(dst_fs, "/dst/b.py") == b"b"


@pytest.mark.asyncio
async def test_copy_single_file_to_new_name_filter_skips_non_matching() -> None:
    """Filter is applied even in the single-file-to-new-filename fast path."""
    src_fs = MockFilesystem({"/src/script.py": b"code"})
    dst_fs = MockFilesystem()

    options = FileCopyOptions(filter=FilenamePatternFilter(patterns=["*.txt"]))
    await run_task(
        lambda ctx: copy_files(ctx, [src_fs.path("/src/script.py")], dst_fs.path("/dst/renamed.py"), options),
    )

    assert dst_fs.path("/dst/renamed.py").stat_or_none is None


@pytest.mark.asyncio
async def test_copy_paths_progress_tracks_src_paths() -> None:
    """Overall progress total equals len(src_paths) upfront; completed reaches that total."""
    events: list[tuple[int, int]] = []

    def cb(s: TaskStatus) -> None:
        events.append((s.progress.completed, s.progress.total))

    status = TaskStatus(cancel_event=threading.Event(), progress_callback=cb)
    src_fs = MockFilesystem({"/src/a.txt": b"a", "/src/b.txt": b"b", "/src/c.txt": b"c"})
    dst_fs = MockFilesystem()
    srcs = [src_fs.path(p) for p in ("/src/a.txt", "/src/b.txt", "/src/c.txt")]

    await run_task(
        lambda ctx: copy_files(ctx, srcs, dst_fs.path("/home/user")),
        status=status,
    )

    totals = [t for _, t in events]
    completeds = [c for c, _ in events]
    assert totals[0] == 3
    assert max(completeds) == 3


@pytest.mark.asyncio
async def test_copy_paths_cancelled_mid_list() -> None:
    """TaskCancelled is raised when the cancel event fires during the copy."""
    src_fs = MockFilesystem({"/src/a.txt": b"a", "/src/b.txt": b"b"})
    dst_fs = MockFilesystem()

    cancel = threading.Event()
    cancel.set()
    status = make_status(cancel_event=cancel)

    with pytest.raises(TaskCancelled):
        await run_task(
            lambda ctx: copy_files(ctx, [src_fs.path("/src/a.txt"), src_fs.path("/src/b.txt")], dst_fs.path("/home/user")),
            status=status,
        )


@pytest.mark.asyncio
async def test_copy_paths_error_during_directory_copy() -> None:
    """OSError during recursive directory copy propagates correctly."""
    src_fs = MockFilesystem({"/src/mydir/file1.txt": b"content1", "/src/mydir/file2.txt": b"content2"})
    dst_fs = MockFilesystem(
        write_errors={"/home/user/mydir/file2.txt": OSError("disk full")},
    )
    dst_fs._mkdir_p(PurePosixPath("/home/user/mydir"))

    with pytest.raises(OSError, match="disk full"):
        await run_task(lambda ctx: copy_files(ctx, [src_fs.path("/src/mydir")], dst_fs.path("/home/user")))


@pytest.mark.asyncio
async def test_copy_paths_single_directory_to_nonexistent_destination() -> None:
    """BUG-3: single directory source with non-existent destination must not crash.

    When exactly one source is a directory and the destination path does not yet
    exist, copy_files previously called copy_file on the directory, which
    raised IsADirectoryError.  The correct behaviour mirrors the single-file
    shortcut: the destination path becomes the new directory, with the source
    tree's contents placed directly under it.
    """
    src_fs = MockFilesystem(
        {
            "/src/mydir/top.txt": b"top",
            "/src/mydir/sub/nested.txt": b"nested",
        }
    )
    dst_fs = MockFilesystem()  # /home/user exists; /home/user/mycopy does not

    await run_task(lambda ctx: copy_files(ctx, [src_fs.path("/src/mydir")], dst_fs.path("/home/user/mycopy")))

    assert read_all(dst_fs, "/home/user/mycopy/top.txt") == b"top"
    assert read_all(dst_fs, "/home/user/mycopy/sub/nested.txt") == b"nested"


@pytest.mark.asyncio
async def test_copy_paths_directory_progress_completed_equals_total() -> None:
    """BUG-4: completed must equal total after copying a directory source.

    _copy_dir inflates total by the number of files in each walk() level but
    only increments completed by 1 at the very end, leaving progress stuck at
    1/N.  After copy_files finishes, the final progress snapshot must have
    completed == total.
    """
    events: list[tuple[int, int]] = []

    def cb(s: TaskStatus) -> None:
        events.append((s.progress.completed, s.progress.total))

    status = TaskStatus(cancel_event=threading.Event(), progress_callback=cb)
    src_fs = MockFilesystem(
        {
            "/src/mydir/a.txt": b"a",
            "/src/mydir/b.txt": b"b",
            "/src/mydir/c.txt": b"c",
        }
    )
    dst_fs = MockFilesystem()
    dst_fs._mkdir_p(PurePosixPath("/dst/mydir"))

    await run_task(
        lambda ctx: copy_files(ctx, [src_fs.path("/src/mydir")], dst_fs.path("/dst")),
        status=status,
    )

    final_completed, final_total = events[-1]
    assert final_completed == final_total, f"Progress stuck: completed={final_completed}, total={final_total}"


@pytest.mark.asyncio
async def test_copy_paths_skip_does_not_overflow_progress() -> None:
    """BUG-5: completed must never exceed total when a copy is skipped.

    copy_file calls ctx.status.set_completed() on skip, which is an absolute
    setter that sets completed=total.  copy_files then increments completed by
    1 more, leaving completed > total.  After copy_files finishes, the final
    progress snapshot must have completed <= total.
    """
    events: list[tuple[int, int]] = []

    def cb(s: TaskStatus) -> None:
        events.append((s.progress.completed, s.progress.total))

    status = TaskStatus(cancel_event=threading.Event(), progress_callback=cb)
    src_fs = MockFilesystem({"/src/file.txt": b"new"})
    dst_fs = MockFilesystem({"/dst/file.txt": b"original"})

    await run_task(
        lambda ctx: copy_files(ctx, [src_fs.path("/src/file.txt")], dst_fs.path("/dst"), FileCopyOptions(overwrite="skip")),
        status=status,
    )

    for completed, total in events:
        assert completed <= total, f"completed={completed} exceeded total={total}"


@pytest.mark.asyncio
async def test_copy_directory_progress_total() -> None:
    """Copying a directory of 3 files must report a total of at least 3 in the progress.

    BUG: copy_files only increments total by len(src_paths) (i.e. 1 for the
    directory itself), so progress.total stays at 1 even though the directory
    contains 3 files.  The correct value is 3 (the files) or 4 (the directory
    plus its 3 files).
    """
    src_fs = MockFilesystem(
        {
            "/src/mydir/a.txt": b"a",
            "/src/mydir/b.txt": b"b",
            "/src/mydir/c.txt": b"c",
        }
    )
    dst_fs = MockFilesystem()

    status = make_status()
    await run_task(
        lambda ctx: copy_files(ctx, [src_fs.path("/src/mydir")], dst_fs.path("/home/user")),
        status=status,
    )

    assert status.progress.total >= 3
    assert status.progress.completed == status.progress.total
    assert status.progress.step_completed == status.progress.step_total


@pytest.mark.asyncio
async def test_copy_plain_files_total_known_upfront() -> None:
    """Copying 5 plain files must set progress.total to 5 before any file is processed."""
    src_fs = MockFilesystem(
        {
            "/src/a.txt": b"a",
            "/src/b.txt": b"b",
            "/src/c.txt": b"c",
            "/src/d.txt": b"d",
            "/src/e.txt": b"e",
        }
    )
    dst_fs = MockFilesystem()

    first_total: list[int] = []

    def cb(s: TaskStatus) -> None:
        if not first_total:
            first_total.append(s.progress.total)

    status = TaskStatus(cancel_event=threading.Event(), progress_callback=cb)
    srcs = [src_fs.path(p) for p in ("/src/a.txt", "/src/b.txt", "/src/c.txt", "/src/d.txt", "/src/e.txt")]
    await run_task(lambda ctx: copy_files(ctx, srcs, dst_fs.path("/home/user")), status=status)

    assert first_total[0] == 5, f"Expected total=5 on first callback, got {first_total[0]}"
    assert status.progress.total == 5
    assert status.progress.completed == 5


@pytest.mark.asyncio
async def test_copy_file_partial_destination_removed_on_write_error() -> None:
    """BUG-6: partial destination file must be removed after a write error.

    When copy_file raises mid-copy due to a write error, the partially-written
    destination file currently remains on disk.  After the exception propagates,
    the destination path must not exist.
    """
    src_fs = MockFilesystem({"/src/file.txt": b"data"})
    dst_fs = MockFilesystem(write_errors={"/home/user/file.txt": OSError("disk full")})

    src = src_fs.path("/src/file.txt")
    dst = dst_fs.path("/home/user/file.txt")

    with pytest.raises(OSError, match="disk full"):
        await run_task(lambda ctx: copy_file(ctx, src, dst))

    assert not dst_fs.exists("/home/user/file.txt"), "partial destination file was not cleaned up"


@pytest.mark.asyncio
async def test_copy_file_partial_destination_removed_on_read_error() -> None:
    """BUG-6: partial destination file must be removed after a read error.

    When copy_file raises mid-copy due to a read error, the partially-written
    destination file currently remains on disk.  After the exception propagates,
    the destination path must not exist.
    """
    src_fs = MockFilesystem(
        {"/src/file.txt": b"x" * 100},
        read_errors={"/src/file.txt": OSError("disk error")},
    )
    dst_fs = MockFilesystem()

    src = src_fs.path("/src/file.txt")
    dst = dst_fs.path("/home/user/file.txt")

    with pytest.raises(OSError, match="disk error"):
        await run_task(lambda ctx: copy_file(ctx, src, dst))

    assert not dst_fs.exists("/home/user/file.txt"), "partial destination file was not cleaned up"


# ---------------------------------------------------------------------------
# Multiple directories / subdirectories — progress
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_copy_multiple_directories_progress() -> None:
    """Three flat source directories, 2 files each: progress grows from 3 upfront to 9.

    copy_files counts all top-level items (3) upfront; the total then grows as
    _copy_dir discovers 2 files per directory (walk inc_total=2 x 3 = 6).
    Each directory also closes with a root bracket (inc_completed=1 x 3 = 3),
    giving total=9 and completed=9 at completion.
    """
    events: list[tuple[int, int]] = []

    def cb(s: TaskStatus) -> None:
        events.append((s.progress.completed, s.progress.total))

    status = TaskStatus(cancel_event=threading.Event(), progress_callback=cb)
    src_fs = MockFilesystem(
        {
            "/src/A/a1.txt": b"a1",
            "/src/A/a2.txt": b"a2",
            "/src/B/b1.txt": b"b1",
            "/src/B/b2.txt": b"b2",
            "/src/C/c1.txt": b"c1",
            "/src/C/c2.txt": b"c2",
        }
    )
    dst_fs = MockFilesystem()
    srcs = [src_fs.path(p) for p in ("/src/A", "/src/B", "/src/C")]

    await run_task(lambda ctx: copy_files(ctx, srcs, dst_fs.path("/home/user")), status=status)

    assert events[0][1] == 3, f"Expected initial total=3, got {events[0][1]}"
    assert status.progress.total == 9
    assert status.progress.completed == 9


@pytest.mark.asyncio
async def test_copy_dir_immediate_subdir_count() -> None:
    """Root with 3 empty subdirs: total must jump from 1 to 4 after the first walk step.

    Before the fix, _copy_dir discarded the subdirs list from walk(), so the
    total never grew beyond 1 until the subdirs were actually entered one by
    one.  After the fix, inc_total = len(subdirs) + len(files) per walk
    iteration, so the whole batch is counted the moment the root is visited.
    """
    events: list[tuple[int, int]] = []

    def cb(s: TaskStatus) -> None:
        events.append((s.progress.completed, s.progress.total))

    status = TaskStatus(cancel_event=threading.Event(), progress_callback=cb)
    src_fs = MockFilesystem(
        {
            "/src/root/sub1": None,
            "/src/root/sub2": None,
            "/src/root/sub3": None,
        }
    )
    dst_fs = MockFilesystem()

    await run_task(
        lambda ctx: copy_files(ctx, [src_fs.path("/src/root")], dst_fs.path("/home/user/mycopy")),
        status=status,
    )

    # Total must jump to 4 (1 upfront + 3 subdirs) when the root is walked —
    # never pass through 2 or 3 on the way there.
    first_non_unit_total = next(t for _, t in events if t > 1)
    assert first_non_unit_total == 4, f"Expected total to jump to 4, got {first_non_unit_total}"
    assert status.progress.completed == status.progress.total


@pytest.mark.asyncio
async def test_copy_dir_subdir_total_grows_monotonically() -> None:
    """Single directory with 2 subdirs (2 files each): progress invariants and final counts.

    Verifies that at every progress callback: completed ≤ total, total is
    non-decreasing, and completed is non-decreasing.  Final counts must be
    total=7 and completed=7.

    Accounting: 1 (root upfront) + 2 (root's subdirs) + 2 (sub1 files) +
    2 (sub2 files) = 7 total; 1 (sub1 dir bracket) + 1 (sub2 dir bracket) +
    2 + 2 (files) + 1 (root bracket) = 7 completed.
    """
    events: list[tuple[int, int]] = []

    def cb(s: TaskStatus) -> None:
        events.append((s.progress.completed, s.progress.total))

    status = TaskStatus(cancel_event=threading.Event(), progress_callback=cb)
    src_fs = MockFilesystem(
        {
            "/src/root/sub1/f1.txt": b"f1",
            "/src/root/sub1/f2.txt": b"f2",
            "/src/root/sub2/f3.txt": b"f3",
            "/src/root/sub2/f4.txt": b"f4",
        }
    )
    dst_fs = MockFilesystem()

    await run_task(
        lambda ctx: copy_files(ctx, [src_fs.path("/src/root")], dst_fs.path("/home/user/mycopy")),
        status=status,
    )

    prev_completed, prev_total = 0, 0
    for completed, total in events:
        assert completed <= total, f"completed={completed} exceeded total={total}"
        assert total >= prev_total, f"total decreased from {prev_total} to {total}"
        assert completed >= prev_completed, f"completed decreased from {prev_completed} to {completed}"
        prev_completed, prev_total = completed, total
    assert events[-1][1] == 7, f"Expected final total=7, got {events[-1][1]}"
    assert events[-1][0] == 7, f"Expected final completed=7, got {events[-1][0]}"
