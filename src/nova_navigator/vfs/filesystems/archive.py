from __future__ import annotations

import errno
import hashlib
import io
import tarfile
import tempfile
import threading
import uuid
import zipfile
from collections.abc import AsyncIterator
from contextlib import ExitStack
from pathlib import Path
from typing import IO, Literal, override
from weakref import WeakSet

from ...archive import Archive, open_archive
from ...archive.archives import is_archive_writable, is_zip_archive
from ...archive.backing import ArchiveBacking, LocalArchiveBacking
from ...archive.tar_rebuild import rebuild_tar
from ...archive.zip_rebuild import rebuild_zip
from ..change_detector import file_digest
from ..filesystem import AtomicWriterLike, Filesystem, FilesystemCapabilities, Stat, StreamReaderLike
from ..process_root import process_root
from ..vpath import VPath
from .local import LocalFilesystem

_CHUNK = 1024 * 1024


class ArchiveFilesystem(Filesystem):
    """Filesystem backed by a tar or zip archive; writable when the format and source allow it.

    Pass either a pre-opened :class:`~nova_navigator.archive.Archive` (read-only, no backing),
    an :class:`~nova_navigator.archive.backing.ArchiveBacking`, or a
    :class:`~nova_navigator.vfs.vpath.VPath` pointing to an archive file on the local filesystem
    (wrapped into a :class:`~nova_navigator.archive.backing.LocalArchiveBacking`).
    *archive_parent* is the :class:`VPath` returned by :meth:`parent` when the caller asks for
    the parent of the archive root — i.e. the directory that contains the archive file itself.
    """

    _archive_parent: VPath
    _archive: Archive
    _backing: ArchiveBacking | None

    def __init__(
        self,
        archive_parent: VPath,
        archive: Archive | ArchiveBacking | VPath,
        *,
        work_dir: Path | None = None,
    ) -> None:
        self._archive_parent = archive_parent
        self._closed = False
        self._member_paths: WeakSet[VPath] = WeakSet()
        self._write_lock = threading.Lock()
        if isinstance(archive, Archive):
            self._backing = None
            self._archive = archive
        else:
            if isinstance(archive, VPath):
                assert isinstance(archive.filesystem, LocalFilesystem)
                self._backing = LocalArchiveBacking(archive)
            else:
                self._backing = archive
            self._archive = open_archive(self._backing.local_path, mode="r")
        self._identity = self._archive
        self._local_path = Path(self._archive.archive_path)
        self._work_dir = work_dir if work_dir is not None else process_root() / "work"
        self._archive_format: Literal["zip", "tar"] = "zip" if is_zip_archive(self.source.path) else "tar"

    @property
    def source(self) -> VPath:
        """Original archive source, independent of member paths and local staging."""
        if self._backing is not None:
            return self._backing.source
        return VPath(self._local_path, LocalFilesystem.singleton())

    @property
    def local_path(self) -> Path:
        """Local archive bytes backing this mount."""
        return self._local_path

    def close(self) -> None:
        """Close the underlying archive reader."""
        if self._closed:
            return
        self._archive.close()
        self._closed = True

    def reload(self) -> None:
        """Reopen replaced archive bytes and invalidate cached member metadata."""
        if self._closed:
            raise ValueError("Archive mount is closed")
        self._reopen()

    def _reopen(self) -> None:
        """Reopen the archive reader from ``local_path`` and invalidate cached member stats.

        Opens the replacement archive before touching ``self._archive`` and only closes the
        previous reader afterwards, so a concurrent ``read()``/``stat()`` sees either the old or
        the new reader through ``self._archive`` and never one that has already been closed. If
        opening the replacement fails, ``self._archive`` is left untouched (still open) and the
        mount stays usable with stale content; the exception propagates to the caller.
        """
        new_archive = open_archive(self._local_path, mode="r")
        old_archive = self._archive
        self._archive = new_archive
        for member in self._member_paths:
            member._stat = None
        old_archive.close()

    @override
    def cwd(self) -> VPath:
        return self.root()

    @override
    def root(self) -> VPath:
        return VPath("/", self)

    @override
    def home(self) -> VPath:
        return self.root()

    @property
    @override
    def capabilities(self) -> FilesystemCapabilities:
        read_only = self._backing is None or not is_archive_writable(self.source.path) or self._backing.source.filesystem.capabilities.read_only
        return FilesystemCapabilities(
            streaming_iterdir=False,
            watch=False,
            symlinks=False,
            permissions=False,
            read_only=read_only,
        )

    @property
    @override
    def scheme(self) -> str:
        return "archive"

    @override
    async def iterdir(
        self,
        path: VPath,
        *,
        cancel: threading.Event | None = None,
    ) -> AsyncIterator[VPath]:
        self._assert_vpath(path)
        for entry in self._archive.listdir(path.path):
            if cancel is not None and cancel.is_set():
                return
            vp = VPath(path.path / entry, self)
            vp._stat = self.stat(vp)
            yield vp

    @override
    def parent(self, path: VPath) -> VPath:
        self._assert_vpath(path)
        if path.path.parent == path.path:
            return self._archive_parent
        return VPath(path.path.parent, self)

    @override
    def stat(self, path: VPath) -> Stat:
        self._assert_vpath(path)
        self._member_paths.add(path)
        return self._archive.stats(path.path)

    @override
    def is_same_device(self, path1: VPath, path2: VPath) -> bool:
        self._assert_vpath(path1)
        return path1.filesystem == path2.filesystem

    @override
    def read(self, path: VPath) -> StreamReaderLike:
        self._assert_vpath(path)
        return self._archive.read(path.path)

    @override
    def write(self, path: VPath) -> AtomicWriterLike:
        self._assert_vpath(path)
        if self.capabilities.read_only or self._backing is None:
            raise PermissionError(f"Archive is read-only: {self.source.uri}")
        self._work_dir.mkdir(mode=0o700, parents=True, exist_ok=True)
        return _MemberWriter(self, path.path.as_posix().lstrip("/"))

    @override
    def write_atomic(self, path: VPath) -> AtomicWriterLike:
        return self.write(path)

    @override
    def version_tag(self, path: VPath) -> str | None:
        self._assert_vpath(path)
        return self._archive.version_tag(path.path)

    def _commit_member(self, member: str, spooled: Path) -> None:
        """Rebuild the archive with *member* replaced by *spooled* and publish it.

        Runs under ``_write_lock`` so concurrent member writes serialize. ``self._archive`` is
        kept open through the (potentially slow) rebuild, validation, and commit — os.replace()/
        rename() work fine against a path with an open reader on Linux — and reopened only in
        ``_reopen()``, which never leaves ``self._archive`` pointing at a closed reader. The
        archive reader is always reopened afterwards — including on failure — so the mount stays
        usable; a failed rebuild never touches the source archive.
        """
        assert self._backing is not None
        with self._write_lock:
            self._backing.prepare_write()
            rebuilt = self._work_dir / f"{uuid.uuid4().hex}.rebuilt"
            try:
                rebuild = rebuild_zip if self._archive_format == "zip" else rebuild_tar
                rebuild(self._backing.local_path, member, spooled, rebuilt)
                _validate_member(rebuilt, member, spooled, self._archive_format)
                self._backing.commit(rebuilt)
            finally:
                rebuilt.unlink(missing_ok=True)
                self._reopen()

    # Only member content can be replaced (via write()); archive structure stays unsupported
    # regardless of whether this mount is writable. io.UnsupportedOperation is an OSError
    # subclass, so callers (e.g. the vfs shell) that catch OSError handle it uniformly.

    @override
    def remove(self, path: VPath) -> None:
        raise io.UnsupportedOperation("ArchiveFilesystem does not support removing members")

    @override
    def rename(self, src_path: VPath, dst_path: VPath) -> None:
        raise io.UnsupportedOperation("ArchiveFilesystem does not support renaming members")

    @override
    def rmdir(self, path: VPath) -> None:
        raise io.UnsupportedOperation("ArchiveFilesystem does not support removing directories")

    @override
    def mkdir(self, path: VPath) -> None:
        raise io.UnsupportedOperation("ArchiveFilesystem does not support creating directories")

    @override
    def copy_stat(self, path: VPath, stat: Stat) -> None:
        pass  # no attribute support; Filesystem.copy_stat() calls for a no-op here

    @override
    def refresh(self, path: VPath | None = None) -> None:
        pass  # no caching in ArchiveFilesystem

    @override
    def readlink(self, path: VPath) -> str:
        self._assert_vpath(path)
        raise OSError(errno.EINVAL, "Not a symbolic link", str(path.path))

    def __eq__(self, value: object) -> bool:
        return isinstance(value, ArchiveFilesystem) and self._identity == value._identity and self._archive_parent == value._archive_parent

    def __hash__(self) -> int:
        return hash((self._identity, self._archive_parent))


class _MemberWriter:
    """Spools written bytes to a private temp file; ``close()`` rebuilds the archive around it.

    ``close()`` and ``abort()`` are both idempotent: a second call to either is a no-op, and
    the spool file is always removed exactly once, whether or not the commit succeeded.
    """

    def __init__(self, fs: ArchiveFilesystem, member: str) -> None:
        self._fs = fs
        self._member = member
        with ExitStack() as stack:
            self._file = stack.enter_context(tempfile.NamedTemporaryFile(dir=fs._work_dir, delete=False))
            self._exit_stack = stack.pop_all()
        self._path = Path(self._file.name)
        self._done = False

    def write(self, data: bytes) -> int:
        return self._file.write(data)

    def close(self) -> None:
        if self._done:
            return
        self._done = True
        try:
            self._exit_stack.close()
            self._fs._commit_member(self._member, self._path)
        finally:
            self._path.unlink(missing_ok=True)

    def abort(self) -> None:
        if self._done:
            return
        self._done = True
        self._exit_stack.close()
        self._path.unlink(missing_ok=True)


def _digest_stream(reader: IO[bytes]) -> str:
    """Return the SHA-256 hex digest of *reader*'s remaining content."""
    digest = hashlib.sha256()
    while chunk := reader.read(_CHUNK):
        digest.update(chunk)
    return digest.hexdigest()


def _validate_member(rebuilt: Path, member: str, spooled: Path, archive_format: Literal["zip", "tar"]) -> None:
    """Raise ValueError unless *member* inside *rebuilt* matches *spooled* byte for byte."""
    expected = file_digest(spooled)
    if archive_format == "zip":
        with zipfile.ZipFile(rebuilt) as zf, zf.open(member) as reader:
            actual = _digest_stream(reader)
    else:
        with tarfile.open(rebuilt, "r:*") as tf:
            info = next((candidate for candidate in tf.getmembers() if candidate.name in (member, f"./{member}")), None)
            if info is None:
                raise ValueError(f"Rebuilt archive is missing member: {member}")
            reader = tf.extractfile(info)
            if reader is None:
                raise ValueError(f"Rebuilt archive member is not a regular file: {member}")
            actual = _digest_stream(reader)
    if actual != expected:
        raise ValueError("Rebuilt member does not match replacement")
