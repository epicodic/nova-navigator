"""Tests for cp command."""

from __future__ import annotations

from pathlib import Path

import pytest

from nova_navigator.terminal.vfs_shell.interpreter import VfsShellInterpreter
from nova_navigator.vfs.filesystems.local import LocalFilesystem
from tests._utils.mock_filesystem import MockFilesystem


@pytest.fixture
def fs() -> MockFilesystem:
    return MockFilesystem(
        {
            "/home/user/src.txt": b"source content",
            "/home/user/dir/a.txt": b"a content",
            "/home/user/dest": None,
        }
    )


@pytest.mark.asyncio
async def test_cp_file(fs: MockFilesystem) -> None:
    interp = VfsShellInterpreter(fs, fs.cwd(), cols=80, rows=24)
    output: list[str] = []
    exit_code = await interp.execute("cp src.txt copy.txt", output.append, output.append)
    assert exit_code == 0
    assert fs.exists("/home/user/copy.txt")


@pytest.mark.asyncio
async def test_cp_recursive_directory(fs: MockFilesystem) -> None:
    interp = VfsShellInterpreter(fs, fs.cwd(), cols=80, rows=24)
    output: list[str] = []
    exit_code = await interp.execute("cp -r dir dest/dir_copy", output.append, output.append)
    assert exit_code == 0
    assert fs.exists("/home/user/dest/dir_copy")
    assert fs.exists("/home/user/dest/dir_copy/a.txt")


@pytest.mark.asyncio
async def test_cp_directory_without_r_fails(fs: MockFilesystem) -> None:
    interp = VfsShellInterpreter(fs, fs.cwd(), cols=80, rows=24)
    output: list[str] = []
    exit_code = await interp.execute("cp dir dest/copy", output.append, output.append)
    assert exit_code != 0


@pytest.mark.asyncio
async def test_cp_r_recreates_dir_link_and_does_not_descend() -> None:
    fs = MockFilesystem(
        {"/outside/keep.txt": b"keep", "/home/user/src/f.txt": b"f"},
        links={"/home/user/src/out": "/outside", "/home/user/src/up": ".."},
    )
    interp = VfsShellInterpreter(fs, fs.cwd(), cols=80, rows=24)
    output: list[str] = []
    assert await interp.execute("cp -r src dst", output.append, output.append) == 0
    assert fs.readlink(fs.path("/home/user/dst/out")) == "/outside"
    assert fs.readlink(fs.path("/home/user/dst/up")) == ".."
    assert fs.exists("/home/user/dst/f.txt")
    assert fs.stat(fs.path("/home/user/dst/out")).is_symlink


@pytest.mark.asyncio
async def test_cp_r_on_filesystem_without_links_copies_file_link_and_skips_directory_link() -> None:
    fs = MockFilesystem(
        {"/home/user/src/f.txt": b"f", "/home/user/src/d/g.txt": b"g"},
        links={"/home/user/src/lf": "f.txt", "/home/user/src/ld": "d"},
        symlinks=False,
    )
    interp = VfsShellInterpreter(fs, fs.cwd(), cols=80, rows=24)
    output: list[str] = []
    exit_code = await interp.execute("cp -r src dst", output.append, output.append)
    assert exit_code != 0
    assert fs.read(fs.path("/home/user/dst/lf")).read(10) == b"f"
    assert not fs.exists("/home/user/dst/ld")
    assert fs.exists("/home/user/dst/d/g.txt")  # the rest of the tree is still copied
    assert any("skipping link" in text for text in output)


@pytest.mark.asyncio
async def test_cp_without_r_still_copies_link_to_file_as_content() -> None:
    fs = MockFilesystem({"/home/user/f.txt": b"content"}, links={"/home/user/l": "f.txt"})
    interp = VfsShellInterpreter(fs, fs.cwd(), cols=80, rows=24)
    output: list[str] = []
    assert await interp.execute("cp l copy", output.append, output.append) == 0
    assert fs.read(fs.path("/home/user/copy")).read(10) == b"content"


@pytest.mark.asyncio
async def test_cp_without_r_omits_directory_link() -> None:
    fs = MockFilesystem({"/home/user/d/g.txt": b"g"}, links={"/home/user/ld": "d"})
    interp = VfsShellInterpreter(fs, fs.cwd(), cols=80, rows=24)
    output: list[str] = []
    assert await interp.execute("cp ld copy", output.append, output.append) != 0
    assert any("omitting directory" in text for text in output)


@pytest.mark.asyncio
async def test_cp_r_on_local_filesystem_copies_link_only(tmp_path: Path) -> None:
    outside = tmp_path / "outside"
    outside.mkdir()
    (outside / "keep.txt").write_text("keep")
    (tmp_path / "src").mkdir()
    (tmp_path / "src" / "out").symlink_to(outside, target_is_directory=True)
    local = LocalFilesystem()
    interp = VfsShellInterpreter(local, local.path(tmp_path), cols=80, rows=24)
    output: list[str] = []
    assert await interp.execute("cp -r src dst", output.append, output.append) == 0
    assert (tmp_path / "dst" / "out").is_symlink()
    assert sorted(p.name for p in outside.iterdir()) == ["keep.txt"]


def _cp_tree_on_disk(root: Path) -> None:
    (root / "d" / "sub").mkdir(parents=True)
    (root / "d" / "f").write_bytes(b"data-f")
    (root / "link").symlink_to("d/f")
    (root / "hard").hardlink_to(root / "d" / "f")
    (root / "alias").symlink_to("d")


@pytest.mark.asyncio
@pytest.mark.parametrize("command", ["cp d/f d/f", "cp d/f d/./f", "cp d/f link", "cp link d/f", "cp d/f hard", "cp d/f alias/f"])
async def test_cp_onto_itself_is_refused(tmp_path: Path, command: str) -> None:
    _cp_tree_on_disk(tmp_path)
    local = LocalFilesystem()
    interp = VfsShellInterpreter(local, local.path(tmp_path), cols=80, rows=24)
    output: list[str] = []

    exit_code = await interp.execute(command, output.append, output.append)

    assert exit_code == 1
    assert "are the same file" in "".join(output)
    assert (tmp_path / "d" / "f").read_bytes() == b"data-f"


@pytest.mark.asyncio
@pytest.mark.parametrize("destination", ["d", "d/sub", "d/new", "alias"])
async def test_cp_r_into_itself_is_refused(tmp_path: Path, destination: str) -> None:
    _cp_tree_on_disk(tmp_path)
    local = LocalFilesystem()
    interp = VfsShellInterpreter(local, local.path(tmp_path), cols=80, rows=24)
    output: list[str] = []

    exit_code = await interp.execute(f"cp -r d {destination}", output.append, output.append)

    assert exit_code == 1
    assert "into itself" in "".join(output)
    assert (tmp_path / "d" / "f").read_bytes() == b"data-f"
    assert sorted(p.name for p in (tmp_path / "d").iterdir()) == ["f", "sub"]
    assert list((tmp_path / "d" / "sub").iterdir()) == []


@pytest.mark.asyncio
async def test_cp_through_a_chain_of_twenty_links_is_refused(tmp_path: Path) -> None:
    (tmp_path / "f").write_bytes(b"data-f")
    (tmp_path / "l0").symlink_to("f")
    for i in range(1, 20):
        (tmp_path / f"l{i}").symlink_to(f"l{i - 1}")
    local = LocalFilesystem()
    interp = VfsShellInterpreter(local, local.path(tmp_path), cols=80, rows=24)
    output: list[str] = []

    exit_code = await interp.execute("cp f l19", output.append, output.append)

    assert exit_code == 1
    assert "are the same file" in "".join(output)
    assert (tmp_path / "f").read_bytes() == b"data-f"
