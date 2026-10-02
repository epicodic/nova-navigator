"""Tests for `FileIdentity`, `ChangeKind` and `check_path` (REQ-14)."""

from __future__ import annotations

import os
from pathlib import Path

import pytest

from nova_editor.core import ChangeKind, FileIdentity, check_path


def identity_of(path: Path) -> FileIdentity:
    return FileIdentity.from_stat(os.stat(path))


def test_unchanged(tmp_path: Path) -> None:
    path = tmp_path / "f"
    path.write_bytes(b"hello")
    assert check_path(path, identity_of(path)) is ChangeKind.UNCHANGED


def test_modified_by_mtime(tmp_path: Path) -> None:
    path = tmp_path / "f"
    path.write_bytes(b"hello")
    held = identity_of(path)
    os.utime(path, ns=(held.mtime_ns, held.mtime_ns + 5_000_000_000))
    assert check_path(path, held) is ChangeKind.MODIFIED


def test_modified_by_growth(tmp_path: Path) -> None:
    path = tmp_path / "f"
    path.write_bytes(b"hello")
    held = identity_of(path)
    path.write_bytes(b"hello world")
    assert check_path(path, held) is ChangeKind.MODIFIED


def test_truncated(tmp_path: Path) -> None:
    path = tmp_path / "f"
    path.write_bytes(b"hello")
    held = identity_of(path)
    os.truncate(path, 2)
    assert check_path(path, held) is ChangeKind.TRUNCATED


def test_replaced(tmp_path: Path) -> None:
    path = tmp_path / "f"
    other = tmp_path / "g"
    path.write_bytes(b"hello")
    other.write_bytes(b"hello")
    held = identity_of(path)
    os.replace(other, path)
    assert check_path(path, held) is ChangeKind.REPLACED


def test_deleted(tmp_path: Path) -> None:
    path = tmp_path / "f"
    path.write_bytes(b"hello")
    held = identity_of(path)
    path.unlink()
    assert check_path(path, held) is ChangeKind.DELETED


def test_expected_new_exists_is_created(tmp_path: Path) -> None:
    path = tmp_path / "f"
    path.write_bytes(b"x")
    assert check_path(path, None) is ChangeKind.CREATED


def test_expected_new_missing_is_unchanged(tmp_path: Path) -> None:
    assert check_path(tmp_path / "missing", None) is ChangeKind.UNCHANGED


def test_path_below_a_file_is_unreadable(tmp_path: Path) -> None:
    path = tmp_path / "f"
    path.write_bytes(b"hello")
    held = identity_of(path)
    assert check_path(path / "x", held) is ChangeKind.UNREADABLE  # NotADirectoryError
    assert check_path(path / "x", None) is ChangeKind.UNREADABLE


@pytest.mark.skipif(os.geteuid() == 0, reason="root ignores permissions")
def test_permission_error_is_unreadable(tmp_path: Path) -> None:
    locked = tmp_path / "locked"
    locked.mkdir()
    path = locked / "f"
    path.write_bytes(b"hello")
    held = identity_of(path)
    locked.chmod(0)
    try:
        assert check_path(path, held) is ChangeKind.UNREADABLE
    finally:
        locked.chmod(0o700)


def test_symlink_is_resolved(tmp_path: Path) -> None:
    path = tmp_path / "f"
    link = tmp_path / "link"
    path.write_bytes(b"hello")
    link.symlink_to(path)
    assert check_path(link, identity_of(path)) is ChangeKind.UNCHANGED
