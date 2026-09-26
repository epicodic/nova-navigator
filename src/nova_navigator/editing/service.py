"""Coordinate recoverable local mirrors for external editing."""

from __future__ import annotations

import os
from collections.abc import Callable
from pathlib import Path, PurePosixPath

from nova_navigator.archive.archives import is_archive_writable
from nova_navigator.vfs.filesystems.archive import ArchiveFilesystem
from nova_navigator.vfs.filesystems.azure import AzureFilesystem
from nova_navigator.vfs.filesystems.local import LocalFilesystem
from nova_navigator.vfs.filesystems.ssh import SSHFilesystem
from nova_navigator.vfs.vpath import VPath

from .archive_rebuild import rebuild_archive
from .commit import commit_azure, commit_local, commit_ssh
from .model import EditingSession, SessionState, SourceConflictError, SourceVersion
from .store import RecoveryIssue, SessionStore
from .transfer import digest_file, digest_vpath, mirror_file_with_version

Resolver = Callable[[str], VPath | None]


class EditingService:
    """Own editing-session state transitions and source write-back.

    The service is deliberately synchronous because transfer and commit work is
    dispatched off Textual's event loop by its callers.
    """

    def __init__(self, store: SessionStore, resolve_source: Resolver | None = None) -> None:
        self._store = store
        self._resolve_source = resolve_source
        self._sessions: dict[str, EditingSession] = {}
        self._sources: dict[str, VPath] = {}
        self._archive_mounts: dict[str, ArchiveFilesystem] = {}

    @property
    def sessions(self) -> list[EditingSession]:
        """Return active sessions in creation order."""
        return list(self._sessions.values())

    def prepare(self, source: VPath, *, wait_for_exit: bool = False) -> EditingSession:
        """Create and persist one local mirror for *source*.

        A second open of the same source returns the existing active session.
        """
        source_key = self._source_key(source)
        for session in self._sessions.values():
            session_key = f"{session.archive_uri}#{session.member_path}" if session.archive_uri is not None else session.source_uri
            if session_key == source_key and session.state not in {SessionState.COMPLETED, SessionState.DISCARDED}:
                return session

        archive_filesystem = source.filesystem if isinstance(source.filesystem, ArchiveFilesystem) else None
        original = archive_filesystem.source if archive_filesystem is not None else source
        member_path = source.path.as_posix().lstrip("/") if archive_filesystem is not None else None
        archive_uri = original.uri if archive_filesystem is not None else None
        read_only = archive_filesystem is not None and not is_archive_writable(original.path)
        session_id, mirror = self._store.create(source_key, source.name)
        try:
            if archive_filesystem is None:
                digest, _size, version = mirror_file_with_version(source, mirror, lambda: False)
                archive_stage_path = None
            else:
                digest, _size = self._mirror_member(source, mirror)
                version = archive_filesystem.source_version or self._version_for(original)
                archive_stage_path = archive_filesystem.local_path
            if read_only:
                mirror.chmod(0o400)
            session = EditingSession(
                session_id=session_id,
                source_uri=original.uri,
                archive_uri=archive_uri,
                member_path=member_path,
                mirror_path=mirror,
                archive_stage_path=archive_stage_path,
                initial_mirror_digest=digest,
                source_version=version,
                wait_for_exit=wait_for_exit,
                read_only=read_only,
                state=SessionState.READY,
            )
            self._sessions[session_id] = session
            self._sources[session_id] = original
            if archive_filesystem is not None:
                self._archive_mounts[session_id] = archive_filesystem
            self._store.save(session)
            return session
        except BaseException:
            self._store.discard(self._placeholder(session_id, mirror, source_key))
            raise

    def finish(self, session_id: str, force: bool = False) -> EditingSession:
        """Check a mirror and write it back when it changed."""
        session = self._get(session_id)
        changed = digest_file(session.mirror_path, lambda: False) != session.initial_mirror_digest
        if not changed:
            self._complete(session)
            return session
        session.state = SessionState.CHANGED
        self._store.save(session)
        if session.read_only:
            return session
        session.state = SessionState.SYNCING
        self._store.save(session)
        try:
            source = self._source_for(session)
            self._commit(session, source, force)
        except SourceConflictError as error:
            session.state = SessionState.CONFLICT
            session.error = str(error)
            self._store.save(session)
        except (OSError, RuntimeError, TypeError, ValueError) as error:
            session.state = SessionState.FAILED
            session.error = str(error)
            self._store.save(session)
        else:
            self._complete(session)
        return session

    def retry(self, session_id: str) -> EditingSession:
        """Retry a failed or conflicted session without forcing overwrite."""
        return self.finish(session_id)

    def discard(self, session_id: str) -> None:
        """Discard a mirror only when explicitly requested."""
        session = self._get(session_id)
        session.state = SessionState.DISCARDED
        self._store.save(session)
        self._store.discard(session)
        self._sessions.pop(session_id, None)
        self._sources.pop(session_id, None)
        self._archive_mounts.pop(session_id, None)

    def recover(self) -> tuple[list[EditingSession], list[RecoveryIssue]]:
        """Reload unfinished sessions and reconnect source paths when possible."""
        sessions, issues = self._store.recover()
        for session in sessions:
            if session.state in {SessionState.COMPLETED, SessionState.DISCARDED}:
                continue
            self._sessions[session.session_id] = session
            if self._resolve_source is not None:
                source = self._resolve_source(session.archive_uri or session.source_uri)
                if source is not None:
                    self._sources[session.session_id] = source
        return self.sessions, issues

    def _commit(self, session: EditingSession, source: VPath, force: bool) -> None:
        replacement = session.mirror_path
        if session.member_path is not None:
            if session.archive_stage_path is None:
                raise ValueError("archive session has no staged archive")
            rebuilt = session.archive_stage_path.with_suffix(session.archive_stage_path.suffix + ".rebuilt")
            rebuild_archive(session.archive_stage_path, PurePosixPath(session.member_path), replacement, rebuilt)
            try:
                self._commit_path(source, rebuilt, session.source_version, force)
                os.replace(rebuilt, session.archive_stage_path)
                mount = self._archive_mounts.get(session.session_id)
                if mount is not None:
                    mount.reload()
            finally:
                rebuilt.unlink(missing_ok=True)
            return
        self._commit_path(source, replacement, session.source_version, force)

    def _commit_path(self, source: VPath, replacement: Path, version: SourceVersion, force: bool) -> None:
        filesystem = source.filesystem.unwrap()
        if isinstance(filesystem, LocalFilesystem):
            commit_local(Path(source.path), replacement, version, force)
        elif isinstance(filesystem, SSHFilesystem):
            commit_ssh(source, replacement, version, force)
        elif isinstance(filesystem, AzureFilesystem):
            commit_azure(source, replacement, version, force)
        else:
            raise TypeError(f"Unsupported editing filesystem: {type(filesystem).__name__}")

    def _complete(self, session: EditingSession) -> None:
        session.state = SessionState.COMPLETED
        session.error = None
        self._store.save(session)
        self._store.discard(session)
        self._sessions.pop(session.session_id, None)
        self._sources.pop(session.session_id, None)
        self._archive_mounts.pop(session.session_id, None)

    def _source_for(self, session: EditingSession) -> VPath:
        source = self._sources.get(session.session_id)
        if source is None and self._resolve_source is not None:
            source = self._resolve_source(session.archive_uri or session.source_uri)
            if source is not None:
                self._sources[session.session_id] = source
        if source is None:
            raise ValueError("source connection is unavailable")
        return source

    def _get(self, session_id: str) -> EditingSession:
        try:
            return self._sessions[session_id]
        except KeyError as error:
            raise ValueError(f"unknown editing session: {session_id}") from error

    @staticmethod
    def _mirror_member(source: VPath, mirror: Path) -> tuple[str, int]:
        reader = source.filesystem.read(source)
        try:
            with mirror.open("wb") as output:
                while chunk := reader.read(64 * 1024):
                    output.write(chunk)
        finally:
            reader.close()
        return digest_file(mirror, lambda: False), mirror.stat().st_size

    @staticmethod
    def _version_for(source: VPath) -> SourceVersion:
        digest = digest_vpath(source, lambda: False)
        stat = source.stat
        return SourceVersion("digest", digest, stat.size, stat.modified)

    @staticmethod
    def _source_key(source: VPath) -> str:
        if isinstance(source.filesystem, ArchiveFilesystem):
            return f"{source.filesystem.source.uri}#{source.path.as_posix()}"
        return source.uri

    @staticmethod
    def _placeholder(session_id: str, mirror: Path, source_uri: str) -> EditingSession:
        return EditingSession(
            session_id,
            source_uri,
            None,
            None,
            mirror,
            None,
            "",
            SourceVersion("digest", "", 0),
            False,
            False,
            SessionState.PREPARING,
        )
