import threading

import pytest

from nova_navigator.filemanager.tasks import CHUNK_SIZE, FileCopyOptions, copy_file
from nova_navigator.task import DecisionResponse, TaskCancelled
from tests.mock_filesystem import MockFilesystem

from .common import make_status, run_task

# ---------------------------------------------------------------------------
# Tests
# ---------------------------------------------------------------------------


def test_copy_file_simple() -> None:
    """Copies a file between two MockFilesystem instances."""
    content = b"hello from memory"
    src_fs = MockFilesystem({"/src/file.txt": content})
    dst_fs = MockFilesystem()

    src = src_fs.path("/src/file.txt")
    dst = dst_fs.path("/home/user/file.txt")

    run_task(copy_file(make_status(), src, dst))

    assert dst_fs.path("/home/user/file.txt").stat.size == len(content)
    reader = dst_fs.read(dst)
    assert reader.read(len(content)) == content
    reader.close()
    assert src_fs.readers[0].close_count == 1
    assert dst_fs.writers[0].close_count == 1


def test_copy_file_overwrite_skip() -> None:
    """skip policy leaves dst unchanged when dst exists."""
    src_fs = MockFilesystem({"/src/file.txt": b"new"})
    dst_fs = MockFilesystem({"/home/user/file.txt": b"original"})

    src = src_fs.path("/src/file.txt")
    dst = dst_fs.path("/home/user/file.txt")

    run_task(copy_file(make_status(), src, dst, FileCopyOptions(overwrite="skip")))

    assert len(dst_fs.writers) == 0
    assert src_fs.readers[0].close_count == 1
    reader = dst_fs.read(dst)
    assert reader.read(1024) == b"original"
    reader.close()


def test_copy_file_overwrite_force() -> None:
    """overwrite policy replaces dst content without asking."""
    src_fs = MockFilesystem({"/src/file.txt": b"new content"})
    dst_fs = MockFilesystem({"/home/user/file.txt": b"old"})

    src = src_fs.path("/src/file.txt")
    dst = dst_fs.path("/home/user/file.txt")

    run_task(copy_file(make_status(), src, dst, FileCopyOptions(overwrite="overwrite")))

    assert src_fs.readers[0].close_count == 1
    assert dst_fs.writers[0].close_count == 1
    reader = dst_fs.read(dst)
    assert reader.read(1024) == b"new content"
    reader.close()


def test_copy_file_ask_yes() -> None:
    """ask policy prompts the user; YES overwrites dst."""
    src_fs = MockFilesystem({"/src/file.txt": b"replacement"})
    dst_fs = MockFilesystem({"/home/user/file.txt": b"old"})

    src = src_fs.path("/src/file.txt")
    dst = dst_fs.path("/home/user/file.txt")

    requests = run_task(
        copy_file(make_status(), src, dst, FileCopyOptions(overwrite="ask")),
        [DecisionResponse.YES],
    )

    assert len(requests) == 1
    assert src_fs.readers[0].close_count == 1
    assert dst_fs.writers[0].close_count == 1
    reader = dst_fs.read(dst)
    assert reader.read(1024) == b"replacement"
    reader.close()


def test_copy_file_ask_no() -> None:
    """ask policy prompts the user; NO leaves dst unchanged."""
    src_fs = MockFilesystem({"/src/file.txt": b"replacement"})
    dst_fs = MockFilesystem({"/home/user/file.txt": b"original"})

    src = src_fs.path("/src/file.txt")
    dst = dst_fs.path("/home/user/file.txt")

    requests = run_task(
        copy_file(make_status(), src, dst, FileCopyOptions(overwrite="ask")),
        [DecisionResponse.NO],
    )

    assert len(requests) == 1
    assert len(dst_fs.writers) == 0
    assert src_fs.readers[0].close_count == 1
    reader = dst_fs.read(dst)
    assert reader.read(1024) == b"original"
    reader.close()


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
    reader = fs.read(dst)
    assert reader.read(len(content)) == content
    reader.close()


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
    reader = dst_fs.read(dst)
    assert reader.read(len(content) + 1) == content
    reader.close()


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
