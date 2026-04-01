import threading

import pytest

from nova_navigator.filemanager.tasks import _iterate_dirs_and_files
from nova_navigator.task import TaskCancelled, TaskStatus
from nova_navigator.vfs2 import VPath
from tests.mock_filesystem import MockFilesystem

from .common import make_status


def paths_str(result: list[VPath]) -> list[str]:
    return [str(p.path) for p in result]


# ---------------------------------------------------------------------------
# Tests
# ---------------------------------------------------------------------------


def test_iterate_files_single_file() -> None:
    """A plain file yields itself."""
    fs = MockFilesystem({"/home/user/file.txt": b"hello"})
    result = list(_iterate_dirs_and_files(make_status(), fs.path("/home/user/file.txt")))
    assert paths_str(result) == ["/home/user/file.txt"]


def test_iterate_files_single_file_in_directory() -> None:
    """A plain file yields itself."""
    fs = MockFilesystem({"/home/user/file.txt": b"hello"})
    result = list(_iterate_dirs_and_files(make_status(), fs.path("/home/user/")))
    assert paths_str(result) == ["/home/user", "/home/user/file.txt"]


def test_iterate_files_empty_directory() -> None:
    """An empty directory yields nothing."""
    fs = MockFilesystem({"/home/user/empty": None})
    result = list(_iterate_dirs_and_files(make_status(), fs.path("/home/user/empty")))
    assert paths_str(result) == ["/home/user/empty"]


def test_iterate_files_flat_directory() -> None:
    """A directory with only files yields all files, no directories."""
    fs = MockFilesystem(
        {
            "/data/a.txt": b"a",
            "/data/b.txt": b"b",
            "/data/c.txt": b"c",
        }
    )
    result = list(_iterate_dirs_and_files(make_status(), fs.path("/data")))
    assert paths_str(result) == ["/data", "/data/a.txt", "/data/b.txt", "/data/c.txt"]


def test_iterate_files_nested_directories() -> None:
    """Recursively yields all leaf files, not intermediate directories."""
    fs = MockFilesystem(
        {
            "/root/a.txt": b"a",
            "/root/sub/b.txt": b"b",
            "/root/sub/deep/c.txt": b"c",
        }
    )
    result = list(_iterate_dirs_and_files(make_status(), fs.path("/root")))
    assert paths_str(result) == [
        "/root",
        "/root/a.txt",
        "/root/sub",
        "/root/sub/b.txt",
        "/root/sub/deep",
        "/root/sub/deep/c.txt",
    ]


def test_iterate_files_mixed_files_and_dirs() -> None:
    """Directories mixed with files: only files are yielded."""
    fs = MockFilesystem(
        {
            "/root/file.txt": b"x",
            "/root/subdir/nested.txt": b"y",
            "/root/empty_dir": None,
        }
    )
    result = list(_iterate_dirs_and_files(make_status(), fs.path("/root")))
    assert paths_str(result) == [
        "/root",
        "/root/file.txt",
        "/root/subdir",
        "/root/subdir/nested.txt",
        "/root/empty_dir",
    ]


def test_iterate_files_updates_progress() -> None:
    """Progress total is incremented for each directory entry encountered."""
    progress_log: list[tuple[int, int]] = []

    def callback(status: TaskStatus) -> None:
        progress_log.append((status.progress.completed, status.progress.total))

    status = TaskStatus(cancel_event=threading.Event(), progress_callback=callback)

    fs = MockFilesystem(
        {
            "/root/a.txt": b"a",
            "/root/b.txt": b"b",
        }
    )
    list(_iterate_dirs_and_files(status, fs.path("/root")))

    totals = [t for _, t in progress_log]
    completed = [c for c, _ in progress_log]
    assert max(totals) == 2  # two entries in /root
    assert max(completed) == 2  # both marked completed after iteration


def test_iterate_files_cancelled() -> None:
    """Raises TaskCancelled when the cancel event is set before iteration."""
    fs = MockFilesystem(
        {
            "/root/a.txt": b"a",
            "/root/b.txt": b"b",
        }
    )
    status = make_status()
    assert status.cancel_event is not None
    status.cancel_event.set()

    gen = _iterate_dirs_and_files(status, fs.path("/root"))
    with pytest.raises(TaskCancelled):
        list(gen)
