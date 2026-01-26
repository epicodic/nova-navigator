import threading
from pathlib import PurePosixPath

import pytest

from nova_navigator.decision import Decision
from nova_navigator.filemanager.tasks import FileCopyOptions, move_paths
from nova_navigator.scheduler import TaskCancelled
from tests._utils.mock_filesystem import MockFilesystem

from .common import make_status, read_all, run_task

# ---------------------------------------------------------------------------
# Same-device moves (atomic rename)
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_move_single_file_to_new_path() -> None:
    """A file renamed to a non-existent destination path on the same device."""
    fs = MockFilesystem({"/src/file.txt": b"hello"})

    await run_task(lambda ctx: move_paths(ctx, [fs.path("/src/file.txt")], fs.path("/home/user/renamed.txt")))

    assert PurePosixPath("/src/file.txt") not in fs._nodes
    assert read_all(fs, "/home/user/renamed.txt") == b"hello"


@pytest.mark.asyncio
async def test_move_single_file_into_directory() -> None:
    """When destination is an existing directory the file is placed inside it."""
    fs = MockFilesystem({"/src/file.txt": b"data"})

    await run_task(lambda ctx: move_paths(ctx, [fs.path("/src/file.txt")], fs.path("/home/user")))

    assert PurePosixPath("/src/file.txt") not in fs._nodes
    assert read_all(fs, "/home/user/file.txt") == b"data"


@pytest.mark.asyncio
async def test_move_multiple_files_into_directory() -> None:
    """Multiple files are all moved into the destination directory."""
    fs = MockFilesystem({"/src/a.txt": b"a", "/src/b.txt": b"b"})

    srcs = [fs.path("/src/a.txt"), fs.path("/src/b.txt")]
    await run_task(lambda ctx: move_paths(ctx, srcs, fs.path("/home/user")))

    for p in ("/src/a.txt", "/src/b.txt"):
        assert PurePosixPath(p) not in fs._nodes
    assert read_all(fs, "/home/user/a.txt") == b"a"
    assert read_all(fs, "/home/user/b.txt") == b"b"


@pytest.mark.asyncio
async def test_move_overwrite_skip() -> None:
    """skip policy leaves the destination unchanged and does not move the source."""
    fs = MockFilesystem({"/src/file.txt": b"new", "/home/user/file.txt": b"original"})

    requests = await run_task(
        lambda ctx: move_paths(
            ctx, [fs.path("/src/file.txt")], fs.path("/home/user"), FileCopyOptions(overwrite="skip")
        )
    )

    assert len(requests) == 0
    assert PurePosixPath("/src/file.txt") in fs._nodes
    assert read_all(fs, "/home/user/file.txt") == b"original"


@pytest.mark.asyncio
async def test_move_overwrite_ask_yes() -> None:
    """ask + YES: destination is replaced, source is removed."""
    fs = MockFilesystem({"/src/file.txt": b"new", "/home/user/file.txt": b"old"})

    requests = await run_task(
        lambda ctx: move_paths(
            ctx, [fs.path("/src/file.txt")], fs.path("/home/user"), FileCopyOptions(overwrite="ask")
        ),
        [Decision.YES],
    )

    assert len(requests) == 1
    assert PurePosixPath("/src/file.txt") not in fs._nodes
    assert read_all(fs, "/home/user/file.txt") == b"new"


@pytest.mark.asyncio
async def test_move_overwrite_ask_no() -> None:
    """ask + NO: destination is untouched, source is kept."""
    fs = MockFilesystem({"/src/file.txt": b"new", "/home/user/file.txt": b"original"})

    requests = await run_task(
        lambda ctx: move_paths(
            ctx, [fs.path("/src/file.txt")], fs.path("/home/user"), FileCopyOptions(overwrite="ask")
        ),
        [Decision.NO],
    )

    assert len(requests) == 1
    assert PurePosixPath("/src/file.txt") in fs._nodes
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
        lambda ctx: move_paths(ctx, srcs, fs.path("/home/user"), FileCopyOptions(overwrite="ask")),
        [Decision.ALL],
    )

    assert len(requests) == 1
    assert PurePosixPath("/src/a.txt") not in fs._nodes
    assert PurePosixPath("/src/b.txt") not in fs._nodes
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
        lambda ctx: move_paths(ctx, srcs, fs.path("/home/user"), FileCopyOptions(overwrite="ask")),
        [Decision.NONE],
    )

    assert len(requests) == 1
    for p in ("/src/a.txt", "/src/b.txt"):
        assert PurePosixPath(p) in fs._nodes
    assert read_all(fs, "/home/user/a.txt") == b"old-a"
    assert read_all(fs, "/home/user/b.txt") == b"old-b"


@pytest.mark.asyncio
async def test_move_overwrite_existing_directory() -> None:
    """Moving a file onto an existing directory destination erases the dir first."""
    fs = MockFilesystem({"/src/dir": None, "/home/user/dir/file.txt": b"old"})
    # /src/dir is empty; /home/user/dir exists with a file

    await run_task(
        lambda ctx: move_paths(
            ctx, [fs.path("/src/dir")], fs.path("/home/user/dir"), FileCopyOptions(overwrite="overwrite")
        )
    )

    assert PurePosixPath("/src/dir") not in fs._nodes
    assert PurePosixPath("/home/user/dir") in fs._nodes


# ---------------------------------------------------------------------------
# Cross-device moves (copy + remove)
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_move_cross_device_single_file() -> None:
    """Cross-device file move: content is copied, source is removed."""
    src_fs = MockFilesystem({"/src/file.txt": b"cross-device"})
    dst_fs = MockFilesystem()

    await run_task(lambda ctx: move_paths(ctx, [src_fs.path("/src/file.txt")], dst_fs.path("/home/user")))

    assert PurePosixPath("/src/file.txt") not in src_fs._nodes
    assert read_all(dst_fs, "/home/user/file.txt") == b"cross-device"


@pytest.mark.asyncio
async def test_move_cross_device_multiple_files() -> None:
    """Cross-device move of multiple files."""
    src_fs = MockFilesystem({"/src/a.txt": b"aaa", "/src/b.txt": b"bbb"})
    dst_fs = MockFilesystem()

    srcs = [src_fs.path("/src/a.txt"), src_fs.path("/src/b.txt")]
    await run_task(lambda ctx: move_paths(ctx, srcs, dst_fs.path("/home/user")))

    for p in ("/src/a.txt", "/src/b.txt"):
        assert PurePosixPath(p) not in src_fs._nodes
    assert read_all(dst_fs, "/home/user/a.txt") == b"aaa"
    assert read_all(dst_fs, "/home/user/b.txt") == b"bbb"


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

    await run_task(lambda ctx: move_paths(ctx, [src_fs.path("/src/mydir")], dst_fs.path("/home/user")))

    assert PurePosixPath("/src/mydir") not in src_fs._nodes
    assert read_all(dst_fs, "/home/user/mydir/top.txt") == b"top"
    assert read_all(dst_fs, "/home/user/mydir/sub/mid.txt") == b"mid"


# ---------------------------------------------------------------------------
# Progress tracking and cancellation
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_move_progress_tracking() -> None:
    """Progress counters reflect the number of paths processed."""
    fs = MockFilesystem({"/src/a.txt": b"a", "/src/b.txt": b"b", "/src/c.txt": b"c"})

    status = make_status()
    srcs = [fs.path(p) for p in ("/src/a.txt", "/src/b.txt", "/src/c.txt")]
    await run_task(lambda ctx: move_paths(ctx, srcs, fs.path("/home/user")), status=status)

    assert status.progress.total == 3
    assert status.progress.completed == 3


@pytest.mark.asyncio
async def test_move_skipped_path_counted_in_completed() -> None:
    """Skipped paths (overwrite=skip) still increment the completed counter."""
    fs = MockFilesystem({"/src/file.txt": b"new", "/home/user/file.txt": b"original"})

    status = make_status()
    await run_task(
        lambda ctx: move_paths(
            ctx, [fs.path("/src/file.txt")], fs.path("/home/user"), FileCopyOptions(overwrite="skip")
        ),
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
            lambda ctx: move_paths(ctx, [fs.path("/src/file.txt")], fs.path("/home/user")),
            status=status,
        )
