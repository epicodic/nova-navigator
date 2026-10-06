"""Local copies of VFS files with baselines and write-back."""

from __future__ import annotations

import hashlib
import os
from collections.abc import Callable
from dataclasses import dataclass
from enum import StrEnum
from pathlib import Path, PurePosixPath

from .change_detector import file_digest
from .filesystems.archive import ArchiveFilesystem
from .filesystems.local import LocalFilesystem
from .vpath import VPath

_MAX_SEGMENT_BYTES = 255
_MAX_SUFFIX_BYTES = 16
_CHUNK_SIZE = 1024 * 1024


def sanitize_segment(segment: str) -> str:
    """Validate one path segment and shorten it to a filesystem-safe length.

    Raises:
        ValueError: *segment* is empty, ``.``, ``..``, or contains a path
            separator or NUL byte.
    """
    if segment in {"", ".", ".."} or "/" in segment or "\0" in segment:
        raise ValueError(f"Unsafe path segment: {segment!r}")
    if len(segment.encode()) <= _MAX_SEGMENT_BYTES:
        return segment
    suffix = PurePosixPath(segment).suffix
    suffix = suffix if len(suffix.encode()) <= _MAX_SUFFIX_BYTES else ""
    tag = "~" + hashlib.sha256(segment.encode()).hexdigest()[:_MAX_SUFFIX_BYTES]
    budget = _MAX_SEGMENT_BYTES - len(tag.encode()) - len(suffix.encode())
    stem = segment.encode()[:budget].decode(errors="ignore")
    return stem + tag + suffix


def _segments(path: PurePosixPath) -> PurePosixPath:
    """Sanitize every non-root part of *path* and rejoin it as a relative path."""
    return PurePosixPath(*[sanitize_segment(part) for part in path.parts if part != "/"])


def relative_copy_path(source: VPath) -> PurePosixPath:
    """Map *source* to a path below the process root that mirrors its URI."""
    filesystem = source.filesystem
    if isinstance(filesystem, ArchiveFilesystem):
        return PurePosixPath("archive") / relative_copy_path(filesystem.source) / _segments(source.path)
    if isinstance(filesystem.unwrap(), LocalFilesystem):
        return PurePosixPath("local") / _segments(source.path)
    scheme, separator, rest = source.uri.partition("://")
    if not separator:
        raise ValueError(f"Cannot map URI without scheme: {source.uri}")
    return _segments(PurePosixPath(scheme)) / _segments(PurePosixPath("/" + rest))


class ReuseAction(StrEnum):
    """Decision for what to do with an existing local copy when its source is reopened."""

    REUSE = "reuse"
    REFRESH = "refresh"
    CONFLICT = "conflict"


@dataclass(frozen=True)
class SourceFingerprint:
    """Fresh-stat identity of a source; for archive members includes the container."""

    size: int
    modified: float
    tag: str | None
    container: SourceFingerprint | None = None

    @classmethod
    def of(cls, source: VPath) -> SourceFingerprint:
        """Return a freshly-stat'd fingerprint of *source* (never the cached VPath stat)."""
        filesystem = source.filesystem
        stat = filesystem.stat(source)
        container = cls.of(filesystem.source) if isinstance(filesystem, ArchiveFilesystem) else None
        return cls(stat.size, stat.modified, filesystem.version_tag(source), container)


@dataclass(frozen=True)
class Baseline:
    """State recorded at the last download or successful write-back."""

    source: SourceFingerprint
    digest: str


ProgressCallback = Callable[[int], None]


def is_local_source(source: VPath) -> bool:
    """True if *source* is a plain file of the local filesystem: it needs no local copy.

    An archive member is never local, even a member of a local archive.
    """
    # ArchiveFilesystem.unwrap() currently returns the archive filesystem itself, so the first
    # check alone already rejects archive members. The explicit exclusion keeps members of local
    # archives non-local even if unwrap() is ever changed to return the container's filesystem.
    return isinstance(source.filesystem.unwrap(), LocalFilesystem) and not isinstance(source.filesystem, ArchiveFilesystem)


class LocalCopy:
    """A local file standing in for a VFS source, with a baseline for change decisions."""

    def __init__(
        self,
        source: VPath,
        path: Path,
        baseline: Baseline,
        *,
        read_only: bool,
        pass_through: bool,
    ) -> None:
        self._source = source
        self._path = path
        self._baseline = baseline
        self._read_only = read_only
        self._pass_through = pass_through

    @classmethod
    def create(
        cls,
        source: VPath,
        root: Path,
        *,
        read_only: bool = False,
        progress: ProgressCallback | None = None,
    ) -> LocalCopy:
        """Download *source* below *root*, or wrap a local source without copying.

        A source already on the local filesystem (and not an archive member,
        even one mounted from a local archive) is a pass-through: ``path``
        is the source file itself and nothing is copied or created below *root*.
        """
        if is_local_source(source):
            path = Path(source.path)
            return cls(source, path, Baseline(SourceFingerprint.of(source), ""), read_only=read_only, pass_through=True)
        path = root / relative_copy_path(source)
        path.parent.mkdir(mode=0o700, parents=True, exist_ok=True)
        # mkdir(mode=...) is affected by umask and only applies to the leaf directory it
        # creates, so newly created ancestors (and root itself) may not end up at 0o700;
        # chmod every ancestor below and including root explicitly.
        for parent in path.relative_to(root).parents:
            (root / parent).chmod(0o700)
        copy = cls(source, path, Baseline(SourceFingerprint.of(source), ""), read_only=read_only, pass_through=False)
        copy._download(progress)
        return copy

    @property
    def source(self) -> VPath:
        """The VFS path this copy mirrors."""
        return self._source

    @property
    def path(self) -> Path:
        """Local filesystem path of the copy, or of the source itself if pass-through."""
        return self._path

    @property
    def read_only(self) -> bool:
        """True if this copy must never be written back to its source."""
        return self._read_only

    @property
    def is_pass_through(self) -> bool:
        """True if *source* is already a local file and no local copy was made."""
        return self._pass_through

    @property
    def baseline(self) -> Baseline:
        """State recorded at the last download or successful write-back."""
        return self._baseline

    def local_digest(self) -> str:
        """Return the current SHA-256 digest of the local file."""
        return file_digest(self._path)

    def is_modified(self) -> bool:
        """True if the local file's content differs from the baseline digest."""
        return not self._pass_through and self.local_digest() != self._baseline.digest

    def source_changed(self) -> bool:
        """True if a fresh fingerprint of the source differs from the baseline."""
        return SourceFingerprint.of(self._source) != self._baseline.source

    def reuse_action(self) -> ReuseAction:
        """Decide what to do with an existing local copy on reopen."""
        if self._pass_through:
            return ReuseAction.REUSE
        if not self._path.exists():
            return ReuseAction.REFRESH
        modified = self.is_modified()
        changed = self.source_changed()
        if modified and changed:
            return ReuseAction.CONFLICT
        return ReuseAction.REFRESH if changed else ReuseAction.REUSE

    def refresh(self, progress: ProgressCallback | None = None) -> None:
        """Replace the local file with the current source content."""
        self._baseline = Baseline(SourceFingerprint.of(self._source), "")
        self._download(progress)

    def write_back(self, progress: ProgressCallback | None = None) -> None:
        """Replace the source with the local file's content and advance the baseline.

        Raises:
            PermissionError: This copy is read-only.
        """
        if self._read_only:
            raise PermissionError(f"Local copy is read-only: {self._source.uri}")
        if self._pass_through:
            return
        writer = self._source.filesystem.write_atomic(self._source)
        digest = hashlib.sha256()
        try:
            with self._path.open("rb") as reader:
                while chunk := reader.read(_CHUNK_SIZE):
                    writer.write(chunk)
                    digest.update(chunk)
                    if progress is not None:
                        progress(len(chunk))
        except BaseException:
            writer.abort()
            raise
        writer.close()
        self._baseline = Baseline(SourceFingerprint.of(self._source), digest.hexdigest())

    def rebase_container(self) -> None:
        """Accept a new container fingerprint when this member's own stat is unchanged.

        Lets syncing one member of an archive absorb the archive's new fingerprint
        for sibling copies whose own member content did not change, so editing two
        members of one archive does not conflict.
        """
        current = SourceFingerprint.of(self._source)
        base = self._baseline.source
        if (current.size, current.modified, current.tag) == (base.size, base.modified, base.tag):
            self._baseline = Baseline(current, self._baseline.digest)

    def discard(self) -> None:
        """Delete the local file (never the source)."""
        if not self._pass_through:
            self._path.unlink(missing_ok=True)

    def _download(self, progress: ProgressCallback | None) -> None:
        """Write the source's current content to ``path`` via a sibling temp file and rename.

        An editor holding the old file's inode open never sees a half-written copy.
        """
        reader = self._source.filesystem.read(self._source)
        digest = hashlib.sha256()
        tmp = self._path.with_name(f".{self._path.name}.nn-download")
        try:
            with os.fdopen(os.open(tmp, os.O_WRONLY | os.O_CREAT | os.O_TRUNC, 0o600), "wb") as output:
                while chunk := reader.read(_CHUNK_SIZE):
                    output.write(chunk)
                    digest.update(chunk)
                    if progress is not None:
                        progress(len(chunk))
            os.replace(tmp, self._path)
        except BaseException:
            tmp.unlink(missing_ok=True)
            raise
        finally:
            reader.close()
        if self._read_only:
            self._path.chmod(0o400)
        self._baseline = Baseline(self._baseline.source, digest.hexdigest())
