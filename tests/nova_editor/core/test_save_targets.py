"""`SaveJob` special targets: permissions, non-regular files, modes, symlinks and long names."""

from __future__ import annotations

import os
import stat
from collections.abc import Iterator
from contextlib import contextmanager
from pathlib import Path

import pytest

from nova_editor.core import PreadSource
from nova_editor.core.save import SaveFailed, SaveJob, SaveSettings

from .fake_planner import whole_file_planner
from .save_helpers import ORIGINAL, make_job

NOT_ROOT = pytest.mark.skipif(os.geteuid() == 0, reason="root ignores permissions")


def save_bytes(target: Path, data: bytes, side: Path) -> None:
    """Save `data` (kept in the side file) to `target`."""
    side.write_bytes(data)
    source = PreadSource(side)
    result = SaveJob(whole_file_planner(source), target, SaveSettings(chunk=7, fsync_every=64), progress=None, foreground=None).run()
    result.source.close()
    source.close()


def save_to(target: Path, side: Path) -> SaveFailed | None:
    """Save the content of `side` to `target`; return the failure or `None`."""
    side.write_bytes(ORIGINAL)
    source = PreadSource(side)
    try:
        SaveJob(whole_file_planner(source), target, SaveSettings(chunk=7, fsync_every=64), progress=None, foreground=None).run().source.close()
    except SaveFailed as error:
        return error
    finally:
        source.close()
    return None


@contextmanager
def umask(value: int) -> Iterator[None]:
    old = os.umask(value)
    try:
        yield
    finally:
        os.umask(old)


@NOT_ROOT
def test_read_only_directory_offers_save_as(tmp_path: Path) -> None:
    folder = tmp_path / "ro"
    folder.mkdir()
    target = folder / "f.txt"
    target.write_bytes(ORIGINAL)
    job, source = make_job(target)
    folder.chmod(0o500)
    try:
        with pytest.raises(SaveFailed) as info:
            job.run()
    finally:
        folder.chmod(0o700)
    source.close()
    assert info.value.stage == "prepare"
    assert "Save As" in str(info.value)
    assert target.read_bytes() == ORIGINAL
    assert [p.name for p in folder.iterdir()] == ["f.txt"]


@NOT_ROOT
def test_read_only_file_is_refused(tmp_path: Path) -> None:
    target = tmp_path / "f.txt"
    target.write_bytes(ORIGINAL)
    target.chmod(0o444)
    job, source = make_job(target)
    with pytest.raises(SaveFailed, match="read-only file") as info:
        job.run()
    source.close()
    assert info.value.stage == "prepare"
    assert target.read_bytes() == ORIGINAL
    assert [p.name for p in tmp_path.iterdir()] == ["f.txt"]


def test_directory_target_is_refused(tmp_path: Path) -> None:
    target = tmp_path / "dir"
    target.mkdir()
    failure = save_to(target, tmp_path / "side.bin")
    assert failure is not None
    assert failure.stage == "prepare"
    assert sorted(p.name for p in tmp_path.iterdir()) == ["dir", "side.bin"]


def test_fifo_target_is_refused(tmp_path: Path) -> None:
    fifo = tmp_path / "pipe"
    os.mkfifo(fifo)
    failure = save_to(fifo, tmp_path / "side.bin")
    assert failure is not None
    assert failure.stage == "prepare"
    assert stat.S_ISFIFO(fifo.stat().st_mode)
    assert sorted(p.name for p in tmp_path.iterdir()) == ["pipe", "side.bin"]


def test_long_file_name_saves(tmp_path: Path) -> None:
    target = tmp_path / ("n" * 255)
    target.write_bytes(ORIGINAL)
    job, source = make_job(target)
    job.run().source.close()
    source.close()
    assert target.read_bytes() == ORIGINAL
    assert [p.name for p in tmp_path.iterdir()] == [target.name]


@pytest.mark.parametrize("mode", [0o600, 0o644, 0o755])
def test_existing_mode_is_kept(tmp_path: Path, mode: int) -> None:
    target = tmp_path / "f.txt"
    target.write_bytes(ORIGINAL)
    target.chmod(mode)
    job, source = make_job(target)
    job.run().source.close()
    source.close()
    assert stat.S_IMODE(target.stat().st_mode) == mode


def test_new_file_uses_the_umask(tmp_path: Path) -> None:
    target = tmp_path / "new.txt"
    with umask(0o027):
        save_bytes(target, ORIGINAL, tmp_path / "side.bin")
    assert target.read_bytes() == ORIGINAL
    assert stat.S_IMODE(target.stat().st_mode) == 0o640


def test_symlink_stays_a_link_and_the_target_changes(tmp_path: Path) -> None:
    real = tmp_path / "real.txt"
    real.write_bytes(b"old")
    link = tmp_path / "link.txt"
    link.symlink_to(real)
    save_bytes(link, ORIGINAL, tmp_path / "side.bin")
    assert link.is_symlink()
    assert real.read_bytes() == ORIGINAL


def test_dangling_symlink_creates_the_named_file(tmp_path: Path) -> None:
    real = tmp_path / "real.txt"
    link = tmp_path / "link.txt"
    link.symlink_to(real)
    save_bytes(link, ORIGINAL, tmp_path / "side.bin")
    assert link.is_symlink()
    assert real.read_bytes() == ORIGINAL


def test_chain_of_two_links(tmp_path: Path) -> None:
    real = tmp_path / "real.txt"
    real.write_bytes(b"old")
    first = tmp_path / "first.txt"
    first.symlink_to(real)
    second = tmp_path / "second.txt"
    second.symlink_to(first)
    save_bytes(second, ORIGINAL, tmp_path / "side.bin")
    assert second.is_symlink()
    assert first.is_symlink()
    assert real.read_bytes() == ORIGINAL
