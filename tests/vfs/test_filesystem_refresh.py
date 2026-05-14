"""Tests for Filesystem.refresh() default no-op."""

from __future__ import annotations

import os

from nova_navigator.vfs.filesystems import LocalFilesystem


def test_refresh_none_is_noop_on_local_filesystem() -> None:
    fs = LocalFilesystem()
    fs.refresh()  # must not raise


def test_refresh_path_is_noop_on_local_filesystem() -> None:
    fs = LocalFilesystem()
    fs.refresh(fs.path(os.path.expanduser("~")))  # must not raise
