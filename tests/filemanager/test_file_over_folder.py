"""A file (or link) that meets a same-named real folder is skipped with "folder exists"; the folder is never replaced (REQ-32)."""

import asyncio
from collections.abc import Awaitable, Callable
from pathlib import Path

import pytest

from nova_navigator.filemanager.tasks import FileCopyOptions, OverwritePolicy, copy_files, move_files
from nova_navigator.scheduler import ResponseRequest, TaskContext, TaskStatus
from nova_navigator.vfs.filesystem import Filesystem
from nova_navigator.vfs.filesystems.local import LocalFilesystem
from nova_navigator.vfs.vpath import VPath
from nova_widgets import Response

from .common import force_cross_device, make_status, run_task

_POLICIES: list[OverwritePolicy] = ["skip", "ask", "overwrite"]
_SKIP_REASON = "folder exists"

_Operation = Callable[[TaskContext, list[VPath], VPath, FileCopyOptions | None], Awaitable[None]]


def _snapshot(root: Path) -> dict[str, bytes | None]:
    """Map every path below *root* (relative, sorted) to its bytes, or None for a folder."""
    return {str(p.relative_to(root)): (p.read_bytes() if p.is_file() else None) for p in sorted(root.rglob("*"))}


def _folder_over_file_tree(tmp_path: Path) -> tuple[Path, Path]:
    """src/x and src/y (regular files); dst/x is a real folder holding keep.txt and sub/k2.txt."""
    src = tmp_path / "src"
    src.mkdir()
    (src / "x").write_bytes(b"new-x")
    (src / "y").write_bytes(b"new-y")
    dst = tmp_path / "dst"
    (dst / "x" / "sub").mkdir(parents=True)
    (dst / "x" / "keep.txt").write_bytes(b"keep")
    (dst / "x" / "sub" / "k2.txt").write_bytes(b"keep2")
    return src, dst


def _nested_tree(tmp_path: Path) -> tuple[Path, Path]:
    """src/d/{x,other} (regular files); dst/d/x is a real folder holding keep.txt."""
    src = tmp_path / "src"
    (src / "d").mkdir(parents=True)
    (src / "d" / "x").write_bytes(b"new-x")
    (src / "d" / "other").write_bytes(b"new-other")
    dst = tmp_path / "dst"
    (dst / "d" / "x").mkdir(parents=True)
    (dst / "d" / "x" / "keep.txt").write_bytes(b"keep")
    return src, dst


def _link_over_folder_tree(tmp_path: Path) -> tuple[Path, Path]:
    """src/x is a link to src/real.txt; dst/x is a real folder holding keep.txt."""
    src = tmp_path / "src"
    src.mkdir()
    (src / "real.txt").write_bytes(b"real")
    (src / "x").symlink_to(src / "real.txt")
    dst = tmp_path / "dst"
    (dst / "x").mkdir(parents=True)
    (dst / "x" / "keep.txt").write_bytes(b"keep")
    return src, dst


async def _run(
    operation: _Operation,
    src_fs: Filesystem,
    dst_fs: Filesystem,
    sources: list[Path],
    destination: Path,
    options: FileCopyOptions,
    responses: list[Response],
) -> tuple[TaskStatus, list[ResponseRequest]]:
    status = make_status()
    requests = await asyncio.wait_for(
        run_task(
            lambda ctx: operation(ctx, [src_fs.path(s) for s in sources], dst_fs.path(destination), options),
            responses,
            status,
        ),
        timeout=20,
    )
    return status, requests


def _assert_skipped_once(status: TaskStatus, source: Path) -> None:
    assert [(s.item, s.reason) for s in status.skipped] == [(LocalFilesystem().path(source).uri, _SKIP_REASON)]
    assert status.progress.completed == status.progress.total


@pytest.mark.asyncio
@pytest.mark.parametrize("policy", _POLICIES)
async def test_copy_file_over_folder_skipped_under_every_policy(link_fs: Filesystem, tmp_path: Path, policy: OverwritePolicy) -> None:
    src, dst = _folder_over_file_tree(tmp_path)
    before = _snapshot(dst / "x")
    status, requests = await _run(copy_files, LocalFilesystem(), link_fs, [src / "x", src / "y"], dst, FileCopyOptions(overwrite=policy), [])
    assert requests == []
    assert _snapshot(dst / "x") == before
    assert (dst / "y").read_bytes() == b"new-y"
    _assert_skipped_once(status, src / "x")


@pytest.mark.asyncio
async def test_copy_single_file_over_folder_skipped(link_fs: Filesystem, tmp_path: Path) -> None:
    src, dst = _folder_over_file_tree(tmp_path)
    before = _snapshot(dst / "x")
    status, requests = await _run(copy_files, LocalFilesystem(), link_fs, [src / "x"], dst, FileCopyOptions(overwrite="ask"), [])
    assert requests == []
    assert _snapshot(dst / "x") == before
    _assert_skipped_once(status, src / "x")


@pytest.mark.asyncio
@pytest.mark.parametrize("policy", _POLICIES)
async def test_copy_nested_file_over_folder_skipped_siblings_copied(link_fs: Filesystem, tmp_path: Path, policy: OverwritePolicy) -> None:
    src, dst = _nested_tree(tmp_path)
    before = _snapshot(dst / "d" / "x")
    status, requests = await _run(copy_files, LocalFilesystem(), link_fs, [src / "d"], dst, FileCopyOptions(overwrite=policy), [])
    assert requests == []
    assert _snapshot(dst / "d" / "x") == before
    assert (dst / "d" / "other").read_bytes() == b"new-other"
    _assert_skipped_once(status, src / "d" / "x")


@pytest.mark.asyncio
async def test_copy_file_over_folder_across_filesystems(fs_pair: tuple[Filesystem, Filesystem], tmp_path: Path) -> None:
    src, dst = _folder_over_file_tree(tmp_path)
    before = _snapshot(dst / "x")
    status, requests = await _run(copy_files, fs_pair[0], fs_pair[1], [src / "x"], dst, FileCopyOptions(overwrite="ask"), [])
    assert requests == []
    assert _snapshot(dst / "x") == before
    assert [s.reason for s in status.skipped] == [_SKIP_REASON]


@pytest.mark.asyncio
async def test_folder_skip_does_not_consume_a_response(link_fs: Filesystem, tmp_path: Path) -> None:
    src, dst = _folder_over_file_tree(tmp_path)
    (dst / "y").write_bytes(b"old-y")
    before = _snapshot(dst / "x")
    status, requests = await _run(copy_files, LocalFilesystem(), link_fs, [src / "x", src / "y"], dst, FileCopyOptions(overwrite="ask"), [Response.YES])
    assert len(requests) == 1
    assert (dst / "y").read_bytes() == b"new-y"
    assert _snapshot(dst / "x") == before
    assert [s.reason for s in status.skipped] == [_SKIP_REASON]


@pytest.mark.asyncio
@pytest.mark.parametrize("follow_links", [False, True])
@pytest.mark.parametrize("policy", ["ask", "overwrite"])
async def test_copy_link_over_folder_skipped(link_fs: Filesystem, tmp_path: Path, follow_links: bool, policy: OverwritePolicy) -> None:
    src, dst = _link_over_folder_tree(tmp_path)
    before = _snapshot(dst / "x")
    options = FileCopyOptions(overwrite=policy, follow_links=follow_links)
    status, requests = await _run(copy_files, LocalFilesystem(), link_fs, [src / "x"], dst, options, [])
    assert requests == []
    assert _snapshot(dst / "x") == before
    _assert_skipped_once(status, src / "x")


@pytest.mark.asyncio
@pytest.mark.parametrize("policy", ["ask", "overwrite"])
async def test_cross_device_move_file_over_folder_keeps_source(fs_pair: tuple[Filesystem, Filesystem], tmp_path: Path, monkeypatch: pytest.MonkeyPatch, policy: OverwritePolicy) -> None:
    force_cross_device(monkeypatch)
    src, dst = _folder_over_file_tree(tmp_path)
    before = _snapshot(dst / "x")
    status, requests = await _run(move_files, fs_pair[0], fs_pair[1], [src / "x", src / "y"], dst, FileCopyOptions(overwrite=policy), [])
    assert requests == []
    assert _snapshot(dst / "x") == before
    assert (src / "x").read_bytes() == b"new-x"
    assert not (src / "y").exists()
    assert (dst / "y").read_bytes() == b"new-y"
    assert [s.reason for s in status.skipped] == [_SKIP_REASON]
    assert status.progress.completed == status.progress.total


@pytest.mark.asyncio
async def test_cross_device_move_nested_file_over_folder_keeps_source(link_fs: Filesystem, tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    force_cross_device(monkeypatch)
    src, dst = _nested_tree(tmp_path)
    before = _snapshot(dst / "d" / "x")
    status, requests = await _run(move_files, LocalFilesystem(), link_fs, [src / "d"], dst, FileCopyOptions(overwrite="ask"), [])
    assert requests == []
    assert (src / "d" / "x").read_bytes() == b"new-x"
    assert not (src / "d" / "other").exists()
    assert (dst / "d" / "other").read_bytes() == b"new-other"
    assert _snapshot(dst / "d" / "x") == before
    assert [s.reason for s in status.skipped] == [_SKIP_REASON]


@pytest.mark.asyncio
@pytest.mark.parametrize("follow_links", [False, True])
async def test_cross_device_move_link_over_folder_keeps_source(link_fs: Filesystem, tmp_path: Path, monkeypatch: pytest.MonkeyPatch, follow_links: bool) -> None:
    force_cross_device(monkeypatch)
    src, dst = _link_over_folder_tree(tmp_path)
    before = _snapshot(dst / "x")
    options = FileCopyOptions(overwrite="ask", follow_links=follow_links)
    status, requests = await _run(move_files, LocalFilesystem(), link_fs, [src / "x"], dst, options, [])
    assert requests == []
    assert (src / "x").is_symlink()
    assert _snapshot(dst / "x") == before
    assert [s.reason for s in status.skipped] == [_SKIP_REASON]


@pytest.mark.asyncio
@pytest.mark.parametrize("policy", _POLICIES)
async def test_same_device_move_file_over_folder_keeps_folder_and_source(tmp_path: Path, policy: OverwritePolicy) -> None:
    src, dst = _folder_over_file_tree(tmp_path)
    before = _snapshot(dst / "x")
    local = LocalFilesystem()
    status, requests = await _run(move_files, local, local, [src / "x", src / "y"], dst, FileCopyOptions(overwrite=policy), [])
    assert requests == []
    assert _snapshot(dst / "x") == before
    assert (src / "x").read_bytes() == b"new-x"
    assert not (src / "y").exists()
    assert (dst / "y").read_bytes() == b"new-y"
    assert [s.reason for s in status.skipped] == [_SKIP_REASON]
    assert status.progress.completed == status.progress.total


@pytest.mark.asyncio
@pytest.mark.parametrize("policy", _POLICIES)
async def test_same_device_move_nested_file_over_folder_keeps_folder_and_source(tmp_path: Path, policy: OverwritePolicy) -> None:
    src, dst = _nested_tree(tmp_path)
    before = _snapshot(dst / "d" / "x")
    local = LocalFilesystem()
    status, requests = await _run(move_files, local, local, [src / "d"], dst, FileCopyOptions(overwrite=policy), [])
    assert requests == []
    assert _snapshot(dst / "d" / "x") == before
    assert (src / "d" / "x").read_bytes() == b"new-x"
    assert not (src / "d" / "other").exists()
    assert (dst / "d" / "other").read_bytes() == b"new-other"
    assert [s.reason for s in status.skipped] == [_SKIP_REASON]


@pytest.mark.asyncio
async def test_same_device_move_link_over_folder_skipped(tmp_path: Path) -> None:
    src, dst = _link_over_folder_tree(tmp_path)
    before = _snapshot(dst / "x")
    local = LocalFilesystem()
    status, requests = await _run(move_files, local, local, [src / "x"], dst, FileCopyOptions(overwrite="ask"), [])
    assert requests == []
    assert (src / "x").is_symlink()
    assert _snapshot(dst / "x") == before
    assert [s.reason for s in status.skipped] == [_SKIP_REASON]
