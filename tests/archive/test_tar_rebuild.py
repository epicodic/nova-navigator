"""Tests for streaming TAR member replacement."""

from __future__ import annotations

import io
import tarfile
from pathlib import Path

import pytest

from nova_navigator.archive.tar_rebuild import _tar_write_mode, rebuild_tar


def _make_tar(source: Path) -> None:
    with tarfile.open(source, _tar_write_mode(source), format=tarfile.PAX_FORMAT) as archive:
        keep = tarfile.TarInfo("keep.txt")
        keep_content = b"keep me"
        keep.size = len(keep_content)
        keep.mode = 0o640
        keep.pax_headers = {"comment": "x"}
        archive.addfile(keep, io.BytesIO(keep_content))

        edit = tarfile.TarInfo("dir/edit.txt")
        edit_content = b"old content"
        edit.size = len(edit_content)
        archive.addfile(edit, io.BytesIO(edit_content))

        link = tarfile.TarInfo("link")
        link.type = tarfile.SYMTYPE
        link.linkname = "keep.txt"
        archive.addfile(link)


def _read(archive: tarfile.TarFile, name: str) -> bytes:
    member = archive.extractfile(name)
    assert member is not None
    with member:
        return member.read()


@pytest.mark.parametrize("suffix", [".tar", ".tar.gz", ".tar.bz2", ".tar.xz"])
def test_replaces_member_and_preserves_metadata(tmp_path: Path, suffix: str) -> None:
    source = tmp_path / f"a{suffix}"
    _make_tar(source)
    replacement = tmp_path / "new.txt"
    replacement.write_bytes(b"new content")
    output = tmp_path / f"out{suffix}"
    rebuild_tar(source, "dir/edit.txt", replacement, output)
    with tarfile.open(output, "r:*") as archive:
        edit = archive.getmember("dir/edit.txt")
        assert _read(archive, edit.name) == b"new content"
        keep = archive.getmember("keep.txt")
        assert keep.mode == 0o640
        assert keep.pax_headers.get("comment") == "x"
        assert _read(archive, "keep.txt") == b"keep me"
        link = archive.getmember("link")
        assert link.issym()
        assert link.linkname == "keep.txt"


@pytest.mark.parametrize("suffix", [".tar", ".tar.gz", ".tar.bz2", ".tar.xz"])
def test_replaces_member_updates_pax_size_header(tmp_path: Path, suffix: str) -> None:
    source = tmp_path / f"a{suffix}"
    with tarfile.open(source, _tar_write_mode(source), format=tarfile.PAX_FORMAT) as archive:
        edit = tarfile.TarInfo("edit.txt")
        content = b"old"
        edit.size = len(content)
        edit.pax_headers = {"size": str(len(content))}
        archive.addfile(edit, io.BytesIO(content))
    replacement = tmp_path / "new.txt"
    replacement.write_bytes(b"much longer replacement content")
    output = tmp_path / f"out{suffix}"
    rebuild_tar(source, "edit.txt", replacement, output)
    with tarfile.open(output, "r:*") as archive:
        info = archive.getmember("edit.txt")
        assert info.size == len(b"much longer replacement content")
        assert info.pax_headers.get("size") == str(info.size)
        assert _read(archive, "edit.txt") == b"much longer replacement content"


def test_appends_new_member(tmp_path: Path) -> None:
    source = tmp_path / "a.tar"
    _make_tar(source)
    replacement = tmp_path / "new.txt"
    replacement.write_bytes(b"brand new")
    output = tmp_path / "out.tar"
    rebuild_tar(source, "dir/new.txt", replacement, output)
    with tarfile.open(output, "r:*") as archive:
        assert _read(archive, "dir/new.txt") == b"brand new"
        added = archive.getmember("dir/new.txt")
        assert added.mode == 0o644
        assert added.type == tarfile.REGTYPE
        assert len(archive.getmembers()) == 4


def test_rejects_duplicate_member(tmp_path: Path) -> None:
    source = tmp_path / "a.tar"
    with tarfile.open(source, "w:") as archive:
        for _ in range(2):
            info = tarfile.TarInfo("edit.txt")
            content = b"one"
            info.size = len(content)
            archive.addfile(info, io.BytesIO(content))
    replacement = tmp_path / "new.txt"
    replacement.write_bytes(b"x")
    output = tmp_path / "out.tar"
    with pytest.raises(ValueError, match="Ambiguous"):
        rebuild_tar(source, "edit.txt", replacement, output)
    assert not output.exists()


def test_rejects_sparse_member(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    source = tmp_path / "a.tar"
    _make_tar(source)
    replacement = tmp_path / "new.txt"
    replacement.write_bytes(b"x")
    output = tmp_path / "out.tar"
    monkeypatch.setattr(tarfile.TarInfo, "issparse", lambda self: self.name == "keep.txt")
    with pytest.raises(ValueError, match="sparse"):
        rebuild_tar(source, "dir/edit.txt", replacement, output)
    assert not output.exists()


def test_rebuild_tar_does_not_delete_preexisting_output(tmp_path: Path) -> None:
    source = tmp_path / "a.tar"
    _make_tar(source)
    replacement = tmp_path / "new.txt"
    replacement.write_bytes(b"new content")
    output = tmp_path / "out.tar"
    output.write_bytes(b"unrelated pre-existing content")
    with pytest.raises(FileExistsError):
        rebuild_tar(source, "dir/edit.txt", replacement, output)
    assert output.read_bytes() == b"unrelated pre-existing content"


def test_matches_dot_slash_prefixed_member(tmp_path: Path) -> None:
    source = tmp_path / "a.tar"
    with tarfile.open(source, "w:") as archive:
        info = tarfile.TarInfo("./dir/edit.txt")
        content = b"old content"
        info.size = len(content)
        archive.addfile(info, io.BytesIO(content))
    replacement = tmp_path / "new.txt"
    replacement.write_bytes(b"new content")
    output = tmp_path / "out.tar"
    rebuild_tar(source, "dir/edit.txt", replacement, output)
    with tarfile.open(output, "r:*") as archive:
        assert _read(archive, "./dir/edit.txt") == b"new content"
        assert len(archive.getmembers()) == 1
