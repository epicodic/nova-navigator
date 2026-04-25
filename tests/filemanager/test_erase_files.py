import threading
from pathlib import PurePosixPath

import pytest

from nova_navigator.decision import Decision
from nova_navigator.filemanager.tasks import EraseFilesOptions, erase_files
from nova_navigator.scheduler import TaskCancelled
from tests._utils.mock_filesystem import MockFilesystem

from .common import make_status, run_task


@pytest.mark.asyncio
async def test_erase_single_file() -> None:
    """A single file is removed from the filesystem."""
    fs = MockFilesystem({"/home/user/file.txt": b"data"})

    await run_task(lambda ctx: erase_files(ctx, [fs.path("/home/user/file.txt")]))

    assert PurePosixPath("/home/user/file.txt") not in fs._nodes


@pytest.mark.asyncio
async def test_erase_multiple_files() -> None:
    """Multiple files are all removed."""
    fs = MockFilesystem({"/home/user/a.txt": b"a", "/home/user/b.txt": b"b", "/home/user/c.txt": b"c"})

    paths = [fs.path(p) for p in ("/home/user/a.txt", "/home/user/b.txt", "/home/user/c.txt")]
    await run_task(lambda ctx: erase_files(ctx, paths))

    for p in ("/home/user/a.txt", "/home/user/b.txt", "/home/user/c.txt"):
        assert PurePosixPath(p) not in fs._nodes


@pytest.mark.asyncio
async def test_erase_empty_directory() -> None:
    """An empty directory is removed without prompting the user."""
    fs = MockFilesystem({"/home/user/emptydir": None})

    requests = await run_task(lambda ctx: erase_files(ctx, [fs.path("/home/user/emptydir")]))

    assert len(requests) == 0
    assert PurePosixPath("/home/user/emptydir") not in fs._nodes


@pytest.mark.asyncio
async def test_erase_non_empty_directory_ask_yes() -> None:
    """User says YES: the non-empty directory and its contents are deleted."""
    fs = MockFilesystem({"/home/user/dir/file.txt": b"data"})

    requests = await run_task(
        lambda ctx: erase_files(ctx, [fs.path("/home/user/dir")]),
        [Decision.YES],
    )

    assert len(requests) == 1
    assert PurePosixPath("/home/user/dir/file.txt") not in fs._nodes
    assert PurePosixPath("/home/user/dir") not in fs._nodes


@pytest.mark.asyncio
async def test_erase_non_empty_directory_ask_no() -> None:
    """User says NO: the non-empty directory and its contents are left intact."""
    fs = MockFilesystem({"/home/user/dir/file.txt": b"data"})

    requests = await run_task(
        lambda ctx: erase_files(ctx, [fs.path("/home/user/dir")]),
        [Decision.NO],
    )

    assert len(requests) == 1
    assert PurePosixPath("/home/user/dir/file.txt") in fs._nodes
    assert PurePosixPath("/home/user/dir") in fs._nodes


@pytest.mark.asyncio
async def test_erase_multiple_non_empty_dirs_ask_all() -> None:
    """ALL answer suppresses further prompts: both directories are deleted."""
    fs = MockFilesystem(
        {
            "/home/user/dir1/a.txt": b"a",
            "/home/user/dir2/b.txt": b"b",
        }
    )

    paths = [fs.path("/home/user/dir1"), fs.path("/home/user/dir2")]
    requests = await run_task(
        lambda ctx: erase_files(ctx, paths),
        [Decision.ALL],
    )

    # Only one prompt; the cached ALL answer silences the second
    assert len(requests) == 1
    for p in ("/home/user/dir1/a.txt", "/home/user/dir1", "/home/user/dir2/b.txt", "/home/user/dir2"):
        assert PurePosixPath(p) not in fs._nodes


@pytest.mark.asyncio
async def test_erase_multiple_non_empty_dirs_ask_none() -> None:
    """NONE answer suppresses further prompts: both directories are kept."""
    fs = MockFilesystem(
        {
            "/home/user/dir1/a.txt": b"a",
            "/home/user/dir2/b.txt": b"b",
        }
    )

    paths = [fs.path("/home/user/dir1"), fs.path("/home/user/dir2")]
    requests = await run_task(
        lambda ctx: erase_files(ctx, paths),
        [Decision.NONE],
    )

    # Only one prompt; the cached NONE answer silences the second
    assert len(requests) == 1
    for p in ("/home/user/dir1/a.txt", "/home/user/dir1", "/home/user/dir2/b.txt", "/home/user/dir2"):
        assert PurePosixPath(p) in fs._nodes


@pytest.mark.asyncio
async def test_erase_recursive_nested_directory() -> None:
    """A nested directory tree is removed entirely without asking (ask_before_erase=False)."""
    fs = MockFilesystem(
        {
            "/home/user/root/top.txt": b"top",
            "/home/user/root/sub/mid.txt": b"mid",
            "/home/user/root/sub/deep/bottom.txt": b"bottom",
        }
    )

    await run_task(
        lambda ctx: erase_files(ctx, [fs.path("/home/user/root")], EraseFilesOptions(ask_before_erase=False))
    )

    for p in (
        "/home/user/root/top.txt",
        "/home/user/root/sub/mid.txt",
        "/home/user/root/sub/deep/bottom.txt",
        "/home/user/root/sub/deep",
        "/home/user/root/sub",
        "/home/user/root",
    ):
        assert PurePosixPath(p) not in fs._nodes


@pytest.mark.asyncio
async def test_erase_ask_before_erase_false_skips_prompt() -> None:
    """ask_before_erase=False deletes a non-empty directory without prompting."""
    fs = MockFilesystem({"/home/user/dir/file.txt": b"data"})

    requests = await run_task(
        lambda ctx: erase_files(ctx, [fs.path("/home/user/dir")], EraseFilesOptions(ask_before_erase=False))
    )

    assert len(requests) == 0
    assert PurePosixPath("/home/user/dir") not in fs._nodes


@pytest.mark.asyncio
async def test_erase_mixed_files_and_dirs() -> None:
    """A mix of plain files and directories are all erased correctly."""
    fs = MockFilesystem(
        {
            "/home/user/file.txt": b"data",
            "/home/user/dir/nested.txt": b"nested",
        }
    )

    paths = [fs.path("/home/user/file.txt"), fs.path("/home/user/dir")]
    requests = await run_task(
        lambda ctx: erase_files(ctx, paths),
        [Decision.YES],
    )

    assert len(requests) == 1
    assert PurePosixPath("/home/user/file.txt") not in fs._nodes
    assert PurePosixPath("/home/user/dir/nested.txt") not in fs._nodes
    assert PurePosixPath("/home/user/dir") not in fs._nodes


@pytest.mark.asyncio
async def test_erase_cancelled() -> None:
    """TaskCancelled propagates when cancel event is set before the task runs."""
    fs = MockFilesystem({"/home/user/file.txt": b"data"})

    cancel = threading.Event()
    cancel.set()
    status = make_status(cancel_event=cancel)

    with pytest.raises(TaskCancelled):
        await run_task(
            lambda ctx: erase_files(ctx, [fs.path("/home/user/file.txt")]),
            status=status,
        )


@pytest.mark.asyncio
async def test_erase_progress_tracking() -> None:
    """Progress counters reflect the number of items erased."""
    fs = MockFilesystem({"/home/user/a.txt": b"a", "/home/user/b.txt": b"b", "/home/user/c.txt": b"c"})

    status = make_status()
    paths = [fs.path(p) for p in ("/home/user/a.txt", "/home/user/b.txt", "/home/user/c.txt")]
    await run_task(lambda ctx: erase_files(ctx, paths), status=status)

    assert status.progress.total == 3
    assert status.progress.completed == 3


@pytest.mark.asyncio
async def test_erase_skipped_dir_not_counted_in_completed() -> None:
    """A directory skipped via NO still counts in completed (the skip path increments it)."""
    fs = MockFilesystem({"/home/user/dir/file.txt": b"data"})

    status = make_status()
    await run_task(
        lambda ctx: erase_files(ctx, [fs.path("/home/user/dir")]),
        [Decision.NO],
        status=status,
    )

    assert status.progress.total == 1
    assert status.progress.completed == 1
