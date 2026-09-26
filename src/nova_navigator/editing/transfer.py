"""Bounded-memory transfers and content digests for editing sessions."""

from __future__ import annotations

import hashlib
from collections.abc import Callable
from contextlib import closing
from pathlib import Path
from typing import Protocol

from nova_navigator.vfs.filesystems.azure import AzureFilesystem
from nova_navigator.vfs.vpath import VPath

from .model import SourceVersion

CHUNK_SIZE = 64 * 1024


class _Readable(Protocol):
    def read(self, size: int) -> bytes: ...


def _check_cancel(should_cancel: Callable[[], bool]) -> None:
    if should_cancel():
        raise InterruptedError("File transfer cancelled")


def _digest_stream(reader: _Readable, should_cancel: Callable[[], bool]) -> tuple[str, int]:
    digest = hashlib.sha256()
    size = 0
    while True:
        _check_cancel(should_cancel)
        chunk = reader.read(CHUNK_SIZE)
        if not chunk:
            break
        digest.update(chunk)
        size += len(chunk)
    return digest.hexdigest(), size


def mirror_file(source: VPath, destination: Path, should_cancel: Callable[[], bool]) -> tuple[str, int]:
    """Stream a VFS file to a local mirror and return its SHA-256 digest and size."""
    digest, size, _ = mirror_file_with_version(source, destination, should_cancel)
    return digest, size


def mirror_file_with_version(source: VPath, destination: Path, should_cancel: Callable[[], bool]) -> tuple[str, int, SourceVersion]:
    """Mirror a source and capture a version tied to the download.

    Destination must be a fresh preparation mirror: it is truncated when opened
    and removed if transfer or stream closure fails. For SSH, the downloaded
    content digest is authoritative; source size and modified time are advisory
    quick checks. Azure uses the ETag from this download response.
    """
    _check_cancel(should_cancel)
    is_azure = isinstance(source.filesystem.unwrap(), AzureFilesystem)
    source_stat = None if is_azure else source.stat
    destination_opened = False
    try:
        with closing(source.filesystem.read(source)) as reader:
            etag: str | None = None
            if is_azure:
                if not isinstance(reader, AzureFilesystem._BlobReader):
                    raise TypeError("Azure filesystem returned an unexpected reader")
                etag = reader.etag
            digest = hashlib.sha256()
            size = 0
            with destination.open("wb") as output:
                destination_opened = True
                while True:
                    _check_cancel(should_cancel)
                    chunk = reader.read(CHUNK_SIZE)
                    if not chunk:
                        break
                    output.write(chunk)
                    digest.update(chunk)
                    size += len(chunk)
            digest_value = digest.hexdigest()
            if etag is not None:
                version = SourceVersion(kind="etag", value=etag, size=size)
            else:
                assert source_stat is not None
                version = SourceVersion(kind="digest", value=digest_value, size=size, modified=source_stat.modified)
        return digest_value, size, version
    except BaseException:
        if destination_opened:
            destination.unlink(missing_ok=True)
        raise


def digest_file(path: Path, should_cancel: Callable[[], bool]) -> str:
    """Return the SHA-256 digest of a local file using bounded reads."""
    digest = hashlib.sha256()
    with path.open("rb") as reader:
        while True:
            _check_cancel(should_cancel)
            chunk = reader.read(CHUNK_SIZE)
            if not chunk:
                break
            digest.update(chunk)
    return digest.hexdigest()


def digest_vpath(path: VPath, should_cancel: Callable[[], bool]) -> str:
    """Return the SHA-256 digest of a VFS file using bounded reads."""
    reader = path.filesystem.read(path)
    try:
        digest, _ = _digest_stream(reader, should_cancel)
        return digest
    finally:
        reader.close()
