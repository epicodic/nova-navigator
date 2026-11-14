"""A file copied or moved onto a destination symlink replaces the link and never writes through it (REQ-31)."""

import asyncio
import os
import threading
from pathlib import Path

import pytest

from nova_navigator.filemanager.tasks import FileCopyOptions, OverwritePolicy, copy_file, copy_files, move_files
from nova_navigator.scheduler import ResponseRequest, TaskCancelled, TaskStatus
from nova_navigator.vfs.filesystem import Filesystem
from nova_navigator.vfs.filesystems.local import LocalFilesystem
from nova_navigator.vfs.vpath import VPath
from nova_widgets import Response
from tests._utils.mock_filesystem import MockFilesystem

from .common import copy_between, force_cross_device, make_status, move_between, read_all, reasons, run_task

_NEW = b"new-content"
_TARGET = b"target-bytes"

_POLICIES = [
    pytest.param("overwrite", [], id="overwrite"),
    pytest.param("ask", [Response.YES], id="ask-yes"),
]


def _link_tree(tmp_path: Path, target_kind: str, *, link_name: str = "l") -> tuple[Path, Path, Path, Path]:
    """Build src/<link_name> (regular, _NEW), outside/f (_TARGET, absent for "broken") and dst/<link_name> -> outside/f.

    Returns (src_file, dst_dir, link, target). The link text is the absolute target path so it works through the SSH stub.
    """
    src = tmp_path / "src"
    src.mkdir()
    src_file = src / link_name
    src_file.write_bytes(_NEW)
    outside = tmp_path / "outside"
    outside.mkdir()
    target = outside / "f"
    if target_kind == "file":
        target.write_bytes(_TARGET)
    dst = tmp_path / "dst"
    dst.mkdir()
    link = dst / link_name
    link.symlink_to(target)
    return src_file, dst, link, target


def _assert_replaced(link: Path, target: Path, target_kind: str, target_mtime_ns: int | None) -> None:
    assert not link.is_symlink()
    assert link.read_bytes() == _NEW
    if target_kind == "file":
        assert target.read_bytes() == _TARGET
        assert target.stat().st_mtime_ns == target_mtime_ns
    else:
        assert not os.path.lexists(target)


def _assert_link_untouched(link: Path, target: Path, target_kind: str, target_mtime_ns: int | None) -> None:
    assert link.is_symlink()
    assert os.readlink(link) == str(target)
    if target_kind == "file":
        assert target.read_bytes() == _TARGET
        assert target.stat().st_mtime_ns == target_mtime_ns
    else:
        assert not os.path.lexists(target)


def _mtime(target: Path) -> int | None:
    return target.stat().st_mtime_ns if target.exists() else None


async def _run_copy(
    fs_pair: tuple[Filesystem, Filesystem],
    sources: list[Path],
    destination: Path,
    options: FileCopyOptions,
    responses: list[Response],
) -> tuple[TaskStatus, list[ResponseRequest]]:
    src_fs, dst_fs = fs_pair
    status = make_status()
    requests = await asyncio.wait_for(
        run_task(lambda ctx: copy_files(ctx, [src_fs.path(s) for s in sources], dst_fs.path(destination), options), responses, status),
        timeout=20,
    )
    return status, requests


async def _run_move(
    fs_pair: tuple[Filesystem, Filesystem],
    sources: list[Path],
    destination: Path,
    options: FileCopyOptions,
    responses: list[Response],
) -> tuple[TaskStatus, list[ResponseRequest]]:
    src_fs, dst_fs = fs_pair
    status = make_status()
    requests = await asyncio.wait_for(
        run_task(lambda ctx: move_files(ctx, [src_fs.path(s) for s in sources], dst_fs.path(destination), options), responses, status),
        timeout=20,
    )
    return status, requests


# ---------------------------------------------------------------------------
# Copy: the link is replaced, never written through
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
@pytest.mark.parametrize("target_kind", ["file", "broken"])
@pytest.mark.parametrize(("policy", "responses"), _POLICIES)
async def test_copy_over_link_replaces_link_not_target(link_fs: Filesystem, tmp_path: Path, target_kind: str, policy: OverwritePolicy, responses: list[Response]) -> None:
    src_file, dst, link, target = _link_tree(tmp_path, target_kind)
    mtime = _mtime(target)

    status, requests = await _run_copy((LocalFilesystem(), link_fs), [src_file], dst, FileCopyOptions(overwrite=policy), responses)

    _assert_replaced(link, target, target_kind, mtime)
    assert len(requests) == len(responses)
    assert reasons(status) == []
    assert status.progress.completed == status.progress.total


@pytest.mark.asyncio
@pytest.mark.parametrize("target_kind", ["file", "broken"])
async def test_copy_onto_link_skip_leaves_link(link_fs: Filesystem, tmp_path: Path, target_kind: str) -> None:
    src_file, dst, link, target = _link_tree(tmp_path, target_kind)
    mtime = _mtime(target)

    status, requests = await _run_copy((LocalFilesystem(), link_fs), [src_file], dst, FileCopyOptions(overwrite="skip"), [])

    _assert_link_untouched(link, target, target_kind, mtime)
    assert requests == []
    assert reasons(status) == []


@pytest.mark.asyncio
async def test_copy_onto_link_ask_no_leaves_link(link_fs: Filesystem, tmp_path: Path) -> None:
    src_file, dst, link, target = _link_tree(tmp_path, "file")
    mtime = _mtime(target)

    _status, requests = await _run_copy((LocalFilesystem(), link_fs), [src_file], dst, FileCopyOptions(overwrite="ask"), [Response.NO])

    _assert_link_untouched(link, target, "file", mtime)
    assert len(requests) == 1


@pytest.mark.asyncio
async def test_copy_of_link_target_onto_link_keeps_target(link_fs: Filesystem, tmp_path: Path) -> None:
    _src_file, dst, link, target = _link_tree(tmp_path, "file")

    await run_task(lambda ctx: copy_file(ctx, link_fs.path(target), link_fs.path(link), FileCopyOptions(overwrite="overwrite")))

    assert target.read_bytes() == _TARGET
    assert not link.is_symlink()
    assert link.read_bytes() == _TARGET
    assert dst.exists()


@pytest.mark.asyncio
@pytest.mark.parametrize("target_kind", ["file", "broken"])
async def test_copy_across_filesystems_over_link(fs_pair: tuple[Filesystem, Filesystem], tmp_path: Path, target_kind: str) -> None:
    src_file, dst, link, target = _link_tree(tmp_path, target_kind)
    mtime = _mtime(target)

    await copy_between(fs_pair, [src_file], dst, FileCopyOptions(overwrite="overwrite"))

    _assert_replaced(link, target, target_kind, mtime)


@pytest.mark.asyncio
@pytest.mark.parametrize("target_kind", ["file", "broken"])
async def test_copy_follow_links_source_link_over_destination_link(link_fs: Filesystem, tmp_path: Path, target_kind: str) -> None:
    src_file, dst, link, target = _link_tree(tmp_path, target_kind)
    real = src_file.parent / "real.txt"
    real.write_bytes(_NEW)
    src_file.unlink()
    src_file.symlink_to(real)
    mtime = _mtime(target)

    await copy_between((LocalFilesystem(), link_fs), [src_file], dst, FileCopyOptions(overwrite="overwrite", follow_links=True))

    _assert_replaced(link, target, target_kind, mtime)


@pytest.mark.asyncio
async def test_copy_nested_over_links_asks_once_per_entry(link_fs: Filesystem, tmp_path: Path) -> None:
    src = tmp_path / "src"
    (src / "d" / "sub").mkdir(parents=True)
    (src / "d" / "a").write_bytes(_NEW)
    (src / "d" / "sub" / "c").write_bytes(_NEW)
    (src / "d" / "other").write_bytes(_NEW)
    outside = tmp_path / "outside"
    outside.mkdir()
    (outside / "a").write_bytes(_TARGET)
    (outside / "c").write_bytes(_TARGET)
    dst = tmp_path / "dst"
    (dst / "d" / "sub").mkdir(parents=True)
    (dst / "d" / "a").symlink_to(outside / "a")
    (dst / "d" / "sub" / "c").symlink_to(outside / "c")

    status, requests = await _run_copy((LocalFilesystem(), link_fs), [src / "d"], dst, FileCopyOptions(overwrite="ask"), [Response.YES, Response.YES])

    assert len(requests) == 2
    for name in ("a", "sub/c"):
        assert not (dst / "d" / name).is_symlink()
        assert (dst / "d" / name).read_bytes() == _NEW
    assert (outside / "a").read_bytes() == _TARGET
    assert (outside / "c").read_bytes() == _TARGET
    assert (dst / "d" / "other").read_bytes() == _NEW
    assert status.progress.completed == status.progress.total


@pytest.mark.asyncio
async def test_copy_yes_to_all_over_two_links_asks_once(link_fs: Filesystem, tmp_path: Path) -> None:
    src = tmp_path / "src"
    src.mkdir()
    outside = tmp_path / "outside"
    outside.mkdir()
    dst = tmp_path / "dst"
    dst.mkdir()
    for name in ("l1", "l2"):
        (src / name).write_bytes(_NEW)
        (outside / name).write_bytes(_TARGET)
        (dst / name).symlink_to(outside / name)

    _status, requests = await _run_copy((LocalFilesystem(), link_fs), [src / "l1", src / "l2"], dst, FileCopyOptions(overwrite="ask"), [Response.ALL])

    assert len(requests) == 1
    for name in ("l1", "l2"):
        assert not (dst / name).is_symlink()
        assert (dst / name).read_bytes() == _NEW
        assert (outside / name).read_bytes() == _TARGET


@pytest.mark.asyncio
async def test_copy_over_loop_link_replaces_it(link_fs: Filesystem, tmp_path: Path) -> None:
    src_file, dst, link, _target = _link_tree(tmp_path, "file")
    link.unlink()
    link.symlink_to("l")

    await copy_between((LocalFilesystem(), link_fs), [src_file], dst, FileCopyOptions(overwrite="overwrite"))

    assert not link.is_symlink()
    assert link.read_bytes() == _NEW


@pytest.mark.asyncio
@pytest.mark.parametrize(("policy", "responses"), _POLICIES)
async def test_copy_over_link_to_folder_replaces_link_only(link_fs: Filesystem, tmp_path: Path, policy: OverwritePolicy, responses: list[Response]) -> None:
    src_file, dst, link, _target = _link_tree(tmp_path, "file")
    folder = tmp_path / "folder"
    folder.mkdir()
    (folder / "keep.txt").write_bytes(b"keep")
    link.unlink()
    link.symlink_to(folder)

    status, _requests = await _run_copy((LocalFilesystem(), link_fs), [src_file], dst, FileCopyOptions(overwrite=policy), responses)

    assert not link.is_symlink()
    assert link.read_bytes() == _NEW
    assert sorted(p.name for p in folder.iterdir()) == ["keep.txt"]
    assert (folder / "keep.txt").read_bytes() == b"keep"
    assert reasons(status) == []


@pytest.mark.asyncio
async def test_copy_onto_link_to_folder_skip_keeps_link(link_fs: Filesystem, tmp_path: Path) -> None:
    src_file, dst, link, _target = _link_tree(tmp_path, "file")
    folder = tmp_path / "folder"
    folder.mkdir()
    (folder / "keep.txt").write_bytes(b"keep")
    link.unlink()
    link.symlink_to(folder)

    status, requests = await _run_copy((LocalFilesystem(), link_fs), [src_file], dst, FileCopyOptions(overwrite="skip"), [])

    assert link.is_symlink()
    assert os.readlink(link) == str(folder)
    assert sorted(p.name for p in folder.iterdir()) == ["keep.txt"]
    assert requests == []
    assert reasons(status) == []


@pytest.mark.asyncio
async def test_copy_over_regular_file_keeps_inode(tmp_path: Path) -> None:
    src_file, dst, _link, _target = _link_tree(tmp_path, "file")
    plain = dst / "plain"
    plain.write_bytes(b"old")
    (src_file.parent / "plain").write_bytes(_NEW)
    inode = plain.stat().st_ino

    await copy_between((LocalFilesystem(), LocalFilesystem()), [src_file.parent / "plain"], dst, FileCopyOptions(overwrite="overwrite"))

    assert plain.read_bytes() == _NEW
    assert plain.stat().st_ino == inode


@pytest.mark.asyncio
async def test_copy_sees_link_created_after_the_listing_was_cached(link_fs: Filesystem, tmp_path: Path) -> None:
    src_file, dst, link, target = _link_tree(tmp_path, "file")
    link.unlink()
    (dst / "keep.txt").write_bytes(b"keep")
    _ = link_fs.stat(link_fs.path(dst / "keep.txt"))  # caches the parent listing on SSH
    link.symlink_to(target)
    mtime = _mtime(target)

    await copy_between((LocalFilesystem(), link_fs), [src_file], dst, FileCopyOptions(overwrite="overwrite"))

    _assert_replaced(link, target, "file", mtime)


# ---------------------------------------------------------------------------
# Copy: a failed copy puts the removed link back
# ---------------------------------------------------------------------------


def _mock_link_destination() -> tuple[MockFilesystem, MockFilesystem]:
    src_fs = MockFilesystem({"/elsewhere/src.txt": b"data"})
    dst_fs = MockFilesystem({"/elsewhere/f": _TARGET}, links={"/dst/l": "/elsewhere/f"})
    return src_fs, dst_fs


@pytest.mark.asyncio
async def test_failed_write_restores_removed_link() -> None:
    src_fs = MockFilesystem({"/elsewhere/src.txt": b"data"})
    dst_fs = MockFilesystem(
        {"/elsewhere/f": _TARGET},
        links={"/dst/l": "/elsewhere/f"},
        write_errors={"/dst/l": OSError("disk full")},  # keyed by the resolved path: only a write after the link was removed fails
    )

    with pytest.raises(OSError, match="disk full"):
        await run_task(lambda ctx: copy_file(ctx, src_fs.path("/elsewhere/src.txt"), dst_fs.path("/dst/l"), FileCopyOptions(overwrite="overwrite")))

    assert dst_fs.readlink(dst_fs.path("/dst/l")) == "/elsewhere/f"
    assert read_all(dst_fs, "/elsewhere/f") == _TARGET


@pytest.mark.asyncio
async def test_failed_read_restores_removed_link() -> None:
    src_fs = MockFilesystem({"/elsewhere/src.txt": b"data"}, read_errors={"/elsewhere/src.txt": OSError("disk error")})
    dst_fs = MockFilesystem({"/elsewhere/f": _TARGET}, links={"/dst/l": "/elsewhere/f"})

    with pytest.raises(OSError, match="disk error"):
        await run_task(lambda ctx: copy_file(ctx, src_fs.path("/elsewhere/src.txt"), dst_fs.path("/dst/l"), FileCopyOptions(overwrite="overwrite")))

    assert dst_fs.readlink(dst_fs.path("/dst/l")) == "/elsewhere/f"
    assert read_all(dst_fs, "/elsewhere/f") == _TARGET


@pytest.mark.asyncio
async def test_cancelled_copy_restores_removed_link() -> None:
    src_fs, dst_fs = _mock_link_destination()
    cancel = threading.Event()
    cancel.set()
    status = make_status(cancel_event=cancel)

    with pytest.raises(TaskCancelled):
        await run_task(
            lambda ctx: copy_file(ctx, src_fs.path("/elsewhere/src.txt"), dst_fs.path("/dst/l"), FileCopyOptions(overwrite="overwrite")),
            status=status,
        )

    assert dst_fs.readlink(dst_fs.path("/dst/l")) == "/elsewhere/f"
    assert read_all(dst_fs, "/elsewhere/f") == _TARGET
    assert dst_fs.writers[0].close_count == 1


@pytest.mark.asyncio
async def test_remove_failure_keeps_link_and_writes_nothing(monkeypatch: pytest.MonkeyPatch) -> None:
    src_fs, dst_fs = _mock_link_destination()

    def failing_remove(_self: MockFilesystem, _path: VPath) -> None:
        raise PermissionError("no remove")

    monkeypatch.setattr(MockFilesystem, "remove", failing_remove)

    with pytest.raises(PermissionError, match="no remove"):
        await run_task(lambda ctx: copy_file(ctx, src_fs.path("/elsewhere/src.txt"), dst_fs.path("/dst/l"), FileCopyOptions(overwrite="overwrite")))

    assert dst_fs.readlink(dst_fs.path("/dst/l")) == "/elsewhere/f"
    assert dst_fs.writers == []


@pytest.mark.asyncio
async def test_unreadable_source_never_removes_link(monkeypatch: pytest.MonkeyPatch) -> None:
    src_fs, dst_fs = _mock_link_destination()

    def failing_read(_path: VPath) -> None:
        raise PermissionError("no read")

    monkeypatch.setattr(src_fs, "read", failing_read)

    with pytest.raises(PermissionError, match="no read"):
        await run_task(lambda ctx: copy_file(ctx, src_fs.path("/elsewhere/src.txt"), dst_fs.path("/dst/l"), FileCopyOptions(overwrite="overwrite")))

    assert dst_fs.readlink(dst_fs.path("/dst/l")) == "/elsewhere/f"
    assert dst_fs.writers == []


@pytest.mark.asyncio
async def test_link_restore_failure_does_not_hide_the_original_error(monkeypatch: pytest.MonkeyPatch) -> None:
    src_fs = MockFilesystem({"/elsewhere/src.txt": b"data"})
    dst_fs = MockFilesystem(
        {"/elsewhere/f": _TARGET},
        links={"/dst/l": "/elsewhere/f"},
        write_errors={"/dst/l": OSError("disk full")},
    )

    def failing_symlink(_self: MockFilesystem, _path: VPath, _target: str) -> None:
        raise OSError("restore")

    monkeypatch.setattr(MockFilesystem, "symlink", failing_symlink)

    with pytest.raises(OSError, match="disk full"):
        await run_task(lambda ctx: copy_file(ctx, src_fs.path("/elsewhere/src.txt"), dst_fs.path("/dst/l"), FileCopyOptions(overwrite="overwrite")))


# ---------------------------------------------------------------------------
# Cross-device move: the link is replaced and the source removed
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
@pytest.mark.parametrize("target_kind", ["file", "broken"])
async def test_cross_device_move_over_link_replaces_link(fs_pair: tuple[Filesystem, Filesystem], tmp_path: Path, monkeypatch: pytest.MonkeyPatch, target_kind: str) -> None:
    force_cross_device(monkeypatch)
    src_file, dst, link, target = _link_tree(tmp_path, target_kind)
    mtime = _mtime(target)

    await move_between(fs_pair, [src_file], dst, FileCopyOptions(overwrite="overwrite"))

    _assert_replaced(link, target, target_kind, mtime)
    assert not src_file.exists()


@pytest.mark.asyncio
async def test_cross_device_move_onto_link_skip_keeps_source_and_link(link_fs: Filesystem, tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    force_cross_device(monkeypatch)
    src_file, dst, link, target = _link_tree(tmp_path, "file")
    mtime = _mtime(target)

    _status, requests = await _run_move((LocalFilesystem(), link_fs), [src_file], dst, FileCopyOptions(overwrite="skip"), [])

    _assert_link_untouched(link, target, "file", mtime)
    assert src_file.read_bytes() == _NEW
    assert requests == []


@pytest.mark.asyncio
async def test_cross_device_move_onto_link_ask_once(link_fs: Filesystem, tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    force_cross_device(monkeypatch)
    src_file, dst, link, target = _link_tree(tmp_path, "file")
    mtime = _mtime(target)

    _status, requests = await _run_move((LocalFilesystem(), link_fs), [src_file], dst, FileCopyOptions(overwrite="ask"), [Response.YES])

    assert len(requests) == 1
    _assert_replaced(link, target, "file", mtime)
    assert not src_file.exists()


@pytest.mark.asyncio
async def test_cross_device_move_follow_links_over_link(link_fs: Filesystem, tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    force_cross_device(monkeypatch)
    src_file, dst, link, target = _link_tree(tmp_path, "file")
    real = src_file.parent / "real.txt"
    real.write_bytes(_NEW)
    src_file.unlink()
    src_file.symlink_to(real)
    mtime = _mtime(target)

    await move_between((LocalFilesystem(), link_fs), [src_file], dst, FileCopyOptions(overwrite="overwrite", follow_links=True))

    _assert_replaced(link, target, "file", mtime)
    assert not os.path.lexists(src_file)


@pytest.mark.asyncio
async def test_cross_device_move_nested_over_link(link_fs: Filesystem, tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    force_cross_device(monkeypatch)
    src = tmp_path / "src"
    (src / "d").mkdir(parents=True)
    (src / "d" / "a").write_bytes(_NEW)
    (src / "d" / "other").write_bytes(_NEW)
    outside = tmp_path / "outside"
    outside.mkdir()
    (outside / "a").write_bytes(_TARGET)
    dst = tmp_path / "dst"
    (dst / "d").mkdir(parents=True)
    (dst / "d" / "a").symlink_to(outside / "a")

    await move_between((LocalFilesystem(), link_fs), [src / "d"], dst, FileCopyOptions(overwrite="overwrite"))

    assert not (dst / "d" / "a").is_symlink()
    assert (dst / "d" / "a").read_bytes() == _NEW
    assert (dst / "d" / "other").read_bytes() == _NEW
    assert (outside / "a").read_bytes() == _TARGET


@pytest.mark.asyncio
async def test_cross_device_move_sees_link_created_after_the_listing_was_cached(link_fs: Filesystem, tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    force_cross_device(monkeypatch)
    src_file, dst, link, target = _link_tree(tmp_path, "file")
    link.unlink()
    (dst / "keep.txt").write_bytes(b"keep")
    _ = link_fs.stat(link_fs.path(dst / "keep.txt"))
    link.symlink_to(target)
    mtime = _mtime(target)

    await move_between((LocalFilesystem(), link_fs), [src_file], dst, FileCopyOptions(overwrite="overwrite"))

    _assert_replaced(link, target, "file", mtime)
    assert not src_file.exists()
