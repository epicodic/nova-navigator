"""State and version information for external editing sessions."""

from dataclasses import dataclass
from enum import StrEnum
from pathlib import Path
from typing import Literal


class SessionState(StrEnum):
    PREPARING = "preparing"
    READY = "ready"
    CHANGED = "changed"
    SYNCING = "syncing"
    CONFLICT = "conflict"
    FAILED = "failed"
    COMPLETED = "completed"
    DISCARDED = "discarded"


@dataclass
class SourceVersion:
    kind: Literal["digest", "etag"]
    value: str
    size: int
    modified: float | None = None


@dataclass
class EditingSession:
    session_id: str
    source_uri: str
    archive_uri: str | None
    member_path: str | None
    mirror_path: Path
    archive_stage_path: Path | None
    initial_mirror_digest: str
    source_version: SourceVersion
    wait_for_exit: bool
    read_only: bool
    state: SessionState
    error: str | None = None


class SourceConflictError(Exception):
    """The source changed since the editing session began."""
