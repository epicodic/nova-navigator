"""Persistence and recovery tests for external editing sessions."""

import json
import os
import stat
from pathlib import Path

import pytest

from nova_navigator.editing.model import EditingSession, SessionState, SourceVersion
from nova_navigator.editing.store import SessionStore


def _session(session_id: str, mirror_path: Path) -> EditingSession:
    return EditingSession(
        session_id=session_id,
        source_uri="file:///tmp/source.txt",
        archive_uri=None,
        member_path=None,
        mirror_path=mirror_path,
        archive_stage_path=None,
        initial_mirror_digest="abc123",
        source_version=SourceVersion(kind="digest", value="abc123", size=4),
        wait_for_exit=True,
        read_only=False,
        state=SessionState.READY,
    )


def _record_path(mirror_path: Path) -> Path:
    return mirror_path.parent.parent / "session.json"


def test_create_allocates_unique_private_directories_and_mirrors(tmp_path: Path) -> None:
    store = SessionStore(tmp_path / "sessions")

    first_id, first_mirror = store.create("file:///tmp/a.txt", "a.txt")
    second_id, second_mirror = store.create("file:///tmp/a.txt", "a.txt")

    assert first_id != second_id
    assert first_mirror != second_mirror
    assert first_mirror.is_file()
    assert second_mirror.is_file()
    assert stat.S_IMODE(store.base_dir.stat().st_mode) == 0o700
    assert stat.S_IMODE(first_mirror.parent.parent.stat().st_mode) == 0o700
    assert stat.S_IMODE(first_mirror.parent.stat().st_mode) == 0o700
    assert stat.S_IMODE(first_mirror.stat().st_mode) == 0o600


def test_create_rejects_symlinked_base_without_changing_target(tmp_path: Path) -> None:
    target = tmp_path / "target"
    target.mkdir(mode=0o755)
    base = tmp_path / "sessions"
    base.symlink_to(target, target_is_directory=True)
    original_mode = stat.S_IMODE(target.stat().st_mode)

    with pytest.raises(ValueError, match="base directory"):
        SessionStore(base).create("file:///tmp/source", "source.txt")

    assert stat.S_IMODE(target.stat().st_mode) == original_mode
    assert list(target.iterdir()) == []


def test_recover_rejects_symlinked_base_without_reading_target(tmp_path: Path) -> None:
    target = tmp_path / "target"
    target.mkdir()
    base = tmp_path / "sessions"
    base.symlink_to(target, target_is_directory=True)

    with pytest.raises(ValueError, match="base directory"):
        SessionStore(base).recover()


def test_create_rejects_foreign_owned_base_without_changing_mode(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    base = tmp_path / "sessions"
    base.mkdir(mode=0o755)
    original_mode = stat.S_IMODE(base.stat().st_mode)
    monkeypatch.setattr(os, "getuid", lambda: base.stat().st_uid + 1)

    with pytest.raises(ValueError, match="base directory"):
        SessionStore(base).create("file:///tmp/source", "source.txt")

    assert stat.S_IMODE(base.stat().st_mode) == original_mode


def test_recover_rejects_foreign_owned_base(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    base = tmp_path / "sessions"
    base.mkdir()
    monkeypatch.setattr(os, "getuid", lambda: base.stat().st_uid + 1)

    with pytest.raises(ValueError, match="base directory"):
        SessionStore(base).recover()


def test_create_rejects_nondirectory_base(tmp_path: Path) -> None:
    base = tmp_path / "sessions"
    base.write_text("foreign data")

    with pytest.raises(ValueError, match="base directory"):
        SessionStore(base).create("file:///tmp/source", "source.txt")

    assert base.read_text() == "foreign data"


@pytest.mark.parametrize("basename", ["", "..", "a..b", "../secret", "nested/file", "nested\\file"])
def test_create_rejects_unsafe_basename(tmp_path: Path, basename: str) -> None:
    store = SessionStore(tmp_path / "sessions")

    with pytest.raises(ValueError, match="unsafe mirror basename"):
        store.create("file:///tmp/source", basename)


@pytest.mark.parametrize("basename", ["session.json", "mirror", ".metadata"])
def test_record_never_overwrites_mirror_with_reserved_basename(tmp_path: Path, basename: str) -> None:
    store = SessionStore(tmp_path / "sessions")
    session_id, mirror = store.create("file:///tmp/source", basename)
    mirror.write_text("editor content")

    store.save(_session(session_id, mirror))

    assert mirror.name == basename
    assert mirror.read_text() == "editor content"
    assert (store.base_dir / session_id / "session.json").is_file()


def test_save_atomically_replaces_existing_record(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    store = SessionStore(tmp_path / "sessions")
    session_id, mirror = store.create("file:///tmp/source.txt", "source.txt")
    session = _session(session_id, mirror)
    store.save(session)
    record = _record_path(mirror)
    original = record.read_bytes()
    real_replace = os.replace
    observations: list[tuple[bytes, bytes]] = []

    def inspect_replace(source: str | os.PathLike[str], target: str | os.PathLike[str]) -> None:
        observations.append((record.read_bytes(), Path(source).read_bytes()))
        assert Path(target) == record
        assert Path(source).parent == record.parent
        real_replace(source, target)

    monkeypatch.setattr(os, "replace", inspect_replace)
    session.state = SessionState.CHANGED
    store.save(session)

    assert len(observations) == 1
    assert observations[0][0] == original
    assert json.loads(observations[0][1])["state"] == "changed"
    assert json.loads(record.read_text())["state"] == "changed"
    assert list(record.parent.glob("*.tmp")) == []


def test_save_replace_failure_keeps_old_record_and_removes_temp(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    store = SessionStore(tmp_path / "sessions")
    session_id, mirror = store.create("file:///tmp/source.txt", "source.txt")
    session = _session(session_id, mirror)
    store.save(session)
    record = _record_path(mirror)
    original = record.read_bytes()

    def fail_replace(_source: str | os.PathLike[str], _target: str | os.PathLike[str]) -> None:
        raise OSError("replace failed")

    monkeypatch.setattr(os, "replace", fail_replace)
    session.state = SessionState.CHANGED

    with pytest.raises(OSError, match="replace failed"):
        store.save(session)

    assert record.read_bytes() == original
    assert list(record.parent.glob("*.tmp")) == []


def test_recover_returns_valid_session_and_reports_corrupt_record_without_deleting(tmp_path: Path) -> None:
    store = SessionStore(tmp_path / "sessions")
    session_id, mirror = store.create("file:///tmp/source.txt", "source.txt")
    session = _session(session_id, mirror)
    store.save(session)
    corrupt_dir = store.base_dir / "corrupt"
    corrupt_dir.mkdir()
    corrupt_record = corrupt_dir / "session.json"
    corrupt_record.write_text("{broken")

    sessions, issues = store.recover()

    assert sessions == [session]
    assert len(issues) == 1
    assert issues[0].directory == corrupt_dir
    assert issues[0].error
    assert corrupt_record.read_text() == "{broken"
    assert mirror.exists()


def test_recover_reports_missing_mirror_without_deleting_record(tmp_path: Path) -> None:
    store = SessionStore(tmp_path / "sessions")
    session_id, mirror = store.create("file:///tmp/source.txt", "source.txt")
    store.save(_session(session_id, mirror))
    mirror.unlink()

    sessions, issues = store.recover()

    assert sessions == []
    assert len(issues) == 1
    assert issues[0].directory == mirror.parent.parent
    assert "mirror" in issues[0].error.lower()
    assert _record_path(mirror).exists()


def test_recover_reports_missing_record_without_deleting_mirror(tmp_path: Path) -> None:
    store = SessionStore(tmp_path / "sessions")
    _, mirror = store.create("file:///tmp/source.txt", "source.txt")

    sessions, issues = store.recover()

    assert sessions == []
    assert len(issues) == 1
    assert issues[0].directory == mirror.parent.parent
    assert "record" in issues[0].error.lower()
    assert mirror.exists()


@pytest.mark.parametrize(
    ("field", "value"),
    [
        ("source_version.kind", "unknown"),
        ("source_version.size", -1),
        ("source_version.modified", "yesterday"),
        ("source_uri", 42),
        ("wait_for_exit", "yes"),
        ("mirror_path", "/elsewhere"),
        ("archive_stage_path", 42),
    ],
)
def test_recover_reports_malformed_record_without_deleting_files(tmp_path: Path, field: str, value: object) -> None:
    store = SessionStore(tmp_path / "sessions")
    session_id, mirror = store.create("file:///tmp/source.txt", "source.txt")
    store.save(_session(session_id, mirror))
    record = store.base_dir / session_id / "session.json"
    payload = json.loads(record.read_text())
    if field.startswith("source_version."):
        payload["source_version"][field.removeprefix("source_version.")] = value
    else:
        payload[field] = value
    record.write_text(json.dumps(payload))

    sessions, issues = store.recover()

    assert sessions == []
    assert len(issues) == 1
    assert issues[0].directory == record.parent
    assert issues[0].error
    assert record.exists()
    assert mirror.exists()


def test_recover_reports_numeric_session_id_and_keeps_healthy_session(tmp_path: Path) -> None:
    store = SessionStore(tmp_path / "sessions")
    healthy_id, healthy_mirror = store.create("file:///tmp/healthy", "healthy.txt")
    healthy = _session(healthy_id, healthy_mirror)
    store.save(healthy)
    bad_id, bad_mirror = store.create("file:///tmp/bad", "bad.txt")
    store.save(_session(bad_id, bad_mirror))
    record = _record_path(bad_mirror)
    payload = json.loads(record.read_text())
    payload["session_id"] = 42
    record.write_text(json.dumps(payload))

    sessions, issues = store.recover()

    assert sessions == [healthy]
    assert len(issues) == 1
    assert issues[0].directory == record.parent
    assert record.exists()
    assert bad_mirror.exists()


def test_recover_reports_missing_archive_stage_without_deleting_files(tmp_path: Path) -> None:
    store = SessionStore(tmp_path / "sessions")
    session_id, mirror = store.create("file:///tmp/source.txt", "source.txt")
    session = _session(session_id, mirror)
    stage = tmp_path / "archive-stage.tar"
    stage.write_bytes(b"archive")
    session.archive_stage_path = stage
    store.save(session)
    stage.unlink()

    sessions, issues = store.recover()

    assert sessions == []
    assert len(issues) == 1
    assert "archive stage" in issues[0].error.lower()
    assert mirror.exists()
    assert (store.base_dir / session_id / "session.json").exists()


def test_discard_removes_only_named_session_directory(tmp_path: Path) -> None:
    store = SessionStore(tmp_path / "sessions")
    first_id, first_mirror = store.create("file:///tmp/a.txt", "a.txt")
    second_id, second_mirror = store.create("file:///tmp/b.txt", "b.txt")
    first = _session(first_id, first_mirror)
    store.save(first)
    store.save(_session(second_id, second_mirror))

    store.discard(first)

    assert not first_mirror.parent.parent.exists()
    assert second_mirror.exists()
    assert _record_path(second_mirror).exists()
