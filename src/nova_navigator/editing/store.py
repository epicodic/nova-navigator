"""Private, recoverable on-disk storage for editing sessions."""

import json
import os
import shutil
import stat
import tempfile
from dataclasses import asdict, dataclass
from pathlib import Path
from uuid import UUID, uuid4

from nova_navigator.editing.model import EditingSession, SessionState, SourceVersion

DEFAULT_BASE_DIR = Path(os.sep) / "tmp" / "nova-navigator"


@dataclass(frozen=True)
class RecoveryIssue:
    directory: Path
    error: str


class SessionStore:
    def __init__(self, base_dir: Path = DEFAULT_BASE_DIR) -> None:
        self.base_dir = base_dir

    def create(self, source_uri: str, basename: str) -> tuple[str, Path]:
        """Allocate a private session directory and an empty mirror file."""
        if not basename or ".." in basename or "/" in basename or "\\" in basename:
            raise ValueError("unsafe mirror basename")
        self._validate_base_dir(create=True)
        while True:
            session_id = str(uuid4())
            directory = self.base_dir / session_id
            try:
                directory.mkdir(mode=0o700)
                break
            except FileExistsError:
                continue
        mirror_directory = directory / "mirror"
        mirror_directory.mkdir(mode=0o700)
        mirror_path = mirror_directory / basename
        descriptor = os.open(mirror_path, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
        os.close(descriptor)
        return session_id, mirror_path

    def save(self, session: EditingSession) -> None:
        """Persist a session through a synced sibling file and atomic rename."""
        directory = self._directory_for(session)
        record_path = directory / "session.json"
        payload = asdict(session)
        payload["mirror_path"] = str(session.mirror_path)
        payload["archive_stage_path"] = str(session.archive_stage_path) if session.archive_stage_path else None
        payload["state"] = session.state.value
        descriptor, temporary_name = tempfile.mkstemp(prefix="session-", suffix=".tmp", dir=directory)
        temporary_path = Path(temporary_name)
        try:
            with os.fdopen(descriptor, "w", encoding="utf-8") as handle:
                json.dump(payload, handle)
                handle.flush()
                os.fsync(handle.fileno())
            os.replace(temporary_path, record_path)
        finally:
            temporary_path.unlink(missing_ok=True)

    def recover(self) -> tuple[list[EditingSession], list[RecoveryIssue]]:
        """Read valid sessions and report damaged records without deleting data."""
        sessions: list[EditingSession] = []
        issues: list[RecoveryIssue] = []
        if not self._validate_base_dir(create=False):
            return sessions, issues
        for directory in sorted(self.base_dir.iterdir()):
            if not directory.is_dir():
                continue
            record_path = directory / "session.json"
            if not record_path.exists():
                issues.append(RecoveryIssue(directory=directory, error="session record missing"))
                continue
            try:
                payload = json.loads(record_path.read_text(encoding="utf-8"))
                version = SourceVersion(**payload["source_version"])
                session = EditingSession(
                    session_id=payload["session_id"],
                    source_uri=payload["source_uri"],
                    archive_uri=payload["archive_uri"],
                    member_path=payload["member_path"],
                    mirror_path=Path(payload["mirror_path"]),
                    archive_stage_path=Path(payload["archive_stage_path"]) if payload["archive_stage_path"] else None,
                    initial_mirror_digest=payload["initial_mirror_digest"],
                    source_version=version,
                    wait_for_exit=payload["wait_for_exit"],
                    read_only=payload["read_only"],
                    state=SessionState(payload["state"]),
                    error=payload.get("error"),
                )
                if self._directory_for(session) != directory:
                    raise ValueError("session record does not match its directory")
                self._validate_session(session)
                if not session.mirror_path.is_file():
                    raise FileNotFoundError(f"mirror missing: {session.mirror_path}")
                if session.archive_stage_path is not None and not session.archive_stage_path.is_file():
                    raise FileNotFoundError(f"archive stage missing: {session.archive_stage_path}")
                sessions.append(session)
            except (OSError, ValueError, TypeError, KeyError) as exc:
                issues.append(RecoveryIssue(directory=directory, error=str(exc)))
        return sessions, issues

    def discard(self, session: EditingSession) -> None:
        """Remove the directory belonging to the named session."""
        shutil.rmtree(self._directory_for(session))

    def _directory_for(self, session: EditingSession) -> Path:
        if not isinstance(session.session_id, str):
            raise ValueError("invalid session ID")
        try:
            if str(UUID(session.session_id)) != session.session_id:
                raise ValueError("invalid session ID")
        except ValueError as exc:
            raise ValueError("invalid session ID") from exc
        directory = self.base_dir / session.session_id
        if session.mirror_path.parent != directory / "mirror":
            raise ValueError("mirror path is outside session directory")
        return directory

    def _validate_base_dir(self, *, create: bool) -> bool:
        try:
            info = self.base_dir.lstat()
        except FileNotFoundError:
            if not create:
                return False
            self.base_dir.mkdir(mode=0o700, parents=True, exist_ok=True)
            info = self.base_dir.lstat()
        if not stat.S_ISDIR(info.st_mode) or info.st_uid != os.getuid():
            raise ValueError("invalid session base directory")
        if create:
            self.base_dir.chmod(0o700)
        return True

    @staticmethod
    def _validate_session(session: EditingSession) -> None:
        version = session.source_version
        if version.kind not in ("digest", "etag"):
            raise ValueError("invalid source version kind")
        if not isinstance(version.value, str):
            raise ValueError("invalid source version value")
        if isinstance(version.size, bool) or not isinstance(version.size, int) or version.size < 0:
            raise ValueError("invalid source version size")
        if version.modified is not None and (isinstance(version.modified, bool) or not isinstance(version.modified, (int, float))):
            raise ValueError("invalid source version modified time")
        if not isinstance(session.source_uri, str):
            raise ValueError("invalid source URI")
        if session.archive_uri is not None and not isinstance(session.archive_uri, str):
            raise ValueError("invalid archive URI")
        if session.member_path is not None and not isinstance(session.member_path, str):
            raise ValueError("invalid member path")
        if not isinstance(session.initial_mirror_digest, str):
            raise ValueError("invalid mirror digest")
        if not isinstance(session.wait_for_exit, bool) or not isinstance(session.read_only, bool):
            raise ValueError("invalid session flags")
        if session.error is not None and not isinstance(session.error, str):
            raise ValueError("invalid session error")
        if not session.mirror_path.is_absolute() or not session.mirror_path.name:
            raise ValueError("invalid mirror path")
        if session.archive_stage_path is not None and not session.archive_stage_path.is_absolute():
            raise ValueError("invalid archive stage path")
