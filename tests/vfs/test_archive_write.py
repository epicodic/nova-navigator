"""Tests for writing archive members through ArchiveFilesystem."""

import io
import tarfile
import time
import zipfile
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path

import pytest

from nova_navigator.archive.backing import CopiedArchiveBacking, LocalArchiveBacking
from nova_navigator.vfs.filesystems.archive import ArchiveFilesystem
from nova_navigator.vfs.filesystems.local import LocalFilesystem
from nova_navigator.vfs.local_copy import LocalCopy
from tests._utils.local_copy_helpers import SchemeFs, overwrite


def _mount(tmp_path: Path, name: str = "a.zip") -> tuple[Path, ArchiveFilesystem]:
    archive = tmp_path / name
    with zipfile.ZipFile(archive, "w") as z:
        z.writestr("dir/edit.txt", b"old")
        z.writestr("keep.txt", b"keep")
    local = LocalFilesystem.singleton()
    fs = ArchiveFilesystem(local.path(tmp_path), LocalArchiveBacking(local.path(archive)), work_dir=tmp_path / "work")
    return archive, fs


def _write(fs: ArchiveFilesystem, member: str, data: bytes) -> None:
    writer = fs.write(fs.path(member))
    writer.write(data)
    writer.close()


def test_write_replaces_member_in_local_archive(tmp_path: Path) -> None:
    archive, fs = _mount(tmp_path)
    _write(fs, "/dir/edit.txt", b"new")
    with zipfile.ZipFile(archive) as z:
        assert z.read("dir/edit.txt") == b"new"
        assert z.read("keep.txt") == b"keep"
    assert fs.read(fs.path("/dir/edit.txt")).read(10) == b"new"
    assert fs.stat(fs.path("/dir/edit.txt")).size == 3


def test_read_only_format_rejects_write(tmp_path: Path) -> None:
    _, fs = _mount(tmp_path, "a.jar")
    assert fs.capabilities.read_only
    with pytest.raises(PermissionError):
        fs.write(fs.path("/dir/edit.txt"))


def test_zip_member_version_tag_is_crc(tmp_path: Path) -> None:
    _, fs = _mount(tmp_path)
    before = fs.version_tag(fs.path("/dir/edit.txt"))
    _write(fs, "/dir/edit.txt", b"new")
    assert fs.version_tag(fs.path("/dir/edit.txt")) != before


def test_failed_rebuild_leaves_archive_untouched(tmp_path: Path) -> None:
    archive, fs = _mount(tmp_path)
    original = archive.read_bytes()
    with zipfile.ZipFile(archive, "a") as z:
        z.writestr("dir/edit.txt", b"duplicate")
    duplicated = archive.read_bytes()
    fs.reload()
    writer = fs.write(fs.path("/dir/edit.txt"))
    writer.write(b"x")
    with pytest.raises(ValueError, match="Ambiguous"):
        writer.close()
    assert archive.read_bytes() == duplicated != original
    assert list((tmp_path / "work").iterdir()) == []


def test_concurrent_reads_survive_a_slow_commit(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    """A reader must never see ``self._archive`` pointing at an already-closed reader.

    Slows down the backing's commit() so a batch of concurrent fs.read() calls overlaps the
    window where the rebuilt archive is being published; before the race fix, roughly half of
    a similar batch failed with "Attempt to use ZIP archive that was already closed".
    """
    _, fs = _mount(tmp_path)
    assert fs._backing is not None
    real_commit = fs._backing.commit

    def slow_commit(rebuilt: Path) -> None:
        time.sleep(0.05)
        real_commit(rebuilt)

    monkeypatch.setattr(fs._backing, "commit", slow_commit)

    def read_keep() -> bytes:
        reader = fs.read(fs.path("/keep.txt"))
        try:
            return reader.read(10)
        finally:
            reader.close()

    with ThreadPoolExecutor(max_workers=8) as pool:
        futures = [pool.submit(read_keep) for _ in range(100)]
        _write(fs, "/dir/edit.txt", b"new")
        results = [future.result() for future in futures]

    assert results == [b"keep"] * 100


# ---------------------------------------------------------------------------
# Task 12: archives mounted from a remote filesystem (CopiedArchiveBacking)
# ---------------------------------------------------------------------------


def _zip_bytes(members: dict[str, bytes]) -> bytes:
    buffer = io.BytesIO()
    with zipfile.ZipFile(buffer, "w") as archive:
        for name, data in members.items():
            archive.writestr(name, data)
    return buffer.getvalue()


def _targz_bytes(members: dict[str, bytes]) -> bytes:
    buffer = io.BytesIO()
    with tarfile.open(fileobj=buffer, mode="w:gz") as archive:
        for name, data in members.items():
            info = tarfile.TarInfo(name)
            info.size = len(data)
            archive.addfile(info, io.BytesIO(data))
    return buffer.getvalue()


def _read_remote(fs: SchemeFs, path: str) -> bytes:
    """Read *path*'s full content back out of the mock filesystem."""
    reader = fs.read(fs.path(path))
    chunks: list[bytes] = []
    while chunk := reader.read(65536):
        chunks.append(chunk)
    reader.close()
    return b"".join(chunks)


def _mount_remote(fs: SchemeFs, remote_path: str, root: Path) -> tuple[ArchiveFilesystem, LocalCopy]:
    """Mount *remote_path* the way a remote archive is mounted in production: through a LocalCopy."""
    zip_vpath = fs.path(remote_path)
    copy = LocalCopy.create(zip_vpath, root)
    backing = CopiedArchiveBacking(copy)
    archive_fs = ArchiveFilesystem(zip_vpath.parent, backing, work_dir=root / "work")
    return archive_fs, copy


def test_write_replaces_member_on_remote_zip(tmp_path: Path) -> None:
    fs = SchemeFs({"/archives/a.zip": _zip_bytes({"dir/edit.txt": b"old", "keep.txt": b"keep"})})
    archive_fs, _ = _mount_remote(fs, "/archives/a.zip", tmp_path)

    _write(archive_fs, "/dir/edit.txt", b"new")

    with zipfile.ZipFile(io.BytesIO(_read_remote(fs, "/archives/a.zip"))) as z:
        assert z.read("dir/edit.txt") == b"new"
        assert z.read("keep.txt") == b"keep"


def test_write_replaces_member_on_remote_targz(tmp_path: Path) -> None:
    """Cheap tar.gz variant of the ZIP write-through-a-remote-copy test above."""
    fs = SchemeFs({"/archives/a.tar.gz": _targz_bytes({"dir/edit.txt": b"old", "keep.txt": b"keep"})})
    archive_fs, _ = _mount_remote(fs, "/archives/a.tar.gz", tmp_path)

    _write(archive_fs, "/dir/edit.txt", b"new")

    with tarfile.open(fileobj=io.BytesIO(_read_remote(fs, "/archives/a.tar.gz")), mode="r:gz") as t:
        edit_member = t.extractfile("dir/edit.txt")
        keep_member = t.extractfile("keep.txt")
        assert edit_member is not None
        assert edit_member.read() == b"new"
        assert keep_member is not None
        assert keep_member.read() == b"keep"


def test_write_after_remote_change_keeps_both_edits(tmp_path: Path) -> None:
    """prepare_write() re-downloads a remotely-changed archive so an overwrite doesn't lose it.

    Member A (dir/edit.txt) is written locally first; then member B (keep.txt) is changed
    directly on the remote, independently of this mount. Writing member A again must not
    clobber the remote change to member B: CopiedArchiveBacking.prepare_write() notices the
    source changed since the last download and refreshes the local copy before rebuilding.
    """
    fs = SchemeFs({"/archives/a.zip": _zip_bytes({"dir/edit.txt": b"old", "keep.txt": b"keep"})})
    archive_fs, _ = _mount_remote(fs, "/archives/a.zip", tmp_path)

    _write(archive_fs, "/dir/edit.txt", b"new")

    # Someone else replaces the whole archive on the remote, changing keep.txt, independent
    # of our local copy; overwrite() bumps the mock's recorded mtime so it is detected.
    overwrite(fs, "/archives/a.zip", _zip_bytes({"dir/edit.txt": b"new", "keep.txt": b"changed"}))

    _write(archive_fs, "/dir/edit.txt", b"newer")

    with zipfile.ZipFile(io.BytesIO(_read_remote(fs, "/archives/a.zip"))) as z:
        assert z.read("dir/edit.txt") == b"newer"
        assert z.read("keep.txt") == b"changed"


def test_close_does_not_delete_remote_local_copy(tmp_path: Path) -> None:
    """ArchiveFilesystem.close() only closes the archive reader; it never deletes the LocalCopy.

    The LocalCopy's lifetime (and cleanup of the process root on quit) belongs to a later
    LocalCopyManager task, not to ArchiveFilesystem.close().
    """
    fs = SchemeFs({"/archives/a.zip": _zip_bytes({"dir/edit.txt": b"old", "keep.txt": b"keep"})})
    archive_fs, copy = _mount_remote(fs, "/archives/a.zip", tmp_path)

    assert copy.path.exists()
    archive_fs.close()
    assert copy.path.exists()
