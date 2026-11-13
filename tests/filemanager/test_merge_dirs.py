"""Copy merges a source folder into an existing real folder and treats a file or link at its name by the overwrite policy (REQ-6, REQ-7)."""

import asyncio
import os
import shutil
import stat as stat_module
from pathlib import Path

import pytest

from nova_navigator.filemanager.tasks import FileCopyOptions, OverwritePolicy, _copy_dir, copy_files, move_files
from nova_navigator.scheduler import ResponseRequest, TaskContext, TaskStatus
from nova_navigator.vfs.filesystem import Filesystem
from nova_navigator.vfs.filesystems.local import LocalFilesystem
from nova_navigator.vfs.vpath import VPath
from nova_widgets import Response

from .common import make_status, run_task


def _tree(tmp_path: Path, *, collide: bool = True) -> tuple[Path, Path]:
    """Build the source and destination trees; return (src, dst) with the folder under test at src/d and dst/d.

    src/d holds a.txt, b.txt, sub/c.txt and sub/deep/e.txt with new content.
    dst/d holds keep.txt and sub/keep2.txt. With *collide*, dst/d/a.txt and dst/d/sub/c.txt exist with old content.
    """
    src = tmp_path / "src"
    (src / "d" / "sub" / "deep").mkdir(parents=True)
    (src / "d" / "a.txt").write_bytes(b"new-a.txt")
    (src / "d" / "b.txt").write_bytes(b"new-b.txt")
    (src / "d" / "sub" / "c.txt").write_bytes(b"new-c.txt")
    (src / "d" / "sub" / "deep" / "e.txt").write_bytes(b"new-e.txt")
    dst = tmp_path / "dst"
    (dst / "d" / "sub").mkdir(parents=True)
    (dst / "d" / "keep.txt").write_bytes(b"keep")
    (dst / "d" / "sub" / "keep2.txt").write_bytes(b"keep")
    if collide:
        (dst / "d" / "a.txt").write_bytes(b"old-a.txt")
        (dst / "d" / "sub" / "c.txt").write_bytes(b"old-c.txt")
    return src, dst


def _outside(tmp_path: Path) -> Path:
    outside = tmp_path / "outside"
    outside.mkdir()
    (outside / "keep.txt").write_bytes(b"keep")
    return outside


def _assert_outside_unchanged(outside: Path) -> None:
    assert sorted(p.name for p in outside.iterdir()) == ["keep.txt"]
    assert (outside / "keep.txt").read_bytes() == b"keep"


def _reasons(status: TaskStatus) -> list[str]:
    return [skipped.reason for skipped in status.skipped]


async def _copy(
    src_folder: Path,
    destination: Path,
    fs: Filesystem,
    policy: OverwritePolicy,
    responses: list[Response] | None = None,
) -> tuple[TaskStatus, list[ResponseRequest]]:
    status = make_status()
    requests = await asyncio.wait_for(
        run_task(
            lambda ctx: copy_files(ctx, [LocalFilesystem().path(src_folder)], fs.path(destination), FileCopyOptions(overwrite=policy)),
            responses,
            status,
        ),
        timeout=20,
    )
    return status, requests


# REQ-6: existing folders are merged without a question about the folder itself.


@pytest.mark.asyncio
@pytest.mark.parametrize("policy", ["skip", "overwrite"])
async def test_copy_merge_existing_folder_applies_policy_to_files_only(link_fs: Filesystem, tmp_path: Path, policy: OverwritePolicy) -> None:
    src, dst = _tree(tmp_path)
    status, requests = await _copy(src / "d", dst, link_fs, policy)
    d = dst / "d"
    assert requests == []
    assert (d / "b.txt").read_bytes() == b"new-b.txt"
    assert (d / "sub" / "deep" / "e.txt").read_bytes() == b"new-e.txt"
    assert (d / "keep.txt").read_bytes() == b"keep"
    assert (d / "sub" / "keep2.txt").read_bytes() == b"keep"
    replaced = policy == "overwrite"
    assert (d / "a.txt").read_bytes() == (b"new-a.txt" if replaced else b"old-a.txt")
    assert (d / "sub" / "c.txt").read_bytes() == (b"new-c.txt" if replaced else b"old-c.txt")
    assert status.progress.completed == status.progress.total


@pytest.mark.asyncio
@pytest.mark.parametrize(
    ("answers", "replaced"),
    [([Response.NO, Response.NO], 0), ([Response.YES, Response.YES], 2), ([Response.YES, Response.NO], 1)],
    ids=["no-no", "yes-yes", "yes-no"],
)
async def test_copy_merge_ask_answers_per_file(link_fs: Filesystem, tmp_path: Path, answers: list[Response], replaced: int) -> None:
    src, dst = _tree(tmp_path)
    status, requests = await _copy(src / "d", dst, link_fs, "ask", answers)
    d = dst / "d"
    assert [r.title for r in requests] == ["Overwrite", "Overwrite"]
    assert sorted(r.message for r in requests) == [
        "File 'a.txt' already exists. Overwrite?",
        "File 'c.txt' already exists. Overwrite?",
    ]
    new_count = sum(
        [(d / "a.txt").read_bytes() == b"new-a.txt", (d / "sub" / "c.txt").read_bytes() == b"new-c.txt"],
    )
    assert new_count == replaced
    assert (d / "b.txt").read_bytes() == b"new-b.txt"
    assert status.progress.completed == status.progress.total


@pytest.mark.asyncio
async def test_copy_merge_asks_nothing_when_only_folders_collide(link_fs: Filesystem, tmp_path: Path) -> None:
    src, dst = _tree(tmp_path, collide=False)
    status, requests = await _copy(src / "d", dst, link_fs, "ask")
    assert requests == []
    assert (dst / "d" / "a.txt").read_bytes() == b"new-a.txt"
    assert (dst / "d" / "sub" / "deep" / "e.txt").read_bytes() == b"new-e.txt"
    assert (dst / "d" / "keep.txt").read_bytes() == b"keep"
    assert status.progress.completed == status.progress.total


@pytest.mark.asyncio
async def test_copy_merge_subfolders_merge_at_depth(link_fs: Filesystem, tmp_path: Path) -> None:
    src = tmp_path / "src"
    dst = tmp_path / "dst"
    levels = ["d", "d/l1", "d/l1/l2", "d/l1/l2/l3"]
    for depth, level in enumerate(levels):
        (src / level).mkdir(parents=True, exist_ok=True)
        (src / level / f"new-{depth}.txt").write_bytes(b"new")
        (dst / level).mkdir(parents=True, exist_ok=True)
        (dst / level / f"keep-{depth}.txt").write_bytes(b"keep")
    status, requests = await _copy(src / "d", dst, link_fs, "overwrite")
    assert requests == []
    for depth, level in enumerate(levels):
        assert (dst / level / f"new-{depth}.txt").read_bytes() == b"new"
        assert (dst / level / f"keep-{depth}.txt").read_bytes() == b"keep"
    assert status.progress.completed == status.progress.total


@pytest.mark.asyncio
async def test_copy_merge_existing_empty_folder(link_fs: Filesystem, tmp_path: Path) -> None:
    src = tmp_path / "src"
    (src / "d" / "sub").mkdir(parents=True)
    (src / "d" / "g.txt").write_bytes(b"g")
    (src / "d" / "sub" / "f.txt").write_bytes(b"f")
    dst = tmp_path / "dst"
    (dst / "d").mkdir(parents=True)
    status, requests = await _copy(src / "d", dst, link_fs, "skip")
    assert requests == []
    assert (dst / "d" / "g.txt").read_bytes() == b"g"
    assert (dst / "d" / "sub" / "f.txt").read_bytes() == b"f"
    assert status.progress.completed == status.progress.total


@pytest.mark.asyncio
async def test_copy_merge_does_not_touch_attributes_of_existing_folder(tmp_path: Path) -> None:
    src, dst = _tree(tmp_path)
    os.chmod(src / "d", 0o700)
    existing_mode = stat_module.S_IRWXU | stat_module.S_IRGRP | stat_module.S_IXGRP  # 0o750
    os.chmod(dst / "d", existing_mode)
    status, _ = await _copy(src / "d", dst, LocalFilesystem(), "overwrite")
    assert stat_module.S_IMODE((dst / "d").stat().st_mode) == existing_mode
    assert status.progress.completed == status.progress.total


@pytest.mark.asyncio
async def test_copy_created_folder_gets_source_mode(tmp_path: Path) -> None:
    src = tmp_path / "src"
    (src / "d" / "sub").mkdir(parents=True)
    (src / "d" / "sub" / "f.txt").write_bytes(b"f")
    os.chmod(src / "d", 0o700)
    sub_mode = stat_module.S_IRWXU | stat_module.S_IXGRP | stat_module.S_IXOTH  # 0o711
    os.chmod(src / "d" / "sub", sub_mode)
    dst = tmp_path / "dst"
    dst.mkdir()
    status, _ = await _copy(src / "d", dst, LocalFilesystem(), "skip")
    assert stat_module.S_IMODE((dst / "d").stat().st_mode) == 0o700
    assert stat_module.S_IMODE((dst / "d" / "sub").stat().st_mode) == sub_mode
    assert status.progress.completed == status.progress.total


# REQ-7: a file or a link (to anything) at the folder's name goes through the overwrite policy.


@pytest.mark.asyncio
@pytest.mark.parametrize(
    ("policy", "answers", "asks", "replaced"),
    [("skip", [], 0, False), ("ask", [Response.NO], 1, False), ("ask", [Response.YES], 1, True), ("overwrite", [], 0, True)],
    ids=["skip", "ask-no", "ask-yes", "overwrite"],
)
async def test_copy_folder_over_file_policy(
    link_fs: Filesystem,
    tmp_path: Path,
    policy: OverwritePolicy,
    answers: list[Response],
    asks: int,
    replaced: bool,
) -> None:
    src, dst = _tree(tmp_path)
    shutil.rmtree(dst / "d")
    (dst / "d").write_bytes(b"old-d")
    status, requests = await _copy(src / "d", dst, link_fs, policy, answers)
    assert len(requests) == asks
    assert _reasons(status) == []
    if replaced:
        assert (dst / "d").is_dir()
        assert not (dst / "d").is_symlink()
        assert (dst / "d" / "a.txt").read_bytes() == b"new-a.txt"
        assert (dst / "d" / "sub" / "deep" / "e.txt").read_bytes() == b"new-e.txt"
    else:
        assert (dst / "d").read_bytes() == b"old-d"
    assert status.progress.completed == status.progress.total


@pytest.mark.asyncio
@pytest.mark.parametrize(
    ("policy", "answers", "asks", "replaced"),
    [("skip", [], 0, False), ("ask", [Response.NO], 1, False), ("ask", [Response.YES], 1, True), ("overwrite", [], 0, True)],
    ids=["skip", "ask-no", "ask-yes", "overwrite"],
)
async def test_copy_folder_over_link_to_folder_never_writes_through(
    link_fs: Filesystem,
    tmp_path: Path,
    policy: OverwritePolicy,
    answers: list[Response],
    asks: int,
    replaced: bool,
) -> None:
    src, dst = _tree(tmp_path)
    outside = _outside(tmp_path)
    shutil.rmtree(dst / "d")
    (dst / "d").symlink_to(outside, target_is_directory=True)
    status, requests = await _copy(src / "d", dst, link_fs, policy, answers)
    assert len(requests) == asks
    _assert_outside_unchanged(outside)
    if replaced:
        assert (dst / "d").is_dir()
        assert not (dst / "d").is_symlink()
        assert (dst / "d" / "a.txt").read_bytes() == b"new-a.txt"
    else:
        assert (dst / "d").is_symlink()
    assert status.progress.completed == status.progress.total


@pytest.mark.asyncio
async def test_copy_folder_over_broken_link_overwrite_replaces_link(link_fs: Filesystem, tmp_path: Path) -> None:
    src, dst = _tree(tmp_path)
    shutil.rmtree(dst / "d")
    (dst / "d").symlink_to(tmp_path / "missing", target_is_directory=True)
    status, requests = await _copy(src / "d", dst, link_fs, "overwrite")
    assert requests == []
    assert (dst / "d").is_dir()
    assert not (dst / "d").is_symlink()
    assert (dst / "d" / "a.txt").read_bytes() == b"new-a.txt"
    assert not (tmp_path / "missing").exists()
    assert status.progress.completed == status.progress.total


@pytest.mark.asyncio
@pytest.mark.parametrize("policy", ["skip", "overwrite"])
async def test_copy_nested_folder_over_link_to_folder_at_depth(link_fs: Filesystem, tmp_path: Path, policy: OverwritePolicy) -> None:
    src, dst = _tree(tmp_path)
    outside = _outside(tmp_path)
    shutil.rmtree(dst / "d" / "sub")
    (dst / "d" / "sub").symlink_to(outside, target_is_directory=True)
    status, requests = await _copy(src / "d", dst, link_fs, policy)
    assert requests == []
    _assert_outside_unchanged(outside)
    assert (dst / "d" / "b.txt").read_bytes() == b"new-b.txt"
    assert (dst / "d" / "keep.txt").read_bytes() == b"keep"
    if policy == "skip":
        assert (dst / "d" / "sub").is_symlink()
    else:
        assert not (dst / "d" / "sub").is_symlink()
        assert (dst / "d" / "sub" / "c.txt").read_bytes() == b"new-c.txt"
        assert (dst / "d" / "sub" / "deep" / "e.txt").read_bytes() == b"new-e.txt"
    assert status.progress.completed == status.progress.total


@pytest.mark.asyncio
async def test_copy_nested_folder_over_file_at_depth_ask_once_per_entry(link_fs: Filesystem, tmp_path: Path) -> None:
    src, dst = _tree(tmp_path, collide=False)
    (dst / "d" / "sub" / "c.txt").write_bytes(b"old-c.txt")
    (dst / "d" / "sub" / "deep").write_bytes(b"old-deep")
    status, requests = await _copy(src / "d", dst, link_fs, "ask", [Response.YES, Response.YES])
    assert len(requests) == 2
    assert (dst / "d" / "sub" / "c.txt").read_bytes() == b"new-c.txt"
    assert (dst / "d" / "sub" / "deep" / "e.txt").read_bytes() == b"new-e.txt"
    assert status.progress.completed == status.progress.total


@pytest.mark.asyncio
@pytest.mark.parametrize("where", ["root", "nested"])
async def test_copy_skipped_folder_counts_as_completed(link_fs: Filesystem, tmp_path: Path, where: str) -> None:
    src, dst = _tree(tmp_path)
    target = dst / "d" if where == "root" else dst / "d" / "sub"
    shutil.rmtree(target)
    target.write_bytes(b"old")
    status, requests = await _copy(src / "d", dst, link_fs, "skip")
    assert requests == []
    assert _reasons(status) == []
    assert status.progress.completed == status.progress.total


@pytest.mark.asyncio
async def test_copy_single_folder_to_renamed_destination_over_file(link_fs: Filesystem, tmp_path: Path) -> None:
    src, dst = _tree(tmp_path)
    (dst / "newname").write_bytes(b"old")
    status, requests = await _copy(src / "d", dst / "newname", link_fs, "overwrite")
    assert requests == []
    assert (dst / "newname" / "a.txt").read_bytes() == b"new-a.txt"
    assert (dst / "newname" / "sub" / "deep" / "e.txt").read_bytes() == b"new-e.txt"
    assert status.progress.completed == status.progress.total


@pytest.mark.asyncio
@pytest.mark.parametrize(("root_kind", "expected"), [("file", False), ("folder", True)], ids=["skipped-root", "merged-root"])
async def test_copy_dir_returns_false_only_when_root_skipped(tmp_path: Path, root_kind: str, expected: bool) -> None:
    src, dst = _tree(tmp_path)
    if root_kind == "file":
        shutil.rmtree(dst / "d")
        (dst / "d").write_bytes(b"old")
    fs = LocalFilesystem()
    results: list[bool] = []

    async def task(ctx: TaskContext) -> None:
        results.append(await _copy_dir(ctx, fs.path(src / "d"), fs.path(dst / "d"), fs, FileCopyOptions(overwrite="skip")))

    await run_task(task, status=make_status())
    assert results == [expected]


# Commit 3: moves merge into existing folders through the same ensure-directory step.


def _force_cross_device(monkeypatch: pytest.MonkeyPatch) -> None:
    def is_same_device(_self: LocalFilesystem, _a: VPath, _b: VPath) -> bool:
        return False

    monkeypatch.setattr(LocalFilesystem, "is_same_device", is_same_device)


async def _move(
    source: Path,
    destination: Path,
    fs: Filesystem,
    policy: OverwritePolicy,
    responses: list[Response] | None = None,
) -> tuple[TaskStatus, list[ResponseRequest]]:
    status = make_status()
    requests = await asyncio.wait_for(
        run_task(
            lambda ctx: move_files(
                ctx,
                [LocalFilesystem().path(source)],
                fs.path(destination),
                FileCopyOptions(overwrite=policy),
            ),
            responses,
            status,
        ),
        timeout=20,
    )
    return status, requests


def _make_destination_entry(dst_d: Path, kind: str, outside: Path) -> None:
    """Replace the folder at *dst_d* with a file, a link to *outside*, or a broken link."""
    shutil.rmtree(dst_d)
    if kind == "file":
        dst_d.write_bytes(b"old-d")
    elif kind == "link-to-folder":
        dst_d.symlink_to(outside, target_is_directory=True)
    else:
        dst_d.symlink_to(outside.parent / "missing", target_is_directory=True)


def _assert_entry_kept(entry: Path, kind: str) -> None:
    if kind == "file":
        assert entry.read_bytes() == b"old-d"
    else:
        assert entry.is_symlink()


# REQ-6: existing folders are merged by a move; only moved files leave the source.


@pytest.mark.asyncio
@pytest.mark.parametrize("policy", ["skip", "overwrite"])
async def test_move_cross_device_merge_applies_policy_to_files(
    link_fs: Filesystem,
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    policy: OverwritePolicy,
) -> None:
    _force_cross_device(monkeypatch)
    src, dst = _tree(tmp_path)
    status, requests = await _move(src / "d", dst, link_fs, policy)
    d = dst / "d"
    assert requests == []
    assert (d / "b.txt").read_bytes() == b"new-b.txt"
    assert (d / "sub" / "deep" / "e.txt").read_bytes() == b"new-e.txt"
    assert (d / "keep.txt").read_bytes() == b"keep"
    assert (d / "sub" / "keep2.txt").read_bytes() == b"keep"
    assert not (src / "d" / "b.txt").exists()
    assert not (src / "d" / "sub" / "deep").exists()
    if policy == "overwrite":
        assert (d / "a.txt").read_bytes() == b"new-a.txt"
        assert (d / "sub" / "c.txt").read_bytes() == b"new-c.txt"
        assert not (src / "d").exists()
    else:
        assert (d / "a.txt").read_bytes() == b"old-a.txt"
        assert (d / "sub" / "c.txt").read_bytes() == b"old-c.txt"
        assert (src / "d" / "a.txt").read_bytes() == b"new-a.txt"
        assert (src / "d" / "sub" / "c.txt").read_bytes() == b"new-c.txt"
    assert _reasons(status) == []
    assert status.progress.completed == status.progress.total


@pytest.mark.asyncio
@pytest.mark.parametrize(
    ("answers", "replaced"),
    [([Response.NO, Response.YES], 1), ([Response.YES, Response.YES], 2)],
    ids=["no-yes", "yes-yes"],
)
async def test_move_cross_device_merge_ask_asks_only_for_files(
    link_fs: Filesystem,
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    answers: list[Response],
    replaced: int,
) -> None:
    _force_cross_device(monkeypatch)
    src, dst = _tree(tmp_path)
    status, requests = await _move(src / "d", dst, link_fs, "ask", answers)
    d = dst / "d"
    assert [r.title for r in requests] == ["Overwrite", "Overwrite"]
    assert sorted(r.message for r in requests) == [
        "File 'a.txt' already exists. Overwrite?",
        "File 'c.txt' already exists. Overwrite?",
    ]
    new_count = sum(
        [(d / "a.txt").read_bytes() == b"new-a.txt", (d / "sub" / "c.txt").read_bytes() == b"new-c.txt"],
    )
    assert new_count == replaced
    assert (d / "b.txt").read_bytes() == b"new-b.txt"
    assert status.progress.completed == status.progress.total


@pytest.mark.asyncio
async def test_move_cross_device_merge_at_depth_removes_source_only_after_contents_moved(
    link_fs: Filesystem,
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    _force_cross_device(monkeypatch)
    src = tmp_path / "src"
    (src / "d" / "l1" / "l2" / "l3").mkdir(parents=True)
    (src / "d" / "l1" / "s.txt").write_bytes(b"new-s")
    (src / "d" / "l1" / "l2" / "l3" / "e.txt").write_bytes(b"new-e")
    dst = tmp_path / "dst"
    (dst / "d" / "l1" / "l2" / "l3").mkdir(parents=True)
    (dst / "d" / "l1" / "l2" / "l3" / "e.txt").write_bytes(b"old-e")
    status, requests = await _move(src / "d", dst, link_fs, "skip")
    assert requests == []
    assert (dst / "d" / "l1" / "s.txt").read_bytes() == b"new-s"
    assert not (src / "d" / "l1" / "s.txt").exists()
    assert (dst / "d" / "l1" / "l2" / "l3" / "e.txt").read_bytes() == b"old-e"
    # Only the folders above the skipped file remain in the source.
    assert (src / "d" / "l1" / "l2" / "l3" / "e.txt").read_bytes() == b"new-e"
    assert sorted(p.name for p in (src / "d").iterdir()) == ["l1"]
    assert sorted(p.name for p in (src / "d" / "l1").iterdir()) == ["l2"]
    assert status.progress.completed == status.progress.total


@pytest.mark.asyncio
async def test_move_cross_device_merge_at_depth_overwrite_removes_whole_source(
    link_fs: Filesystem,
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    _force_cross_device(monkeypatch)
    src = tmp_path / "src"
    (src / "d" / "l1" / "l2" / "l3").mkdir(parents=True)
    (src / "d" / "l1" / "s.txt").write_bytes(b"new-s")
    (src / "d" / "l1" / "l2" / "l3" / "e.txt").write_bytes(b"new-e")
    dst = tmp_path / "dst"
    (dst / "d" / "l1" / "l2" / "l3").mkdir(parents=True)
    (dst / "d" / "l1" / "l2" / "l3" / "e.txt").write_bytes(b"old-e")
    status, requests = await _move(src / "d", dst, link_fs, "overwrite")
    assert requests == []
    assert (dst / "d" / "l1" / "l2" / "l3" / "e.txt").read_bytes() == b"new-e"
    assert not (src / "d").exists()
    assert status.progress.completed == status.progress.total


@pytest.mark.asyncio
async def test_move_cross_device_merge_into_existing_folder_no_error_on_ssh(
    link_fs: Filesystem,
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    _force_cross_device(monkeypatch)
    src = tmp_path / "src"
    (src / "d" / "sub").mkdir(parents=True)
    (src / "d" / "g.txt").write_bytes(b"g")
    (src / "d" / "sub" / "f.txt").write_bytes(b"f")
    dst = tmp_path / "dst"
    (dst / "d").mkdir(parents=True)
    status, requests = await _move(src / "d", dst, link_fs, "skip")
    assert requests == []
    assert (dst / "d" / "g.txt").read_bytes() == b"g"
    assert (dst / "d" / "sub" / "f.txt").read_bytes() == b"f"
    assert not (src / "d").exists()
    assert status.progress.completed == status.progress.total


@pytest.mark.asyncio
@pytest.mark.parametrize("policy", ["skip", "overwrite"])
async def test_move_same_device_merge_applies_policy_and_removes_source(tmp_path: Path, policy: OverwritePolicy) -> None:
    src, dst = _tree(tmp_path)
    status, requests = await _move(src / "d", dst, LocalFilesystem(), policy)
    d = dst / "d"
    assert requests == []
    assert (d / "b.txt").read_bytes() == b"new-b.txt"
    assert (d / "sub" / "deep" / "e.txt").read_bytes() == b"new-e.txt"
    assert (d / "keep.txt").read_bytes() == b"keep"
    if policy == "overwrite":
        assert (d / "a.txt").read_bytes() == b"new-a.txt"
        assert not (src / "d").exists()
    else:
        assert (d / "a.txt").read_bytes() == b"old-a.txt"
        assert (src / "d" / "a.txt").read_bytes() == b"new-a.txt"
        assert (src / "d" / "sub" / "c.txt").read_bytes() == b"new-c.txt"
    assert _reasons(status) == []
    assert status.progress.completed == status.progress.total


@pytest.mark.asyncio
async def test_move_same_device_merge_ask_asks_only_for_files(tmp_path: Path) -> None:
    src, dst = _tree(tmp_path)
    status, requests = await _move(src / "d", dst, LocalFilesystem(), "ask", [Response.NO, Response.YES])
    assert [r.title for r in requests] == ["Overwrite", "Overwrite"]
    assert all(r.message.endswith("already exists. Overwrite?") for r in requests)
    assert (dst / "d" / "b.txt").read_bytes() == b"new-b.txt"
    kept_old = [(dst / "d" / "a.txt").read_bytes() == b"old-a.txt", (dst / "d" / "sub" / "c.txt").read_bytes() == b"old-c.txt"]
    assert kept_old.count(True) == 1
    assert status.progress.completed == status.progress.total


@pytest.mark.asyncio
async def test_move_same_device_merge_at_depth(tmp_path: Path) -> None:
    src = tmp_path / "src"
    dst = tmp_path / "dst"
    levels = ["d", "d/l1", "d/l1/l2", "d/l1/l2/l3"]
    for depth, level in enumerate(levels):
        (src / level).mkdir(parents=True, exist_ok=True)
        (src / level / f"new-{depth}.txt").write_bytes(b"new")
        (dst / level).mkdir(parents=True, exist_ok=True)
        (dst / level / f"keep-{depth}.txt").write_bytes(b"keep")
    status, requests = await _move(src / "d", dst, LocalFilesystem(), "overwrite")
    assert requests == []
    for depth, level in enumerate(levels):
        assert (dst / level / f"new-{depth}.txt").read_bytes() == b"new"
        assert (dst / level / f"keep-{depth}.txt").read_bytes() == b"keep"
    assert not (src / "d").exists()
    assert status.progress.completed == status.progress.total


@pytest.mark.asyncio
@pytest.mark.parametrize("cross_device", [True, False], ids=["cross-device", "same-device"])
async def test_move_merge_does_not_touch_attributes_of_existing_folder(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    cross_device: bool,
) -> None:
    if cross_device:
        _force_cross_device(monkeypatch)
    src, dst = _tree(tmp_path)
    os.chmod(src / "d", 0o700)
    existing_mode = stat_module.S_IRWXU | stat_module.S_IRGRP | stat_module.S_IXGRP  # 0o750
    os.chmod(dst / "d", existing_mode)
    status, _ = await _move(src / "d", dst, LocalFilesystem(), "overwrite")
    assert stat_module.S_IMODE((dst / "d").stat().st_mode) == existing_mode
    assert status.progress.completed == status.progress.total


# REQ-7: a file or a link at the folder's name goes through the overwrite policy; a skip keeps the source folder.


@pytest.mark.asyncio
@pytest.mark.parametrize("kind", ["file", "link-to-folder", "broken-link"])
@pytest.mark.parametrize(
    ("policy", "answers", "asks", "replaced"),
    [("skip", [], 0, False), ("ask", [Response.NO], 1, False), ("ask", [Response.YES], 1, True), ("overwrite", [], 0, True)],
    ids=["skip", "ask-no", "ask-yes", "overwrite"],
)
async def test_move_cross_device_folder_over_file_or_link_policy(
    link_fs: Filesystem,
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    kind: str,
    policy: OverwritePolicy,
    answers: list[Response],
    asks: int,
    replaced: bool,
) -> None:
    _force_cross_device(monkeypatch)
    src, dst = _tree(tmp_path)
    outside = _outside(tmp_path)
    _make_destination_entry(dst / "d", kind, outside)
    status, requests = await _move(src / "d", dst, link_fs, policy, answers)
    assert len(requests) == asks
    _assert_outside_unchanged(outside)
    assert _reasons(status) == []
    assert status.progress.completed == status.progress.total
    if replaced:
        assert (dst / "d").is_dir()
        assert not (dst / "d").is_symlink()
        assert (dst / "d" / "a.txt").read_bytes() == b"new-a.txt"
        assert (dst / "d" / "sub" / "deep" / "e.txt").read_bytes() == b"new-e.txt"
        assert not (src / "d").exists()
    else:
        _assert_entry_kept(dst / "d", kind)
        assert (src / "d" / "a.txt").read_bytes() == b"new-a.txt"
        assert (src / "d" / "sub" / "deep" / "e.txt").read_bytes() == b"new-e.txt"


@pytest.mark.asyncio
@pytest.mark.parametrize("kind", ["file", "link-to-folder", "broken-link"])
@pytest.mark.parametrize(
    ("policy", "answers", "asks", "replaced"),
    [("skip", [], 0, False), ("ask", [Response.NO], 1, False), ("ask", [Response.YES], 1, True), ("overwrite", [], 0, True)],
    ids=["skip", "ask-no", "ask-yes", "overwrite"],
)
async def test_move_same_device_folder_over_file_or_link_policy(
    tmp_path: Path,
    kind: str,
    policy: OverwritePolicy,
    answers: list[Response],
    asks: int,
    replaced: bool,
) -> None:
    src, dst = _tree(tmp_path)
    outside = _outside(tmp_path)
    _make_destination_entry(dst / "d", kind, outside)
    status, requests = await _move(src / "d", dst, LocalFilesystem(), policy, answers)
    assert len(requests) == asks
    _assert_outside_unchanged(outside)
    assert _reasons(status) == []
    assert status.progress.completed == status.progress.total
    if replaced:
        assert (dst / "d").is_dir()
        assert not (dst / "d").is_symlink()
        assert (dst / "d" / "a.txt").read_bytes() == b"new-a.txt"
        assert not (src / "d").exists()
    else:
        _assert_entry_kept(dst / "d", kind)
        assert (src / "d" / "a.txt").read_bytes() == b"new-a.txt"


async def _nested_link_move(tmp_path: Path, fs: Filesystem, policy: OverwritePolicy) -> None:
    """Move src/d into dst/d where dst/d/sub is a link to an outside folder; check the skip and overwrite outcomes."""
    src, dst = _tree(tmp_path)
    outside = _outside(tmp_path)
    shutil.rmtree(dst / "d" / "sub")
    (dst / "d" / "sub").symlink_to(outside, target_is_directory=True)
    status, requests = await _move(src / "d", dst, fs, policy)
    assert requests == []
    assert _reasons(status) == []
    _assert_outside_unchanged(outside)
    assert (dst / "d" / "b.txt").read_bytes() == b"new-b.txt"
    assert (dst / "d" / "keep.txt").read_bytes() == b"keep"
    assert status.progress.completed == status.progress.total
    if policy == "skip":
        assert (dst / "d" / "sub").is_symlink()
        assert (src / "d" / "sub" / "c.txt").read_bytes() == b"new-c.txt"
        assert (src / "d" / "sub" / "deep" / "e.txt").read_bytes() == b"new-e.txt"
        assert not (src / "d" / "b.txt").exists()
    else:
        assert not (dst / "d" / "sub").is_symlink()
        assert (dst / "d" / "sub" / "deep" / "e.txt").read_bytes() == b"new-e.txt"
        assert not (src / "d").exists()


@pytest.mark.asyncio
@pytest.mark.parametrize("policy", ["skip", "overwrite"])
async def test_move_nested_folder_over_link_at_depth_cross_device(
    link_fs: Filesystem,
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    policy: OverwritePolicy,
) -> None:
    _force_cross_device(monkeypatch)
    await _nested_link_move(tmp_path, link_fs, policy)


@pytest.mark.asyncio
@pytest.mark.parametrize("policy", ["skip", "overwrite"])
async def test_move_nested_folder_over_link_at_depth_same_device(tmp_path: Path, policy: OverwritePolicy) -> None:
    await _nested_link_move(tmp_path, LocalFilesystem(), policy)


@pytest.mark.asyncio
async def test_move_cross_device_skipped_nested_empty_source_folder_is_kept(
    link_fs: Filesystem,
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    _force_cross_device(monkeypatch)
    src = tmp_path / "src"
    (src / "d" / "empty").mkdir(parents=True)
    (src / "d" / "b.txt").write_bytes(b"new-b.txt")
    dst = tmp_path / "dst"
    (dst / "d").mkdir(parents=True)
    (dst / "d" / "empty").write_bytes(b"old")
    status, requests = await _move(src / "d", dst, link_fs, "skip")
    assert requests == []
    assert (src / "d" / "empty").is_dir()
    assert (src / "d").is_dir()
    assert (dst / "d" / "b.txt").read_bytes() == b"new-b.txt"
    assert (dst / "d" / "empty").read_bytes() == b"old"
    assert _reasons(status) == []
    assert status.progress.completed == status.progress.total


@pytest.mark.asyncio
async def test_move_cross_device_link_to_folder_onto_skipped_destination_keeps_source_link(
    link_fs: Filesystem,
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    _force_cross_device(monkeypatch)
    target = tmp_path / "target"
    target.mkdir()
    (target / "f.txt").write_bytes(b"f")
    src = tmp_path / "src"
    src.mkdir()
    (src / "s").symlink_to(target, target_is_directory=True)
    dst = tmp_path / "dst"
    dst.mkdir()
    (dst / "s").write_bytes(b"old")
    status, requests = await _move(src / "s", dst, link_fs, "skip")
    assert requests == []
    assert (src / "s").is_symlink()
    assert os.readlink(src / "s") == str(target)
    assert (dst / "s").read_bytes() == b"old"
    assert (target / "f.txt").read_bytes() == b"f"
    assert status.progress.completed == status.progress.total


@pytest.mark.asyncio
@pytest.mark.parametrize("where", ["root", "nested"])
async def test_move_skipped_folder_counts_as_completed(
    link_fs: Filesystem,
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    where: str,
) -> None:
    _force_cross_device(monkeypatch)
    src, dst = _tree(tmp_path)
    target = dst / "d" if where == "root" else dst / "d" / "sub"
    shutil.rmtree(target)
    target.write_bytes(b"old")
    status, requests = await _move(src / "d", dst, link_fs, "skip")
    assert requests == []
    assert _reasons(status) == []
    assert status.progress.completed == status.progress.total
