"""Copy to a destination without symlink support: copy the target, skip broken and looping links (REQ-4, REQ-5)."""

import asyncio
from pathlib import Path

import pytest

from nova_navigator.filemanager.tasks import copy_files
from nova_navigator.scheduler import TaskStatus
from nova_navigator.vfs.filesystems.local import LocalFilesystem
from nova_navigator.vfs.vpath import VPath
from tests._utils.mock_filesystem import MockFilesystem

from .common import make_status, read_all, run_task


async def _copy(sources: list[VPath], destination: VPath) -> TaskStatus:
    status = make_status()
    await asyncio.wait_for(run_task(lambda ctx: copy_files(ctx, sources, destination), status=status), timeout=20)
    return status


def _reasons(status: TaskStatus) -> list[str]:
    return [skipped.reason for skipped in status.skipped]


@pytest.mark.asyncio
async def test_nonlink_destination_file_link_copies_target_content(tmp_path: Path) -> None:
    (tmp_path / "src").mkdir()
    (tmp_path / "src" / "f.txt").write_text("content")
    (tmp_path / "src" / "l").symlink_to("f.txt")
    dst_fs = MockFilesystem(symlinks=False)
    local = LocalFilesystem()
    status = await _copy([local.path(tmp_path / "src")], dst_fs.path("/home/user"))
    assert read_all(dst_fs, "/home/user/src/l") == b"content"
    assert _reasons(status) == []
    assert status.progress.completed == status.progress.total


@pytest.mark.asyncio
async def test_nonlink_destination_directory_link_copies_tree_with_nested_rule(tmp_path: Path) -> None:
    tgt = tmp_path / "src" / "tgt"
    (tgt / "inner").mkdir(parents=True)
    (tgt / "f2.txt").write_text("two")
    (tgt / "inner" / "z.txt").write_text("zed")
    (tgt / "nf").symlink_to("f2.txt")
    (tgt / "nd").symlink_to("inner", target_is_directory=True)
    (tmp_path / "src" / "lnk").symlink_to("tgt", target_is_directory=True)
    dst_fs = MockFilesystem(symlinks=False)
    status = await _copy([LocalFilesystem().path(tmp_path / "src" / "lnk")], dst_fs.path("/home/user"))
    assert read_all(dst_fs, "/home/user/lnk/f2.txt") == b"two"
    assert read_all(dst_fs, "/home/user/lnk/nf") == b"two"
    assert read_all(dst_fs, "/home/user/lnk/inner/z.txt") == b"zed"
    assert read_all(dst_fs, "/home/user/lnk/nd/z.txt") == b"zed"
    assert _reasons(status) == []
    assert status.progress.completed == status.progress.total


@pytest.mark.asyncio
async def test_nonlink_destination_broken_link_skipped_and_reported(tmp_path: Path) -> None:
    (tmp_path / "src").mkdir()
    (tmp_path / "src" / "ok.txt").write_text("ok")
    (tmp_path / "src" / "b").symlink_to("nope")
    local = LocalFilesystem()
    dst_fs = MockFilesystem(symlinks=False)
    status = await _copy([local.path(tmp_path / "src")], dst_fs.path("/home/user"))
    assert read_all(dst_fs, "/home/user/src/ok.txt") == b"ok"
    assert not dst_fs.exists("/home/user/src/b")
    assert [(s.item, s.reason) for s in status.skipped] == [(local.path(tmp_path / "src" / "b").uri, "broken link")]
    assert status.progress.completed == status.progress.total


@pytest.mark.asyncio
async def test_nonlink_destination_self_loop_is_reported_broken(tmp_path: Path) -> None:
    (tmp_path / "src").mkdir()
    (tmp_path / "src" / "a").symlink_to("a")
    dst_fs = MockFilesystem(symlinks=False)
    status = await _copy([LocalFilesystem().path(tmp_path / "src")], dst_fs.path("/home/user"))
    assert _reasons(status) == ["broken link"]
    assert not dst_fs.exists("/home/user/src/a")


@pytest.mark.asyncio
async def test_nonlink_destination_ancestor_link_skipped_after_target_copied_once(tmp_path: Path) -> None:
    (tmp_path / "src" / "d").mkdir(parents=True)
    (tmp_path / "src" / "d" / "g.txt").write_text("g")
    (tmp_path / "src" / "d" / "up").symlink_to("..", target_is_directory=True)
    local = LocalFilesystem()
    dst_fs = MockFilesystem(symlinks=False)
    status = await _copy([local.path(tmp_path / "src")], dst_fs.path("/home/user"))
    assert read_all(dst_fs, "/home/user/src/d/g.txt") == b"g"
    assert not dst_fs.exists("/home/user/src/d/up")
    assert [(s.item, s.reason) for s in status.skipped] == [(local.path(tmp_path / "src" / "d" / "up").uri, "link loop")]
    assert status.progress.completed == status.progress.total


@pytest.mark.asyncio
async def test_nonlink_destination_mutual_links_terminate(tmp_path: Path) -> None:
    for name in ("a", "b"):
        (tmp_path / "src" / name).mkdir(parents=True)
        (tmp_path / "src" / name / f"{name}.txt").write_text(name)
    (tmp_path / "src" / "a" / "lnk").symlink_to("../b", target_is_directory=True)
    (tmp_path / "src" / "b" / "back").symlink_to("../a", target_is_directory=True)
    dst_fs = MockFilesystem(symlinks=False)
    status = await _copy([LocalFilesystem().path(tmp_path / "src")], dst_fs.path("/home/user"))
    assert read_all(dst_fs, "/home/user/src/a/lnk/b.txt") == b"b"
    assert read_all(dst_fs, "/home/user/src/b/back/a.txt") == b"a"
    assert not dst_fs.exists("/home/user/src/a/lnk/back")  # entered via a/lnk (chain: src/a), back -> src/a is on the chain
    assert not dst_fs.exists("/home/user/src/b/back/lnk")  # entered via b/back (chain: src/b), lnk -> src/b is on the chain
    assert _reasons(status) == ["link loop", "link loop"]
    assert status.progress.completed == status.progress.total


@pytest.mark.asyncio
async def test_nonlink_destination_two_links_to_same_directory_both_copied(tmp_path: Path) -> None:
    (tmp_path / "src" / "b").mkdir(parents=True)
    (tmp_path / "src" / "b" / "f.txt").write_text("f")
    (tmp_path / "src" / "x").symlink_to("b", target_is_directory=True)
    (tmp_path / "src" / "y").symlink_to("b", target_is_directory=True)
    dst_fs = MockFilesystem(symlinks=False)
    status = await _copy([LocalFilesystem().path(tmp_path / "src")], dst_fs.path("/home/user"))
    assert read_all(dst_fs, "/home/user/src/x/f.txt") == b"f"
    assert read_all(dst_fs, "/home/user/src/y/f.txt") == b"f"
    assert _reasons(status) == []


class _NoRealPathFs(MockFilesystem):
    def real_path(self, path: VPath) -> VPath | None:
        return None


@pytest.mark.asyncio
async def test_depth_guard_stops_loop_when_real_path_is_unavailable() -> None:
    src_fs = _NoRealPathFs({"/src/d/g.txt": b"g"}, links={"/src/d/up": ".."})
    dst_fs = MockFilesystem(symlinks=False)
    status = await _copy([src_fs.path("/src")], dst_fs.path("/home/user"))
    assert dst_fs.exists("/home/user/src/d/g.txt")
    assert _reasons(status) == ["link loop"]
    assert status.progress.completed == status.progress.total


@pytest.mark.asyncio
async def test_unresolvable_real_path_error_is_reported_broken(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    (tmp_path / "src" / "d").mkdir(parents=True)
    (tmp_path / "src" / "l").symlink_to("d", target_is_directory=True)

    def fail(_self: LocalFilesystem, _path: VPath) -> VPath | None:
        raise FileNotFoundError("gone")

    monkeypatch.setattr(LocalFilesystem, "real_path", fail)
    dst_fs = MockFilesystem(symlinks=False)
    status = await _copy([LocalFilesystem().path(tmp_path / "src")], dst_fs.path("/home/user"))
    assert _reasons(status) == ["broken link"]
