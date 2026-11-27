"""Erase must remove links themselves and never follow them (local and SSH)."""

import asyncio
import shutil
import threading
from collections.abc import AsyncIterator, Callable
from pathlib import Path

import pytest

from nova_navigator.filemanager.tasks import erase_files
from nova_navigator.scheduler import AsyncTaskScheduler, ResponseRequest
from nova_navigator.vfs.filesystem import Filesystem
from nova_navigator.vfs.vpath import VPath
from nova_widgets import Response

from .common import make_status, run_task


def _outside(tmp_path: Path) -> Path:
    outside = tmp_path / "outside"
    (outside / "sub").mkdir(parents=True)
    (outside / "keep.txt").write_text("keep")
    (outside / "sub" / "deep.txt").write_text("deep")
    return outside


def _snapshot(root: Path) -> dict[str, tuple[bool, bytes, int]]:
    return {str(p.relative_to(root)): (p.is_dir(), b"" if p.is_dir() else p.read_bytes(), p.stat().st_ino) for p in sorted(root.rglob("*"))}


@pytest.mark.asyncio
async def test_erase_dir_with_link_to_outside_dir(link_fs: Filesystem, tmp_path: Path) -> None:
    outside = _outside(tmp_path)
    tree = tmp_path / "tree"
    tree.mkdir()
    (tree / "f.txt").write_text("x")
    (tree / "out").symlink_to(outside, target_is_directory=True)
    before = _snapshot(outside)
    requests = await run_task(lambda ctx: erase_files(ctx, [link_fs.path(tree)]), [Response.YES])
    assert not tree.exists()
    assert _snapshot(outside) == before
    assert [r.message for r in requests] == [f"Directory '{tree}' is not empty. Delete it recursively?"]


@pytest.mark.asyncio
async def test_erase_selected_dir_link_removes_only_link(link_fs: Filesystem, tmp_path: Path) -> None:
    outside = _outside(tmp_path)
    lnk = tmp_path / "lnk"
    lnk.symlink_to(outside, target_is_directory=True)
    before = _snapshot(outside)
    await run_task(lambda ctx: erase_files(ctx, [link_fs.path(lnk)]))
    assert not lnk.is_symlink()
    assert _snapshot(outside) == before


@pytest.mark.asyncio
async def test_erase_dir_link_no_prompt(link_fs: Filesystem, tmp_path: Path) -> None:
    outside = _outside(tmp_path)
    lnk = tmp_path / "lnk"
    lnk.symlink_to(outside, target_is_directory=True)
    requests = await run_task(lambda ctx: erase_files(ctx, [link_fs.path(lnk)]))
    assert requests == []
    assert not lnk.is_symlink()


@pytest.mark.asyncio
async def test_erase_selected_file_link_removes_only_link(link_fs: Filesystem, tmp_path: Path) -> None:
    target = tmp_path / "target.txt"
    target.write_text("t")
    lnk = tmp_path / "lnk"
    lnk.symlink_to(target)
    await run_task(lambda ctx: erase_files(ctx, [link_fs.path(lnk)]))
    assert target.read_text() == "t"
    assert not lnk.is_symlink()


@pytest.mark.asyncio
async def test_erase_dir_with_broken_link(link_fs: Filesystem, tmp_path: Path) -> None:
    tree = tmp_path / "tree"
    tree.mkdir()
    (tree / "f").write_text("x")
    (tree / "b").symlink_to("nope")
    await run_task(lambda ctx: erase_files(ctx, [link_fs.path(tree)]), [Response.YES])
    assert not tree.exists()


@pytest.mark.asyncio
async def test_erase_dir_with_link_to_ancestor(link_fs: Filesystem, tmp_path: Path) -> None:
    tree = tmp_path / "tree"
    (tree / "d").mkdir(parents=True)
    (tree / "d" / "up").symlink_to("..", target_is_directory=True)
    await asyncio.wait_for(run_task(lambda ctx: erase_files(ctx, [link_fs.path(tree)]), [Response.YES]), timeout=20)
    assert not tree.exists()


@pytest.mark.asyncio
async def test_erase_self_loop_link(link_fs: Filesystem, tmp_path: Path) -> None:
    a = tmp_path / "a"
    a.symlink_to("a")
    await run_task(lambda ctx: erase_files(ctx, [link_fs.path(a)]))
    assert not a.is_symlink()


@pytest.mark.asyncio
async def test_erase_dir_with_inner_links_single_prompt(link_fs: Filesystem, tmp_path: Path) -> None:
    outside = _outside(tmp_path)
    tree = tmp_path / "tree"
    (tree / "sub").mkdir(parents=True)
    (tree / "f.txt").write_text("x")
    (tree / "l1").symlink_to(outside, target_is_directory=True)
    (tree / "l2").symlink_to("f.txt")
    (tree / "sub" / "l3").symlink_to(outside, target_is_directory=True)
    before = _snapshot(outside)
    requests = await run_task(lambda ctx: erase_files(ctx, [link_fs.path(tree)]), [Response.YES])
    assert len(requests) == 1
    assert not tree.exists()
    assert _snapshot(outside) == before


@pytest.mark.asyncio
async def test_erase_refreshes_stale_top_level_stat(link_fs: Filesystem, tmp_path: Path) -> None:
    outside = _outside(tmp_path)
    d = tmp_path / "d"
    d.mkdir()
    vp = link_fs.path(d)
    assert vp.stat.is_directory
    before = _snapshot(outside)
    d.rmdir()
    d.symlink_to(outside, target_is_directory=True)
    await run_task(lambda ctx: erase_files(ctx, [vp]))
    assert not d.is_symlink()
    assert _snapshot(outside) == before


@pytest.mark.asyncio
async def test_erase_regular_tree_unchanged(link_fs: Filesystem, tmp_path: Path) -> None:
    tree = tmp_path / "tree"
    (tree / "a" / "b").mkdir(parents=True)
    (tree / "a" / "b" / "f.txt").write_text("x")
    (tree / "g.txt").write_text("g")
    requests = await run_task(lambda ctx: erase_files(ctx, [link_fs.path(tree)]), [Response.YES])
    assert len(requests) == 1
    assert not tree.exists()


@pytest.mark.asyncio
async def test_erase_path_vanished_after_selection_completes(link_fs: Filesystem, tmp_path: Path) -> None:
    gone = tmp_path / "gone.txt"
    gone.write_text("x")
    other = tmp_path / "other.txt"
    other.write_text("y")
    selected = [link_fs.path(gone), link_fs.path(other)]
    gone.unlink()
    await run_task(lambda ctx: erase_files(ctx, selected))
    assert not other.exists()


async def _erase_changing_on_question(paths: list[VPath], change: Callable[[], None]) -> list[ResponseRequest]:
    """Erase *paths*, running *change* while the recursive-delete question is open, then answer yes."""
    requests: list[ResponseRequest] = []

    async def answer(request: ResponseRequest, future: asyncio.Future[Response]) -> None:
        requests.append(request)
        change()
        future.set_result(Response.YES)

    await AsyncTaskScheduler.execute(answer, lambda ctx: erase_files(ctx, paths), make_status())
    return requests


def _change_after_first_removal(monkeypatch: pytest.MonkeyPatch, filesystem: Filesystem, name: str, change: Callable[[], None]) -> None:
    """List entries by name and run *change* right after the entry called *name* is removed."""
    original_iterdir = filesystem.iterdir
    original_remove = filesystem.remove

    async def sorted_iterdir(
        path: VPath,
        *,
        cancel: threading.Event | None = None,
    ) -> AsyncIterator[VPath]:
        for child in sorted([c async for c in original_iterdir(path, cancel=cancel)], key=lambda c: c.name):
            yield child

    def remove_then_change(path: VPath) -> None:
        original_remove(path)
        if path.name == name:
            change()

    monkeypatch.setattr(filesystem, "iterdir", sorted_iterdir)
    monkeypatch.setattr(filesystem, "remove", remove_then_change)


def _swap_for_link(directory: Path, target: Path) -> None:
    shutil.rmtree(directory)
    directory.symlink_to(target, target_is_directory=True)


@pytest.mark.asyncio
async def test_subfolder_swapped_for_link_while_question_open(link_fs: Filesystem, tmp_path: Path) -> None:
    outside = _outside(tmp_path)
    tree = tmp_path / "tree"
    (tree / "folder").mkdir(parents=True)
    (tree / "folder" / "x.txt").write_text("x")
    before = _snapshot(outside)
    try:
        requests = await _erase_changing_on_question([link_fs.path(tree)], lambda: _swap_for_link(tree / "folder", outside))
    finally:
        assert _snapshot(outside) == before
    assert len(requests) == 1
    assert not tree.exists()


@pytest.mark.asyncio
async def test_selected_dir_swapped_for_link_while_question_open(link_fs: Filesystem, tmp_path: Path) -> None:
    outside = _outside(tmp_path)
    tree = tmp_path / "tree"
    (tree / "sub").mkdir(parents=True)
    (tree / "sub" / "deep.txt").write_text("x")
    (tree / "keep.txt").write_text("x")
    before = _snapshot(outside)
    try:
        await _erase_changing_on_question([link_fs.path(tree)], lambda: _swap_for_link(tree, outside))
    finally:
        assert _snapshot(outside) == before
    assert not tree.is_symlink()
    assert not tree.exists()


@pytest.mark.asyncio
async def test_subfolder_swapped_for_link_while_earlier_entries_are_deleted(link_fs: Filesystem, tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    outside = _outside(tmp_path)
    tree = tmp_path / "tree"
    (tree / "z_folder").mkdir(parents=True)
    (tree / "z_folder" / "x.txt").write_text("x")
    (tree / "a.txt").write_text("a")
    before = _snapshot(outside)
    _change_after_first_removal(monkeypatch, link_fs, "a.txt", lambda: _swap_for_link(tree / "z_folder", outside))
    try:
        await run_task(lambda ctx: erase_files(ctx, [link_fs.path(tree)]), [Response.YES])
    finally:
        assert _snapshot(outside) == before
    assert not tree.exists()


@pytest.mark.asyncio
async def test_entries_vanished_while_question_open(link_fs: Filesystem, tmp_path: Path) -> None:
    tree = tmp_path / "tree"
    (tree / "folder").mkdir(parents=True)
    (tree / "folder" / "x.txt").write_text("x")
    (tree / "f.txt").write_text("f")
    (tree / "g.txt").write_text("g")

    def vanish() -> None:
        shutil.rmtree(tree / "folder")
        (tree / "f.txt").unlink()

    await _erase_changing_on_question([link_fs.path(tree)], vanish)
    assert not tree.exists()


@pytest.mark.asyncio
async def test_selected_dir_vanished_while_question_open(link_fs: Filesystem, tmp_path: Path) -> None:
    tree = tmp_path / "tree"
    tree.mkdir()
    (tree / "f.txt").write_text("f")
    other = tmp_path / "other.txt"
    other.write_text("o")
    await _erase_changing_on_question([link_fs.path(tree), link_fs.path(other)], lambda: shutil.rmtree(tree))
    assert not tree.exists()
    assert not other.exists()


@pytest.mark.asyncio
async def test_subfolder_vanished_while_earlier_entries_are_deleted(link_fs: Filesystem, tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    tree = tmp_path / "tree"
    (tree / "z_folder").mkdir(parents=True)
    (tree / "z_folder" / "x.txt").write_text("x")
    (tree / "a.txt").write_text("a")
    _change_after_first_removal(monkeypatch, link_fs, "a.txt", lambda: shutil.rmtree(tree / "z_folder"))
    await run_task(lambda ctx: erase_files(ctx, [link_fs.path(tree)]), [Response.YES])
    assert not tree.exists()


@pytest.mark.asyncio
async def test_not_found_error_for_an_existing_entry_is_reported(link_fs: Filesystem, tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    """SFTP reports e.g. "not a directory" as "no such file"; that must not pass as vanished."""
    empty = tmp_path / "empty"
    empty.mkdir()

    def failing_rmdir(path: VPath) -> None:
        raise FileNotFoundError(path.path)

    monkeypatch.setattr(link_fs, "rmdir", failing_rmdir)
    with pytest.raises(FileNotFoundError):
        await run_task(lambda ctx: erase_files(ctx, [link_fs.path(empty)]))
    assert empty.is_dir()
