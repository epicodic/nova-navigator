"""Move must not follow links when it replaces an existing destination."""

from pathlib import Path

import pytest

from nova_navigator.filemanager.tasks import FileCopyOptions, move_files
from nova_navigator.vfs.filesystems.local import LocalFilesystem

from .common import run_task


@pytest.mark.asyncio
async def test_move_file_replaces_dir_containing_outside_link(tmp_path: Path) -> None:
    outside = tmp_path / "outside"
    outside.mkdir()
    (outside / "keep.txt").write_text("keep")
    src = tmp_path / "src"
    src.mkdir()
    (src / "a").write_bytes(b"new")
    dst = tmp_path / "dst"
    (dst / "a").mkdir(parents=True)
    (dst / "a" / "out").symlink_to(outside, target_is_directory=True)
    fs = LocalFilesystem()
    await run_task(lambda ctx: move_files(ctx, [fs.path(src / "a")], fs.path(dst), FileCopyOptions(overwrite="overwrite")))
    assert (dst / "a").read_bytes() == b"new"
    assert sorted(p.name for p in outside.iterdir()) == ["keep.txt"]
    assert (outside / "keep.txt").read_text() == "keep"
