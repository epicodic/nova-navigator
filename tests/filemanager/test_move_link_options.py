"""Move with the follow and stable link options.

Cross-device moves copy links (follow copies the target, stable rewrites outside relative text).
Same-device moves rename links unchanged, whatever the link options say.
"""

import os
from pathlib import Path

import pytest

from nova_navigator.filemanager.tasks import FileCopyOptions
from nova_navigator.vfs.filesystem import Filesystem
from nova_navigator.vfs.filesystems.local import LocalFilesystem

from .common import force_cross_device, move_between, reasons

_FOLLOW = FileCopyOptions(follow_links=True)
_FOLLOW_STABLE = FileCopyOptions(follow_links=True, stable_symlinks=True)


def _write_tree(root: Path, rel: str, text: str) -> Path:
    path = root / rel
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(text)
    return path


def _file_set(root: Path) -> list[str]:
    return sorted(str(p.relative_to(root)) for p in root.rglob("*"))


async def _scenario_file_link(pair: tuple[Filesystem, Filesystem], base: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    force_cross_device(monkeypatch)
    outside = base / "outside"
    _write_tree(outside, "t.txt", "t")
    src = base / "src"
    src.mkdir()
    (src / "l").symlink_to("../outside/t.txt")
    dst = base / "dst"
    dst.mkdir()
    await move_between(pair, [src / "l"], dst, _FOLLOW)
    assert (dst / "l").is_file()
    assert not (dst / "l").is_symlink()
    assert (dst / "l").read_text() == "t"
    assert not os.path.lexists(src / "l")
    assert (outside / "t.txt").read_text() == "t"


async def _scenario_dir_link(pair: tuple[Filesystem, Filesystem], base: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    force_cross_device(monkeypatch)
    outside = base / "outside" / "tree"
    _write_tree(outside, "a.txt", "a")
    _write_tree(outside, "b/c.txt", "c")
    before = _file_set(outside)
    src = base / "src"
    (src / "d").mkdir(parents=True)
    (src / "d" / "ld").symlink_to("../../outside/tree", target_is_directory=True)
    dst = base / "dst"
    dst.mkdir()
    await move_between(pair, [src / "d"], dst, _FOLLOW)
    assert (dst / "d" / "ld" / "a.txt").read_text() == "a"
    assert (dst / "d" / "ld" / "b" / "c.txt").read_text() == "c"
    assert not (dst / "d" / "ld").is_symlink()
    assert not os.path.exists(src / "d")
    assert _file_set(outside) == before


@pytest.mark.asyncio
async def test_move_follow_file_link_copies_content_removes_link_keeps_target(
    fs_pair: tuple[Filesystem, Filesystem],
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    await _scenario_file_link(fs_pair, tmp_path, monkeypatch)


@pytest.mark.asyncio
async def test_move_follow_dir_link_copies_tree_removes_link_keeps_target(
    fs_pair: tuple[Filesystem, Filesystem],
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    await _scenario_dir_link(fs_pair, tmp_path, monkeypatch)


@pytest.mark.asyncio
async def test_move_follow_broken_link_stays_with_parent_folder(
    fs_pair: tuple[Filesystem, Filesystem],
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    force_cross_device(monkeypatch)
    src = tmp_path / "src"
    _write_tree(src, "d/f.txt", "f")
    (src / "d" / "br").symlink_to("nope")
    dst = tmp_path / "dst"
    dst.mkdir()
    status = await move_between(fs_pair, [src / "d"], dst, _FOLLOW)
    assert (dst / "d" / "f.txt").exists()
    assert not (src / "d" / "f.txt").exists()
    assert os.path.islink(src / "d" / "br")
    assert (src / "d").exists()
    assert not os.path.lexists(dst / "d" / "br")
    assert reasons(status) == ["broken link"]


@pytest.mark.asyncio
async def test_move_follow_loop_link_stays_with_parent_folder(
    fs_pair: tuple[Filesystem, Filesystem],
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    force_cross_device(monkeypatch)
    src = tmp_path / "src"
    _write_tree(src, "d/f.txt", "f")
    (src / "d" / "self").symlink_to(".")
    dst = tmp_path / "dst"
    dst.mkdir()
    status = await move_between(fs_pair, [src / "d"], dst, _FOLLOW)
    assert (dst / "d" / "f.txt").exists()
    assert not (src / "d" / "f.txt").exists()
    assert os.path.islink(src / "d" / "self")
    assert (src / "d").exists()
    assert not os.path.lexists(dst / "d" / "self")
    assert reasons(status) == ["link loop"]


@pytest.mark.asyncio
async def test_move_follow_declined_overwrite_keeps_link(
    fs_pair: tuple[Filesystem, Filesystem],
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    force_cross_device(monkeypatch)
    src = tmp_path / "src"
    _write_tree(src, "t.txt", "new")
    (src / "l").symlink_to("t.txt")
    dst = tmp_path / "dst"
    _write_tree(dst, "l", "old")
    await move_between(fs_pair, [src / "l"], dst, FileCopyOptions(follow_links=True, overwrite="skip"))
    assert (dst / "l").read_text() == "old"
    assert os.path.islink(src / "l")


@pytest.mark.asyncio
async def test_move_follow_target_moved_earlier_in_same_move_is_skipped_not_failed(
    fs_pair: tuple[Filesystem, Filesystem],
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    # DEC-7a: files of a folder are moved before its links; the link's stat was listed earlier and still says
    # "file link", so the copy fails with FileNotFoundError, which _follow_link turns into a broken-link skip.
    force_cross_device(monkeypatch)
    src = tmp_path / "src"
    _write_tree(src, "a/t.txt", "t")
    (src / "a" / "l").symlink_to("t.txt")
    dst = tmp_path / "dst"
    dst.mkdir()
    status = await move_between(fs_pair, [src / "a"], dst, _FOLLOW)
    assert (dst / "a" / "t.txt").read_text() == "t"
    assert os.path.islink(src / "a" / "l")
    assert (src / "a").exists()
    assert not os.path.lexists(dst / "a" / "l")
    assert reasons(status) == ["broken link"]


@pytest.mark.asyncio
async def test_move_same_device_ignores_follow_and_stable(tmp_path: Path) -> None:
    local = LocalFilesystem()
    # Case A: destination folder exists, the moved folder does not: whole-folder rename.
    src = tmp_path / "src"
    _write_tree(src, "f.txt", "f")
    (src / "l").symlink_to("f.txt")
    (src / "d").mkdir()
    (src / "d" / "ld").symlink_to("..")
    dst = tmp_path / "dst"
    dst.mkdir()
    await move_between((local, local), [src], dst, _FOLLOW_STABLE)
    assert os.readlink(dst / "src" / "l") == "f.txt"
    assert os.readlink(dst / "src" / "d" / "ld") == ".."
    assert os.path.islink(dst / "src" / "l")
    assert os.path.islink(dst / "src" / "d" / "ld")

    # Case B: the destination folder already exists: the move walks the contents and renames each entry.
    src2 = tmp_path / "src2"
    _write_tree(src2, "f.txt", "f")
    (src2 / "l").symlink_to("f.txt")
    (src2 / "d").mkdir()
    (src2 / "d" / "ld").symlink_to("..")
    dst2 = tmp_path / "dst2"
    (dst2 / "src2").mkdir(parents=True)
    await move_between((local, local), [src2], dst2, _FOLLOW_STABLE)
    assert os.readlink(dst2 / "src2" / "l") == "f.txt"
    assert os.readlink(dst2 / "src2" / "d" / "ld") == ".."
    assert os.path.islink(dst2 / "src2" / "l")
    assert os.path.islink(dst2 / "src2" / "d" / "ld")


@pytest.mark.asyncio
async def test_move_same_device_relative_link_inside_renamed_folder_unchanged_text(tmp_path: Path) -> None:
    local = LocalFilesystem()
    _write_tree(tmp_path, "shared/x", "x")
    proj = tmp_path / "proj"
    proj.mkdir()
    (proj / "l").symlink_to("../shared/x")
    dst2 = tmp_path / "dst2"
    dst2.mkdir()
    await move_between((local, local), [proj], dst2, _FOLLOW_STABLE)
    assert os.readlink(dst2 / "proj" / "l") == "../shared/x"
    assert not (dst2 / "proj" / "l").exists()


@pytest.mark.asyncio
async def test_move_same_device_stable_selected_link_text_unchanged(tmp_path: Path) -> None:
    local = LocalFilesystem()
    _write_tree(tmp_path, "shared/x", "x")
    proj = tmp_path / "proj"
    proj.mkdir()
    (proj / "l").symlink_to("../shared/x")
    dst = tmp_path / "dst"
    dst.mkdir()
    status = await move_between((local, local), [proj / "l"], dst, FileCopyOptions(stable_symlinks=True))
    assert os.readlink(dst / "l") == "../shared/x"
    assert not os.path.lexists(proj / "l")
    assert reasons(status) == []
