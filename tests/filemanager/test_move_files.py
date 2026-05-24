import threading

import pytest

from nova_navigator.filemanager.tasks import FileCopyOptions, move_files
from nova_navigator.response import Response
from nova_navigator.scheduler import TaskCancelled, TaskStatus
from tests._utils.mock_filesystem import MockFilesystem

from .common import make_status, read_all, run_task

# ---------------------------------------------------------------------------
# Same-device moves (atomic rename)
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_move_single_file_to_new_path() -> None:
    """A file renamed to a non-existent destination path on the same device."""
    fs = MockFilesystem({"/src/file.txt": b"hello"})

    await run_task(lambda ctx: move_files(ctx, [fs.path("/src/file.txt")], fs.path("/home/user/renamed.txt")))

    assert not fs.exists("/src/file.txt")
    assert read_all(fs, "/home/user/renamed.txt") == b"hello"


@pytest.mark.asyncio
async def test_move_single_file_into_directory() -> None:
    """When destination is an existing directory the file is placed inside it."""
    fs = MockFilesystem({"/src/file.txt": b"data"})

    await run_task(lambda ctx: move_files(ctx, [fs.path("/src/file.txt")], fs.path("/home/user")))

    assert not fs.exists("/src/file.txt")
    assert read_all(fs, "/home/user/file.txt") == b"data"


@pytest.mark.asyncio
async def test_move_multiple_files_into_directory() -> None:
    """Multiple files are all moved into the destination directory."""
    fs = MockFilesystem({"/src/a.txt": b"a", "/src/b.txt": b"b"})

    srcs = [fs.path("/src/a.txt"), fs.path("/src/b.txt")]
    await run_task(lambda ctx: move_files(ctx, srcs, fs.path("/home/user")))

    for p in ("/src/a.txt", "/src/b.txt"):
        assert not fs.exists(p)
    assert read_all(fs, "/home/user/a.txt") == b"a"
    assert read_all(fs, "/home/user/b.txt") == b"b"


@pytest.mark.asyncio
async def test_move_overwrite_skip() -> None:
    """skip policy leaves the destination unchanged and does not move the source."""
    fs = MockFilesystem({"/src/file.txt": b"new", "/home/user/file.txt": b"original"})

    requests = await run_task(lambda ctx: move_files(ctx, [fs.path("/src/file.txt")], fs.path("/home/user"), FileCopyOptions(overwrite="skip")))

    assert len(requests) == 0
    assert fs.exists("/src/file.txt")
    assert read_all(fs, "/home/user/file.txt") == b"original"


@pytest.mark.asyncio
async def test_move_overwrite_ask_yes() -> None:
    """ask + YES: destination is replaced, source is removed."""
    fs = MockFilesystem({"/src/file.txt": b"new", "/home/user/file.txt": b"old"})

    requests = await run_task(
        lambda ctx: move_files(ctx, [fs.path("/src/file.txt")], fs.path("/home/user"), FileCopyOptions(overwrite="ask")),
        [Response.YES],
    )

    assert len(requests) == 1
    assert not fs.exists("/src/file.txt")
    assert read_all(fs, "/home/user/file.txt") == b"new"


@pytest.mark.asyncio
async def test_move_overwrite_ask_no() -> None:
    """ask + NO: destination is untouched, source is kept."""
    fs = MockFilesystem({"/src/file.txt": b"new", "/home/user/file.txt": b"original"})

    requests = await run_task(
        lambda ctx: move_files(ctx, [fs.path("/src/file.txt")], fs.path("/home/user"), FileCopyOptions(overwrite="ask")),
        [Response.NO],
    )

    assert len(requests) == 1
    assert fs.exists("/src/file.txt")
    assert read_all(fs, "/home/user/file.txt") == b"original"


@pytest.mark.asyncio
async def test_move_overwrite_ask_all() -> None:
    """ALL answer is cached: both files are overwritten with only one prompt."""
    fs = MockFilesystem(
        {
            "/src/a.txt": b"new-a",
            "/src/b.txt": b"new-b",
            "/home/user/a.txt": b"old-a",
            "/home/user/b.txt": b"old-b",
        }
    )

    srcs = [fs.path("/src/a.txt"), fs.path("/src/b.txt")]
    requests = await run_task(
        lambda ctx: move_files(ctx, srcs, fs.path("/home/user"), FileCopyOptions(overwrite="ask")),
        [Response.ALL],
    )

    assert len(requests) == 1
    assert not fs.exists("/src/a.txt")
    assert not fs.exists("/src/b.txt")
    assert read_all(fs, "/home/user/a.txt") == b"new-a"
    assert read_all(fs, "/home/user/b.txt") == b"new-b"


@pytest.mark.asyncio
async def test_move_overwrite_ask_none() -> None:
    """NONE answer is cached: both files are kept with only one prompt."""
    fs = MockFilesystem(
        {
            "/src/a.txt": b"new-a",
            "/src/b.txt": b"new-b",
            "/home/user/a.txt": b"old-a",
            "/home/user/b.txt": b"old-b",
        }
    )

    srcs = [fs.path("/src/a.txt"), fs.path("/src/b.txt")]
    requests = await run_task(
        lambda ctx: move_files(ctx, srcs, fs.path("/home/user"), FileCopyOptions(overwrite="ask")),
        [Response.NONE],
    )

    assert len(requests) == 1
    for p in ("/src/a.txt", "/src/b.txt"):
        assert fs.exists(p)
    assert read_all(fs, "/home/user/a.txt") == b"old-a"
    assert read_all(fs, "/home/user/b.txt") == b"old-b"


@pytest.mark.asyncio
async def test_move_overwrite_existing_directory() -> None:
    """Moving a file onto an existing directory destination erases the dir first."""
    fs = MockFilesystem({"/src/dir": None, "/home/user/dir/file.txt": b"old"})
    # /src/dir is empty; /home/user/dir exists with a file

    await run_task(lambda ctx: move_files(ctx, [fs.path("/src/dir")], fs.path("/home/user/dir"), FileCopyOptions(overwrite="overwrite")))

    assert not fs.exists("/src/dir")
    assert fs.exists("/home/user/dir")


# ---------------------------------------------------------------------------
# Cross-device moves (copy + remove)
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_move_cross_device_single_file() -> None:
    """Cross-device file move: content is copied, source is removed."""
    src_fs = MockFilesystem({"/src/file.txt": b"cross-device"})
    dst_fs = MockFilesystem()

    await run_task(lambda ctx: move_files(ctx, [src_fs.path("/src/file.txt")], dst_fs.path("/home/user")))

    assert not src_fs.exists("/src/file.txt")
    assert read_all(dst_fs, "/home/user/file.txt") == b"cross-device"


@pytest.mark.asyncio
async def test_move_cross_device_multiple_files() -> None:
    """Cross-device move of multiple files."""
    src_fs = MockFilesystem({"/src/a.txt": b"aaa", "/src/b.txt": b"bbb"})
    dst_fs = MockFilesystem()

    srcs = [src_fs.path("/src/a.txt"), src_fs.path("/src/b.txt")]
    await run_task(lambda ctx: move_files(ctx, srcs, dst_fs.path("/home/user")))

    for p in ("/src/a.txt", "/src/b.txt"):
        assert not src_fs.exists(p)
    assert read_all(dst_fs, "/home/user/a.txt") == b"aaa"
    assert read_all(dst_fs, "/home/user/b.txt") == b"bbb"


@pytest.mark.asyncio
async def test_move_cross_device_skip_does_not_delete_source() -> None:
    """BUG-1: cross-device move with skip policy must not delete source when destination exists."""
    src_fs = MockFilesystem({"/src/file.txt": b"new"})
    dst_fs = MockFilesystem({"/home/user/file.txt": b"original"})

    await run_task(lambda ctx: move_files(ctx, [src_fs.path("/src/file.txt")], dst_fs.path("/home/user"), FileCopyOptions(overwrite="skip")))

    assert src_fs.exists("/src/file.txt")
    assert read_all(dst_fs, "/home/user/file.txt") == b"original"


@pytest.mark.asyncio
async def test_move_cross_device_ask_no_does_not_delete_source() -> None:
    """BUG-1: cross-device move with ask+NO must not delete source when destination exists."""
    src_fs = MockFilesystem({"/src/file.txt": b"new"})
    dst_fs = MockFilesystem({"/home/user/file.txt": b"original"})

    requests = await run_task(
        lambda ctx: move_files(ctx, [src_fs.path("/src/file.txt")], dst_fs.path("/home/user"), FileCopyOptions(overwrite="ask")),
        [Response.NO],
    )

    assert len(requests) == 1
    assert src_fs.exists("/src/file.txt")
    assert read_all(dst_fs, "/home/user/file.txt") == b"original"


@pytest.mark.asyncio
async def test_move_cross_device_directory() -> None:
    """Cross-device directory move preserves full structure and removes source."""
    src_fs = MockFilesystem(
        {
            "/src/mydir/top.txt": b"top",
            "/src/mydir/sub/mid.txt": b"mid",
        }
    )
    dst_fs = MockFilesystem()

    await run_task(lambda ctx: move_files(ctx, [src_fs.path("/src/mydir")], dst_fs.path("/home/user")))

    assert not src_fs.exists("/src/mydir")
    assert read_all(dst_fs, "/home/user/mydir/top.txt") == b"top"
    assert read_all(dst_fs, "/home/user/mydir/sub/mid.txt") == b"mid"


@pytest.mark.asyncio
async def test_move_cross_device_directory_skip_does_not_erase_source() -> None:
    """BUG-2: cross-device dir move with skip policy must not erase source when a file was skipped."""
    src_fs = MockFilesystem({"/src/mydir/file.txt": b"new"})
    dst_fs = MockFilesystem({"/home/user/mydir/file.txt": b"original"})

    await run_task(lambda ctx: move_files(ctx, [src_fs.path("/src/mydir")], dst_fs.path("/home/user"), FileCopyOptions(overwrite="skip")))

    assert src_fs.exists("/src/mydir/file.txt")
    assert read_all(dst_fs, "/home/user/mydir/file.txt") == b"original"


@pytest.mark.asyncio
async def test_move_cross_device_directory_ask_no_does_not_erase_source() -> None:
    """BUG-2: cross-device dir move with ask+NO must not erase source when a file was skipped."""
    src_fs = MockFilesystem({"/src/mydir/file.txt": b"new"})
    dst_fs = MockFilesystem({"/home/user/mydir/file.txt": b"original"})

    requests = await run_task(
        lambda ctx: move_files(ctx, [src_fs.path("/src/mydir")], dst_fs.path("/home/user"), FileCopyOptions(overwrite="ask")),
        [Response.NO],
    )

    assert len(requests) == 1
    assert src_fs.exists("/src/mydir/file.txt")
    assert read_all(dst_fs, "/home/user/mydir/file.txt") == b"original"


@pytest.mark.asyncio
async def test_move_cross_device_mixed_skip() -> None:
    """Cross-device move of multiple dirs (including nested) where some files are skipped.

    src:
      A/{a1.txt, a2.txt}
      B/{b1.txt, b2.txt}
      C/sub1/{c11.txt, c12.txt}, C/sub2/{c21.txt, c22.txt}
    dst:
      B/b1.txt, C/sub2/c21.txt  (already exist)

    Expected after skip move:
      src: B/b1.txt, C/sub2/c21.txt survive (skipped);
           B and C/sub2 and C dirs survive as ancestors of skipped files
      dst: A fully present, B/b2.txt added, C/sub1 fully present,
           C/sub2/c21.txt unchanged, C/sub2/c22.txt added
    """
    src_fs = MockFilesystem(
        {
            "/src/A/a1.txt": b"a1",
            "/src/A/a2.txt": b"a2",
            "/src/B/b1.txt": b"b1-new",
            "/src/B/b2.txt": b"b2",
            "/src/C/sub1/c11.txt": b"c11",
            "/src/C/sub1/c12.txt": b"c12",
            "/src/C/sub2/c21.txt": b"c21-new",
            "/src/C/sub2/c22.txt": b"c22",
        }
    )
    dst_fs = MockFilesystem(
        {
            "/home/user/B/b1.txt": b"b1-original",
            "/home/user/C/sub2/c21.txt": b"c21-original",
        }
    )

    srcs = [src_fs.path("/src/A"), src_fs.path("/src/B"), src_fs.path("/src/C")]
    await run_task(lambda ctx: move_files(ctx, srcs, dst_fs.path("/home/user"), FileCopyOptions(overwrite="skip")))

    # A was fully moved
    assert not src_fs.exists("/src/A/a1.txt")
    assert not src_fs.exists("/src/A/a2.txt")
    assert not src_fs.exists("/src/A")
    assert read_all(dst_fs, "/home/user/A/a1.txt") == b"a1"
    assert read_all(dst_fs, "/home/user/A/a2.txt") == b"a2"

    # b1.txt was skipped: source survives, destination unchanged
    assert src_fs.exists("/src/B/b1.txt")
    assert read_all(dst_fs, "/home/user/B/b1.txt") == b"b1-original"
    # b2.txt had no conflict: moved successfully
    assert not src_fs.exists("/src/B/b2.txt")
    assert read_all(dst_fs, "/home/user/B/b2.txt") == b"b2"
    # /src/B survives because b1.txt was not moved
    assert src_fs.exists("/src/B")

    # C/sub1 was fully moved
    assert not src_fs.exists("/src/C/sub1/c11.txt")
    assert not src_fs.exists("/src/C/sub1/c12.txt")
    assert not src_fs.exists("/src/C/sub1")
    assert read_all(dst_fs, "/home/user/C/sub1/c11.txt") == b"c11"
    assert read_all(dst_fs, "/home/user/C/sub1/c12.txt") == b"c12"

    # c21.txt was skipped: source survives, destination unchanged
    assert src_fs.exists("/src/C/sub2/c21.txt")
    assert read_all(dst_fs, "/home/user/C/sub2/c21.txt") == b"c21-original"
    # c22.txt had no conflict: moved successfully
    assert not src_fs.exists("/src/C/sub2/c22.txt")
    assert read_all(dst_fs, "/home/user/C/sub2/c22.txt") == b"c22"
    # /src/C/sub2 and /src/C survive because c21.txt was not moved
    assert src_fs.exists("/src/C/sub2")
    assert src_fs.exists("/src/C")


@pytest.mark.asyncio
async def test_move_same_device_mixed_skip() -> None:
    """Same-device (rename) move of A, B, C where some files conflict.

    Per-file skip granularity is expected: individual files are skipped when
    their destination exists, but non-conflicting files in the same directory
    are still moved.

    src:
      A/{a1.txt, a2.txt}
      B/{b1.txt, b2.txt}
      C/sub1/{c11.txt, c12.txt}, C/sub2/{c21.txt, c22.txt}
    dst:
      B/b1.txt, C/sub2/c21.txt  (already exist)

    Expected after skip move (same as cross-device):
      src: B/b1.txt and C/sub2/c21.txt survive; their ancestor dirs survive
      dst: A fully present, B/b2.txt added, C/sub1 fully present,
           C/sub2/c21.txt unchanged, C/sub2/c22.txt added
    """
    fs = MockFilesystem(
        {
            "/src/A/a1.txt": b"a1",
            "/src/A/a2.txt": b"a2",
            "/src/B/b1.txt": b"b1-new",
            "/src/B/b2.txt": b"b2",
            "/src/C/sub1/c11.txt": b"c11",
            "/src/C/sub1/c12.txt": b"c12",
            "/src/C/sub2/c21.txt": b"c21-new",
            "/src/C/sub2/c22.txt": b"c22",
            "/home/user/B/b1.txt": b"b1-original",
            "/home/user/C/sub2/c21.txt": b"c21-original",
        }
    )

    srcs = [fs.path("/src/A"), fs.path("/src/B"), fs.path("/src/C")]
    await run_task(lambda ctx: move_files(ctx, srcs, fs.path("/home/user"), FileCopyOptions(overwrite="skip")))

    # A had no conflict: fully moved
    assert not fs.exists("/src/A")
    assert read_all(fs, "/home/user/A/a1.txt") == b"a1"
    assert read_all(fs, "/home/user/A/a2.txt") == b"a2"

    # b1.txt was skipped: source survives, destination unchanged
    assert fs.exists("/src/B/b1.txt")
    assert read_all(fs, "/home/user/B/b1.txt") == b"b1-original"
    # b2.txt had no conflict: moved successfully
    assert not fs.exists("/src/B/b2.txt")
    assert read_all(fs, "/home/user/B/b2.txt") == b"b2"
    # /src/B survives because b1.txt was not moved
    assert fs.exists("/src/B")

    # C/sub1 was fully moved
    assert not fs.exists("/src/C/sub1")
    assert read_all(fs, "/home/user/C/sub1/c11.txt") == b"c11"
    assert read_all(fs, "/home/user/C/sub1/c12.txt") == b"c12"

    # c21.txt was skipped: source survives, destination unchanged
    assert fs.exists("/src/C/sub2/c21.txt")
    assert read_all(fs, "/home/user/C/sub2/c21.txt") == b"c21-original"
    # c22.txt had no conflict: moved successfully
    assert not fs.exists("/src/C/sub2/c22.txt")
    assert read_all(fs, "/home/user/C/sub2/c22.txt") == b"c22"
    # /src/C/sub2 and /src/C survive because c21.txt was not moved
    assert fs.exists("/src/C/sub2")
    assert fs.exists("/src/C")


# ---------------------------------------------------------------------------
# Progress tracking and cancellation
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_move_progress_tracking() -> None:
    """Progress counters reflect the number of paths processed."""
    fs = MockFilesystem({"/src/a.txt": b"a", "/src/b.txt": b"b", "/src/c.txt": b"c"})

    status = make_status()
    srcs = [fs.path(p) for p in ("/src/a.txt", "/src/b.txt", "/src/c.txt")]
    await run_task(lambda ctx: move_files(ctx, srcs, fs.path("/home/user")), status=status)

    assert status.progress.total == 3
    assert status.progress.completed == 3


@pytest.mark.asyncio
async def test_move_skipped_path_counted_in_completed() -> None:
    """Skipped paths (overwrite=skip) still increment the completed counter."""
    fs = MockFilesystem({"/src/file.txt": b"new", "/home/user/file.txt": b"original"})

    status = make_status()
    await run_task(
        lambda ctx: move_files(ctx, [fs.path("/src/file.txt")], fs.path("/home/user"), FileCopyOptions(overwrite="skip")),
        status=status,
    )

    assert status.progress.total == 1
    assert status.progress.completed == 1


@pytest.mark.asyncio
async def test_move_cancelled() -> None:
    """TaskCancelled propagates when the cancel event is set before the task runs."""
    fs = MockFilesystem({"/src/file.txt": b"data"})

    cancel = threading.Event()
    cancel.set()
    status = make_status(cancel_event=cancel)

    with pytest.raises(TaskCancelled):
        await run_task(
            lambda ctx: move_files(ctx, [fs.path("/src/file.txt")], fs.path("/home/user")),
            status=status,
        )


@pytest.mark.asyncio
async def test_move_directory_progress_total() -> None:
    """Moving a directory of 3 files must report a total of at least 3 in the progress.

    BUG: move_files only increments total by len(src_paths) (i.e. 1 for the
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
        lambda ctx: move_files(ctx, [src_fs.path("/src/mydir")], dst_fs.path("/home/user")),
        status=status,
    )

    assert status.progress.total >= 3
    assert status.progress.completed == status.progress.total
    assert status.progress.step_completed == status.progress.step_total


@pytest.mark.asyncio
async def test_move_plain_files_total_known_upfront() -> None:
    """Moving 5 plain files must set progress.total to 5 before any file is processed.

    When all src_paths are plain files, move_files knows the count immediately
    and must report total=5 on the very first progress callback, before any
    individual file has been completed.
    """
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
    await run_task(lambda ctx: move_files(ctx, srcs, dst_fs.path("/home/user")), status=status)

    assert first_total[0] == 5, f"Expected total=5 on first callback, got {first_total[0]}"
    assert status.progress.total == 5
    assert status.progress.completed == 5


# ---------------------------------------------------------------------------
# Multiple directories / subdirectories — progress
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_move_cross_device_multiple_directories_progress() -> None:
    """Three flat directories, 2 files each, cross-device: progress grows from 3 to 9.

    move_files counts all top-level items (3) upfront; _move_dir_contents
    discovers 2 files per directory (walk inc_total=2 x 3 = 6).  The root
    bracket from _move_path adds 3 more completed, giving total=9 and
    completed=9 at completion.
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

    await run_task(lambda ctx: move_files(ctx, srcs, dst_fs.path("/home/user")), status=status)

    assert events[0][1] == 3, f"Expected initial total=3, got {events[0][1]}"
    assert status.progress.total == 9
    assert status.progress.completed == 9


@pytest.mark.asyncio
async def test_move_dir_immediate_subdir_count() -> None:
    """Root with 3 empty subdirs (cross-device): total must jump from 1 to 4 after root walk.

    Mirrors test_copy_dir_immediate_subdir_count for the _move_dir_contents
    code path.  Without the walk-subdirs fix the total would stay at 1 until
    each subdir was entered individually.
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
        lambda ctx: move_files(ctx, [src_fs.path("/src/root")], dst_fs.path("/home/user/mycopy")),
        status=status,
    )

    first_non_unit_total = next(t for _, t in events if t > 1)
    assert first_non_unit_total == 4, f"Expected total to jump to 4, got {first_non_unit_total}"
    assert status.progress.completed == status.progress.total


@pytest.mark.asyncio
async def test_move_dir_subdir_total_grows_monotonically() -> None:
    """Single directory with 2 subdirs (2 files each), cross-device: invariants and final counts.

    Verifies that at every progress callback: completed ≤ total, total is
    non-decreasing, and completed is non-decreasing.  Final total=7 and
    completed=7 (same accounting as the equivalent copy test).
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
        lambda ctx: move_files(ctx, [src_fs.path("/src/root")], dst_fs.path("/home/user/mycopy")),
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
