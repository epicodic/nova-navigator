from __future__ import annotations

import shutil
from pathlib import Path

from nova_navigator.usermenu.context import CONTEXT_NAMES, DirInfo, FileInfo, PanelSnapshot, Probe, build_context
from nova_navigator.vfs.filesystems import LocalFilesystem
from tests._utils.mock_filesystem import MockFilesystem

_fs = LocalFilesystem.singleton()


def _snapshot(tmp_path: Path, cursor: str | None = None, selection: tuple[str, ...] = ()) -> PanelSnapshot:
    return PanelSnapshot(
        dir=_fs.path(tmp_path),
        cursor=_fs.path(tmp_path / cursor) if cursor else None,
        selection=tuple(_fs.path(tmp_path / name) for name in selection),
        listing=frozenset(p.name for p in tmp_path.iterdir()),
    )


def test_file_info_name_parts(tmp_path: Path) -> None:
    (tmp_path / "a.tar.gz").write_bytes(b"12345")
    info = FileInfo(_fs.path(tmp_path / "a.tar.gz"))
    assert info.name == "a.tar.gz"
    assert info.stem == "a.tar"
    assert info.ext == "gz"
    assert info.path == str(tmp_path / "a.tar.gz")
    assert str(info) == info.path
    assert info.size == 5
    assert info.is_file is True
    assert info.is_dir is False
    assert info.mimetype == "application/x-tar"


def test_file_info_matches_and_re(tmp_path: Path) -> None:
    (tmp_path / "Photo.JPG").write_bytes(b"")
    info = FileInfo(_fs.path(tmp_path / "Photo.JPG"))
    assert info.matches("*.png", "*.JPG") is True
    assert info.matches("*.jpg") is False
    assert info.matches("*.jpg", ignore_case=True) is True
    assert info.re(r"^Pho") is True


def test_dir_info_has_uses_listing_only(tmp_path: Path) -> None:
    (tmp_path / "Makefile").write_text("")
    info = DirInfo(_fs.path(tmp_path), frozenset({"Makefile"}), Probe(_fs.path(tmp_path)))
    assert info.has("Makefile") is True
    assert info.has("missing") is False
    assert info.scheme == "local"
    assert info.is_local is True
    assert info.host == ""


def test_dir_info_find_up(tmp_path: Path) -> None:
    (tmp_path / ".git").mkdir()
    sub = tmp_path / "a" / "b"
    sub.mkdir(parents=True)
    info = DirInfo(_fs.path(sub), frozenset(), Probe(_fs.path(sub)))
    found = info.find_up(".git")
    assert found is not None
    assert found.path == str(tmp_path)
    assert info.find_up("no-such-marker-xyz") is None


def test_probe_which_local(tmp_path: Path) -> None:
    probe = Probe(_fs.path(tmp_path))
    assert probe.which("sh") is (shutil.which("sh") is not None)
    assert probe.which("no-such-command-xyz") is False


def test_probe_which_without_commands_is_false() -> None:
    mock = MockFilesystem()
    assert Probe(mock.path("/")).which("sh") is False


def test_build_context_targets_fall_back_to_cursor(tmp_path: Path) -> None:
    (tmp_path / "a.txt").write_text("")
    (tmp_path / "b.txt").write_text("")
    names = build_context(_snapshot(tmp_path, cursor="a.txt"), _snapshot(tmp_path))
    assert set(names) == CONTEXT_NAMES
    targets = names["targets"]
    assert isinstance(targets, list)
    assert [t.name if isinstance(t, FileInfo) else t for t in targets] == ["a.txt"]
    assert names["selection"] == []


def test_build_context_targets_prefer_selection(tmp_path: Path) -> None:
    (tmp_path / "a.txt").write_text("")
    (tmp_path / "b.txt").write_text("")
    names = build_context(_snapshot(tmp_path, cursor="a.txt", selection=("b.txt",)), _snapshot(tmp_path))
    targets = names["targets"]
    assert isinstance(targets, list)
    assert [t.name if isinstance(t, FileInfo) else t for t in targets] == ["b.txt"]


def test_build_context_without_cursor(tmp_path: Path) -> None:
    names = build_context(_snapshot(tmp_path), _snapshot(tmp_path))
    assert names["file"] is None
    assert names["targets"] == []
