"""`SaveJob` failure injection: every failing operation leaves the original untouched and no temp file."""

from __future__ import annotations

import errno
from collections import Counter
from pathlib import Path

import pytest

from nova_editor.core.save import SaveFailed, SaveIo, _cleanup_temp_files

from .save_helpers import ORIGINAL, counting_io, failing_io, make_job

EXPECTED_STAGE = {"write": "write", "fsync": "flush", "fchmod": "flush", "replace": "replace", "fallocate": "prepare", "mkstemp": "prepare"}


def calls_of(tmp_path: Path, op: str) -> int:
    probe = tmp_path / "probe"
    probe.write_bytes(ORIGINAL)
    counter: Counter[str] = Counter()
    job, source = make_job(probe, io=counting_io(counter))
    job.run().source.close()
    source.close()
    probe.unlink()
    return counter[op]


@pytest.mark.parametrize("op", ["write", "fsync", "fchmod", "replace", "fallocate", "mkstemp"])
@pytest.mark.parametrize("errno_", [errno.ENOSPC, errno.EIO, errno.EACCES])
def test_failure_leaves_original_and_no_temp(tmp_path: Path, op: str, errno_: int) -> None:
    count = calls_of(tmp_path, op)
    assert count >= 1
    if op == "write":
        assert count == 9  # 60 bytes in chunks of 7
    target = tmp_path / "f.txt"
    target.write_bytes(ORIGINAL)
    for at in range(1, count + 1):
        job, source = make_job(target, io=failing_io(op, at, errno_))
        with pytest.raises(SaveFailed) as info:
            job.run()
        source.close()
        assert info.value.stage == EXPECTED_STAGE[op]
        assert info.value.errno == errno_
        assert target.read_bytes() == ORIGINAL
        assert [p.name for p in tmp_path.iterdir()] == ["f.txt"]


def test_fsync_dir_failure_is_not_fatal(tmp_path: Path) -> None:
    target = tmp_path / "f.txt"
    target.write_bytes(ORIGINAL)
    job, source = make_job(target, io=failing_io("fsync_dir", 1, errno.EIO))
    result = job.run()
    assert target.read_bytes() == ORIGINAL
    result.source.close()
    source.close()


@pytest.mark.parametrize("op", ["fallocate", "fchmod"])
def test_unsupported_operations_are_tolerated(tmp_path: Path, op: str) -> None:
    target = tmp_path / "f.txt"
    target.write_bytes(ORIGINAL)
    job, source = make_job(target, io=failing_io(op, 1, errno.ENOTSUP))
    result = job.run()
    assert target.read_bytes() == ORIGINAL
    result.source.close()
    source.close()


def test_forced_abort_is_cleaned_by_the_exit_hook(tmp_path: Path) -> None:
    target = tmp_path / "f.txt"
    target.write_bytes(ORIGINAL)
    io = SaveIo()

    def interrupted(src: str, dst: str) -> None:
        del src, dst
        raise KeyboardInterrupt

    def stuck_unlink(name: str) -> None:
        raise OSError(errno.EIO, "unlink refused", name)

    io.replace = interrupted
    io.unlink = stuck_unlink
    job, source = make_job(target, io=io)
    with pytest.raises(KeyboardInterrupt):
        job.run()
    source.close()
    assert len([p for p in tmp_path.iterdir() if p.name != "f.txt"]) == 1
    _cleanup_temp_files()
    assert [p.name for p in tmp_path.iterdir()] == ["f.txt"]
    assert target.read_bytes() == ORIGINAL
