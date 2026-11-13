"""Stable symlinks (ADR-1): a copied relative link whose target lies outside the copied item is rewritten.

The rewrite happens only when the link's source and destination share one filesystem object.
Follow links beats the rule, and the rule never runs for other options or destinations.
"""

import logging
import os
from pathlib import Path

import pytest

import nova_navigator.filemanager.tasks as tasks_module
from nova_navigator.filemanager.tasks import FileCopyOptions, _stable_text
from nova_navigator.vfs.filesystem import Filesystem
from nova_navigator.vfs.filesystems.local import LocalFilesystem
from nova_navigator.vfs.filesystems.remote import RemoteFilesystem
from nova_navigator.vfs.vpath import VPath
from tests._utils.mock_filesystem import MockFilesystem

from .common import copy_between, force_cross_device, move_between

_STABLE = FileCopyOptions(stable_symlinks=True)


def _layout(tmp_path: Path) -> tuple[Path, Path, Path]:
    """a/b/proj/data.txt, a/b/proj/doc/{l -> ../../shared/x, inside -> ../data.txt, ab -> <abs shared/x>, br -> ../../shared/missing};
    a/b/shared/x='sx'; c/ empty. Returns (proj, shared_x, c)."""
    proj = tmp_path / "a" / "b" / "proj"
    doc = proj / "doc"
    doc.mkdir(parents=True)
    (proj / "data.txt").write_text("data")
    shared_x = tmp_path / "a" / "b" / "shared" / "x"
    shared_x.parent.mkdir(parents=True)
    shared_x.write_text("sx")
    (doc / "l").symlink_to("../../shared/x")
    (doc / "inside").symlink_to("../data.txt")
    (doc / "ab").symlink_to(shared_x)
    (doc / "br").symlink_to("../../shared/missing")
    c = tmp_path / "c"
    c.mkdir()
    return proj, shared_x, c


def _boom(*_args: object, **_kwargs: object) -> str:
    raise AssertionError("stable_link_target must not be called")


@pytest.mark.asyncio
async def test_stable_copy_req4_example_on_disk(link_fs: Filesystem, tmp_path: Path) -> None:
    proj, shared_x, c = _layout(tmp_path)
    status = await copy_between((link_fs, link_fs), [proj], c, _STABLE)
    copied = c / "proj" / "doc" / "l"
    assert os.readlink(copied) == os.path.relpath(shared_x, c / "proj" / "doc")
    assert copied.read_text() == "sx"
    assert status.progress.completed == status.progress.total


@pytest.mark.asyncio
async def test_stable_inside_link_copied_unchanged(link_fs: Filesystem, tmp_path: Path) -> None:
    proj, _shared_x, c = _layout(tmp_path)
    await copy_between((link_fs, link_fs), [proj], c, _STABLE)
    assert os.readlink(c / "proj" / "doc" / "inside") == "../data.txt"


@pytest.mark.asyncio
async def test_stable_absolute_link_unchanged(link_fs: Filesystem, tmp_path: Path) -> None:
    proj, shared_x, c = _layout(tmp_path)
    await copy_between((link_fs, link_fs), [proj], c, _STABLE)
    assert os.readlink(c / "proj" / "doc" / "ab") == str(shared_x)


@pytest.mark.asyncio
async def test_stable_selected_relative_link_rewritten(link_fs: Filesystem, tmp_path: Path) -> None:
    _proj, shared_x, c = _layout(tmp_path)
    status = await copy_between((link_fs, link_fs), [_proj / "doc" / "l"], c, _STABLE)
    assert os.readlink(c / "l") == os.path.relpath(shared_x, c)
    assert (c / "l").read_text() == "sx"
    assert status.progress.completed == status.progress.total


@pytest.mark.asyncio
async def test_stable_selected_self_referencing_text_still_rewritten(link_fs: Filesystem, tmp_path: Path) -> None:
    d = tmp_path / "d"
    d.mkdir()
    (d / "l").symlink_to("l")
    c = tmp_path / "c"
    c.mkdir()
    status = await copy_between((link_fs, link_fs), [d / "l"], c, _STABLE)
    assert os.readlink(c / "l") == os.path.relpath(d / "l", c)
    assert status.progress.completed == status.progress.total


@pytest.mark.asyncio
async def test_stable_broken_link_stays_broken_at_same_target_path(link_fs: Filesystem, tmp_path: Path) -> None:
    proj, _shared_x, c = _layout(tmp_path)
    status = await copy_between((link_fs, link_fs), [proj], c, _STABLE)
    br = c / "proj" / "doc" / "br"
    target = os.path.normpath(os.path.join(c / "proj" / "doc", os.readlink(br)))
    assert target == str(tmp_path / "a" / "b" / "shared" / "missing")
    assert not br.exists()
    assert status.progress.completed == status.progress.total


@pytest.mark.asyncio
async def test_stable_different_filesystems_text_unchanged(fs_pair: tuple[Filesystem, Filesystem], tmp_path: Path) -> None:
    proj, shared_x, c = _layout(tmp_path)
    status = await copy_between(fs_pair, [proj], c, _STABLE)
    if fs_pair[0] == fs_pair[1]:
        expected = os.path.relpath(shared_x, c / "proj" / "doc")
    else:
        expected = "../../shared/x"
    assert os.readlink(c / "proj" / "doc" / "l") == expected
    assert status.progress.completed == status.progress.total


@pytest.mark.asyncio
async def test_stable_between_two_wrappers_of_one_filesystem_rewrites(tmp_path: Path) -> None:
    local = LocalFilesystem.singleton()
    pair: tuple[Filesystem, Filesystem] = (RemoteFilesystem("a", local), RemoteFilesystem("b", local))
    proj, shared_x, c = _layout(tmp_path)
    status = await copy_between(pair, [proj], c, _STABLE)
    assert os.readlink(c / "proj" / "doc" / "l") == os.path.relpath(shared_x, c / "proj" / "doc")
    assert status.progress.completed == status.progress.total


@pytest.mark.asyncio
async def test_stable_off_text_unchanged(link_fs: Filesystem, tmp_path: Path) -> None:
    proj, _shared_x, c = _layout(tmp_path)
    status = await copy_between((link_fs, link_fs), [proj], c)
    assert os.readlink(c / "proj" / "doc" / "l") == "../../shared/x"
    assert status.progress.completed == status.progress.total


@pytest.mark.asyncio
async def test_follow_beats_stable_same_result(link_fs: Filesystem, tmp_path: Path) -> None:
    proj, _shared_x, c1 = _layout(tmp_path)
    c2 = tmp_path / "c2"
    c2.mkdir()
    follow = FileCopyOptions(follow_links=True)
    follow_stable = FileCopyOptions(follow_links=True, stable_symlinks=True)
    await copy_between((link_fs, link_fs), [proj], c1, follow)
    status = await copy_between((link_fs, link_fs), [proj], c2, follow_stable)
    for root in (c1, c2):
        copied = root / "proj" / "doc" / "l"
        assert copied.is_file()
        assert not copied.is_symlink()
        assert copied.read_text() == "sx"
    assert sorted(p.relative_to(c1) for p in c1.rglob("*")) == sorted(p.relative_to(c2) for p in c2.rglob("*"))
    assert status.progress.completed == status.progress.total


@pytest.mark.asyncio
async def test_follow_on_never_calls_rule(link_fs: Filesystem, tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    proj, _shared_x, c = _layout(tmp_path)
    monkeypatch.setattr(tasks_module, "stable_link_target", _boom)
    status = await copy_between((link_fs, link_fs), [proj], c, FileCopyOptions(follow_links=True, stable_symlinks=True))
    assert (c / "proj" / "doc" / "l").is_file()
    assert status.progress.completed == status.progress.total


@pytest.mark.asyncio
async def test_stable_off_never_calls_rule(link_fs: Filesystem, tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    proj, _shared_x, c = _layout(tmp_path)
    monkeypatch.setattr(tasks_module, "stable_link_target", _boom)
    status = await copy_between((link_fs, link_fs), [proj], c)
    assert os.readlink(c / "proj" / "doc" / "l") == "../../shared/x"
    assert status.progress.completed == status.progress.total


def test_stable_text_keeps_text_when_paths_not_absolute(caplog: pytest.LogCaptureFixture) -> None:
    fs = MockFilesystem({})
    with caplog.at_level(logging.WARNING):
        result = _stable_text(VPath("rel/l", fs), VPath("x/l", fs), "t", None)
    assert result == "t"
    assert any(record.levelno == logging.WARNING for record in caplog.records)


@pytest.mark.asyncio
async def test_move_cross_device_stable_rewrites_outside_link_and_removes_source(link_fs: Filesystem, tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    force_cross_device(monkeypatch)
    proj, shared_x, c = _layout(tmp_path)
    status = await move_between((link_fs, link_fs), [proj], c, _STABLE)
    copied = c / "proj" / "doc"
    assert os.readlink(copied / "l") == os.path.relpath(shared_x, copied)
    assert os.readlink(copied / "inside") == "../data.txt"
    assert not proj.exists()
    assert shared_x.read_text() == "sx"
    assert status.progress.completed == status.progress.total


@pytest.mark.asyncio
async def test_move_cross_device_stable_selected_link_rewritten(link_fs: Filesystem, tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    force_cross_device(monkeypatch)
    proj, shared_x, c = _layout(tmp_path)
    status = await move_between((link_fs, link_fs), [proj / "doc" / "l"], c, _STABLE)
    assert os.readlink(c / "l") == os.path.relpath(shared_x, c)
    assert not os.path.lexists(proj / "doc" / "l")
    assert status.progress.completed == status.progress.total


@pytest.mark.asyncio
async def test_move_cross_device_stable_different_filesystems_unchanged(fs_pair: tuple[Filesystem, Filesystem], tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    force_cross_device(monkeypatch)
    proj, _shared_x, c = _layout(tmp_path)
    status = await move_between(fs_pair, [proj], c, _STABLE)
    if fs_pair[0] != fs_pair[1]:
        assert os.readlink(c / "proj" / "doc" / "l") == "../../shared/x"
    assert status.progress.completed == status.progress.total
