"""Tests for pid_is_alive() and find_orphans()."""

from __future__ import annotations

from pathlib import Path

from nova_navigator.vfs.process_root import find_orphans, pid_is_alive


def test_pid_is_alive_true_for_current_process() -> None:
    import os

    assert pid_is_alive(os.getpid()) is True


def test_pid_is_alive_false_for_dead_pid() -> None:
    # PID 1 is always alive on a real system, but an implausibly large PID is not.
    assert pid_is_alive(2**30) is False


def test_find_orphans_removes_empty_dead_dirs_and_reports_nonempty_ones(tmp_path: Path) -> None:
    (tmp_path / "111" / "empty").mkdir(parents=True)
    (tmp_path / "222" / "ssh" / "h").mkdir(parents=True)
    (tmp_path / "222" / "ssh" / "h" / "f.txt").write_text("x")
    (tmp_path / "333" / "x").mkdir(parents=True)

    def is_alive(pid: int) -> bool:
        return pid == 333

    orphans = find_orphans(tmp_path, is_alive)

    assert orphans == [tmp_path / "222"]
    assert not (tmp_path / "111").exists()
    assert (tmp_path / "222" / "ssh" / "h" / "f.txt").exists()
    assert (tmp_path / "333").exists()


def test_find_orphans_ignores_non_numeric_names(tmp_path: Path) -> None:
    (tmp_path / "not-a-pid").mkdir()
    (tmp_path / "not-a-pid" / "f.txt").write_text("x")

    orphans = find_orphans(tmp_path, lambda _pid: False)

    assert orphans == []
    assert (tmp_path / "not-a-pid").exists()


def test_find_orphans_ignores_files_at_root(tmp_path: Path) -> None:
    (tmp_path / "999").write_text("not a directory")

    orphans = find_orphans(tmp_path, lambda _pid: False)

    assert orphans == []
    assert (tmp_path / "999").exists()


def test_find_orphans_missing_root_returns_empty(tmp_path: Path) -> None:
    assert find_orphans(tmp_path / "does-not-exist", lambda _pid: False) == []


def test_find_orphans_survives_symlinks_and_never_deletes_their_targets(tmp_path: Path) -> None:
    root = tmp_path / "root"
    looped = root / "111" / "sub"
    looped.mkdir(parents=True)
    (looped / "loop").symlink_to(looped, target_is_directory=True)
    outside = tmp_path / "outside"
    outside.mkdir()
    (outside / "keep.txt").write_text("x")
    (root / "222").mkdir()
    (root / "222" / "link").symlink_to(outside, target_is_directory=True)

    find_orphans(root, lambda _pid: False)

    assert (outside / "keep.txt").read_text() == "x"
