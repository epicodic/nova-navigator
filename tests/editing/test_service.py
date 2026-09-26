"""Editing-session state and write-back tests."""

import zipfile
from contextlib import closing
from pathlib import Path

from nova_navigator.editing.archive_mount import open_archive_mount
from nova_navigator.editing.model import SessionState
from nova_navigator.editing.service import EditingService
from nova_navigator.editing.store import SessionStore
from nova_navigator.vfs.filesystems.local import LocalFilesystem


def test_prepare_deduplicates_and_finishes_unchanged_mirror(tmp_path: Path) -> None:
    source = tmp_path / "source.txt"
    source.write_text("original")
    service = EditingService(SessionStore(tmp_path / "sessions"))
    path = LocalFilesystem.singleton().path(source)

    first = service.prepare(path)
    assert service.prepare(path) is first
    result = service.finish(first.session_id)

    assert result.state is SessionState.COMPLETED
    assert not first.mirror_path.exists()
    assert service.sessions == []


def test_changed_local_mirror_commits_and_cleans_session(tmp_path: Path) -> None:
    source = tmp_path / "source.txt"
    source.write_text("original")
    service = EditingService(SessionStore(tmp_path / "sessions"))
    session = service.prepare(LocalFilesystem.singleton().path(source))
    session.mirror_path.write_text("edited")

    result = service.finish(session.session_id)

    assert result.state is SessionState.COMPLETED
    assert source.read_text() == "edited"
    assert not session.mirror_path.exists()


def test_failed_source_commit_retains_changed_mirror_for_retry(tmp_path: Path) -> None:
    source = tmp_path / "source.txt"
    source.write_text("original")
    service = EditingService(SessionStore(tmp_path / "sessions"))
    session = service.prepare(LocalFilesystem.singleton().path(source))
    session.mirror_path.write_text("edited")
    source.write_text("changed elsewhere")

    result = service.finish(session.session_id)

    assert result.state is SessionState.CONFLICT
    assert result.mirror_path.read_text() == "edited"
    assert source.read_text() == "changed elsewhere"


def test_discard_removes_only_requested_session(tmp_path: Path) -> None:
    first_source = tmp_path / "first.txt"
    second_source = tmp_path / "second.txt"
    first_source.write_text("one")
    second_source.write_text("two")
    service = EditingService(SessionStore(tmp_path / "sessions"))
    first = service.prepare(LocalFilesystem.singleton().path(first_source))
    second = service.prepare(LocalFilesystem.singleton().path(second_source))

    service.discard(first.session_id)

    assert not first.mirror_path.exists()
    assert second.mirror_path.exists()
    assert service.sessions == [second]


def test_archive_member_commit_reloads_its_mount(tmp_path: Path) -> None:
    archive = tmp_path / "source.zip"
    with zipfile.ZipFile(archive, "w") as file:
        file.writestr("member.txt", b"original")
        file.writestr("other.txt", b"other")
    mount = open_archive_mount(LocalFilesystem.singleton().path(archive), tmp_path / "stages", lambda: False)
    member = mount.path("/member.txt")
    service = EditingService(SessionStore(tmp_path / "sessions"))
    session = service.prepare(member)
    session.mirror_path.write_bytes(b"edited")

    result = service.finish(session.session_id)

    assert result.state is SessionState.COMPLETED
    with closing(mount.read(member)) as reader:
        assert reader.read(100) == b"edited"
    mount.close()
