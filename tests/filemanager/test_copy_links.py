"""Copy recreates symlinks (identical target text) on link-capable destinations, in all four local/SSH directions."""

import asyncio
import os
from pathlib import Path

import pytest

from nova_navigator.file_filter import FilenamePatternFilter
from nova_navigator.filemanager.tasks import FileCopyOptions, OverwritePolicy, copy_files
from nova_navigator.scheduler import ResponseRequest, TaskStatus
from nova_navigator.vfs.filesystem import Filesystem
from nova_navigator.vfs.filesystems.local import LocalFilesystem
from nova_widgets import Response

from .common import make_status, run_task

_LINKS = ["file_rel", "file_abs", "dir_rel", "dir_abs", "broken", "sub/up"]


def _build(tmp_path: Path) -> tuple[Path, Path, Path]:
    """Return (src, outside, dst); src holds a file, a directory, and one link of every kind."""
    outside = tmp_path / "outside"
    outside.mkdir()
    (outside / "keep.txt").write_text("keep")
    src = tmp_path / "src"
    (src / "sub").mkdir(parents=True)
    (src / "f.txt").write_text("x")
    (src / "sub" / "g.txt").write_text("g")
    (src / "file_rel").symlink_to("f.txt")
    (src / "file_abs").symlink_to(src / "f.txt")
    (src / "dir_rel").symlink_to("sub", target_is_directory=True)
    (src / "dir_abs").symlink_to(outside, target_is_directory=True)
    (src / "broken").symlink_to("../no/such/target")
    (src / "sub" / "up").symlink_to("..", target_is_directory=True)
    dst = tmp_path / "dst"
    dst.mkdir()
    return src, outside, dst


async def _copy(
    sources: list[Path],
    destination: Path,
    fs_pair: tuple[Filesystem, Filesystem],
    options: FileCopyOptions | None = None,
    responses: list[Response] | None = None,
) -> tuple[TaskStatus, list[ResponseRequest]]:
    src_fs, dst_fs = fs_pair
    status = make_status()
    requests = await asyncio.wait_for(
        run_task(
            lambda ctx: copy_files(ctx, [src_fs.path(s) for s in sources], dst_fs.path(destination), options),
            responses,
            status,
        ),
        timeout=20,
    )
    return status, requests


@pytest.mark.asyncio
@pytest.mark.parametrize("name", _LINKS)
async def test_copy_selected_link_recreates_identical_target_text(fs_pair: tuple[Filesystem, Filesystem], tmp_path: Path, name: str) -> None:
    src, outside, dst = _build(tmp_path)
    status, _ = await _copy([src / name], dst, fs_pair)
    copied = dst / Path(name).name
    assert copied.is_symlink()
    assert os.readlink(copied) == os.readlink(src / name)
    assert sorted(p.name for p in dst.iterdir()) == [Path(name).name]
    assert sorted(p.name for p in outside.iterdir()) == ["keep.txt"]
    assert status.progress.completed == status.progress.total


@pytest.mark.asyncio
async def test_copy_dir_recreates_every_link_and_does_not_descend(fs_pair: tuple[Filesystem, Filesystem], tmp_path: Path) -> None:
    src, outside, dst = _build(tmp_path)
    status, _ = await _copy([src], dst, fs_pair)
    for name in _LINKS:
        copied = dst / "src" / name
        assert copied.is_symlink(), name
        assert os.readlink(copied) == os.readlink(src / name)
    assert sorted(p.name for p in outside.iterdir()) == ["keep.txt"]
    assert sorted(p.name for p in (dst / "src" / "sub").iterdir()) == ["g.txt", "up"]
    assert status.progress.completed == status.progress.total


@pytest.mark.asyncio
async def test_copy_tree_with_link_to_ancestor_completes(fs_pair: tuple[Filesystem, Filesystem], tmp_path: Path) -> None:
    src = tmp_path / "src"
    (src / "d").mkdir(parents=True)
    (src / "d" / "g.txt").write_text("g")
    (src / "d" / "up").symlink_to("..", target_is_directory=True)
    dst = tmp_path / "dst"
    dst.mkdir()
    status, _ = await _copy([src], dst, fs_pair)
    assert os.readlink(dst / "src" / "d" / "up") == ".."
    assert (dst / "src" / "d" / "g.txt").read_text() == "g"
    assert status.progress.completed == status.progress.total


@pytest.mark.asyncio
async def test_copy_selected_link_to_new_exact_name(tmp_path: Path) -> None:
    src, _, dst = _build(tmp_path)
    fs = LocalFilesystem()
    await _copy([src / "dir_rel"], dst / "renamed", (fs, fs))
    assert os.readlink(dst / "renamed") == "sub"


@pytest.mark.asyncio
@pytest.mark.parametrize("existing", ["file", "link"])
@pytest.mark.parametrize(
    ("policy", "responses", "replaced"),
    [("skip", [], False), ("overwrite", [], True), ("ask", [Response.YES], True), ("ask", [Response.NO], False)],
)
async def test_copy_link_over_existing_policy(tmp_path: Path, existing: str, policy: OverwritePolicy, responses: list[Response], replaced: bool) -> None:
    src, _, dst = _build(tmp_path)
    if existing == "file":
        (dst / "file_rel").write_text("old")
    else:
        (dst / "file_rel").symlink_to("old-target")
    fs = LocalFilesystem()
    _, requests = await _copy([src / "file_rel"], dst, (fs, fs), FileCopyOptions(overwrite=policy), responses)
    if replaced:
        assert os.readlink(dst / "file_rel") == "f.txt"
    elif existing == "file":
        assert (dst / "file_rel").read_text() == "old"
        assert not (dst / "file_rel").is_symlink()
    else:
        assert os.readlink(dst / "file_rel") == "old-target"
    assert len(requests) == (1 if policy == "ask" else 0)


@pytest.mark.asyncio
async def test_copy_link_unreadable_target_is_skipped_and_reported(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    src, _, dst = _build(tmp_path)

    def failing(_self: LocalFilesystem, _path: object) -> str:
        raise OSError("cannot read link")

    monkeypatch.setattr(LocalFilesystem, "readlink", failing)
    fs = LocalFilesystem()
    status, _ = await _copy([src / "file_rel"], dst, (fs, fs))
    assert list(dst.iterdir()) == []
    assert [(s.item, s.reason) for s in status.skipped] == [(fs.path(src / "file_rel").uri, "unreadable link")]
    assert status.progress.completed == status.progress.total


@pytest.mark.asyncio
async def test_copy_link_respects_filter_and_reports_nothing(tmp_path: Path) -> None:
    src, _, dst = _build(tmp_path)
    fs = LocalFilesystem()
    options = FileCopyOptions(filter=FilenamePatternFilter(["*.txt"]))
    status, _ = await _copy([src], dst, (fs, fs), options)
    assert not (dst / "src" / "file_rel").exists()
    assert not (dst / "src" / "file_rel").is_symlink()
    assert (dst / "src" / "f.txt").exists()
    assert status.skipped == ()
