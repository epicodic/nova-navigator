"""AzureFilesystem — virtual filesystem backed by one Azure Blob Storage container."""

from __future__ import annotations

import errno
import io
from pathlib import PurePosixPath
from typing import override

from azure.core.exceptions import HttpResponseError, ResourceExistsError, ResourceNotFoundError
from azure.identity import DefaultAzureCredential
from azure.storage.blob import BlobPrefix, BlobProperties, ContainerClient, StorageStreamDownloader

from ..filesystem import Filesystem, StreamReaderLike, StreamWriterLike
from ..types import Stat
from ..vpath import VPath


def _blob_name(path: VPath) -> str:
    """Convert a VPath to a blob name (strip leading slash)."""
    return str(path.path).lstrip("/")


def _blob_prefix(path: VPath) -> str:
    """Return the blob prefix for a directory VPath (empty string for root, otherwise 'dir/name/')."""
    name = _blob_name(path)
    if not name:
        return ""
    return name if name.endswith("/") else name + "/"


class AzureFilesystem(Filesystem):
    """Filesystem implementation backed by one Azure Blob Storage container.

    The VFS root (``/``) maps to the container root.
    Blobs map to file paths; virtual directories are synthesised from
    the common prefixes returned by ``list_blobs(..., delimiter="/")``.
    Authentication uses :class:`~azure.identity.DefaultAzureCredential`.
    """

    class _BlobReader:
        """Wraps a StorageStreamDownloader to satisfy StreamReaderLike (adds close())."""

        def __init__(self, downloader: StorageStreamDownloader[str] | StorageStreamDownloader[bytes]) -> None:
            self._downloader = downloader

        def read(self, size: int) -> bytes:
            data = self._downloader.read(size)
            return data.encode() if isinstance(data, str) else data

        def close(self) -> None:
            pass  # StorageStreamDownloader has no close; nothing to release

    class _BlobWriter:
        """Write-through buffer that uploads to Azure on close."""

        def __init__(self, client: ContainerClient, blob_name: str) -> None:
            self._client = client
            self._blob_name = blob_name
            self._buf = io.BytesIO()

        def write(self, data: bytes) -> int:
            return self._buf.write(data)

        def close(self) -> None:
            content = self._buf.getvalue()
            self._buf.close()
            self._client.get_blob_client(self._blob_name).upload_blob(io.BytesIO(content), overwrite=True)

    _client: ContainerClient

    def __init__(
        self,
        account_url: str,
        container: str,
        *,
        client: ContainerClient | None = None,
    ) -> None:
        """Create an :class:`AzureFilesystem` for *container* on *account_url*.

        Args:
            account_url: Azure Storage service URL, e.g.
                ``https://myaccount.blob.core.windows.net``.
            container: Blob container name.
            client: Pre-constructed :class:`~azure.storage.blob.ContainerClient`
                to reuse (used in tests to inject a mock).
                When ``None``, a real client is built using
                :class:`~azure.identity.DefaultAzureCredential`.
        """
        if client is None:
            credential = DefaultAzureCredential()
            self._client = ContainerClient(account_url, container, credential=credential)
        else:
            self._client = client

    @override
    def cwd(self) -> VPath:
        return self.path("/")

    @override
    def root(self) -> VPath:
        return self.path("/")

    @override
    def home(self) -> VPath:
        return self.path("/")

    @override
    def parent(self, path: VPath) -> VPath:
        self._assert_vpath(path)
        parent = PurePosixPath(path.path).parent
        return self.path(str(parent))

    @override
    def is_same_device(self, path1: VPath, path2: VPath) -> bool:
        self._assert_vpath(path1)
        self._assert_vpath(path2)
        return True

    @override
    def iterdir(self, path: VPath) -> list[VPath]:
        self._assert_vpath(path)
        prefix = _blob_prefix(path)
        results: list[VPath] = []
        for item in self._client.walk_blobs(name_starts_with=prefix or None, delimiter="/"):
            if isinstance(item, BlobProperties):
                name = item.name
                if name == prefix:
                    continue  # skip the directory marker itself
                results.append(self.path("/" + name))
            else:
                # BlobPrefix — virtual directory
                assert isinstance(item, BlobPrefix)
                vdir = item.name
                results.append(self.path("/" + vdir.rstrip("/")))
        return results

    @override
    def stat(self, path: VPath) -> Stat:
        self._assert_vpath(path)
        blob_name = _blob_name(path)
        if not blob_name:
            # root is always a virtual directory
            return Stat(is_directory=True, size=0)
        try:
            props = self._client.get_blob_client(blob_name).get_blob_properties()
            modified = props.last_modified.timestamp() if props.last_modified else -1.0
            return Stat(
                size=props.size or 0,
                modified=modified,
            )
        except ResourceNotFoundError:
            # might be a virtual directory — check for any blobs with this prefix
            prefix = blob_name + "/"
            for _ in self._client.list_blobs(name_starts_with=prefix):
                return Stat(is_directory=True, size=0)
            raise FileNotFoundError(errno.ENOENT, "No such file or directory", str(path.path)) from None
        except HttpResponseError as exc:
            if exc.status_code in (401, 403):
                raise PermissionError(errno.EACCES, str(exc), str(path.path)) from exc
            raise OSError(errno.EIO, str(exc), str(path.path)) from exc

    @override
    def read(self, path: VPath) -> StreamReaderLike:
        self._assert_vpath(path)
        try:
            downloader = self._client.get_blob_client(_blob_name(path)).download_blob(encoding=None)
            return self._BlobReader(downloader)
        except ResourceNotFoundError:
            raise FileNotFoundError(errno.ENOENT, "No such file or directory", str(path.path)) from None

    @override
    def write(self, path: VPath) -> StreamWriterLike:
        self._assert_vpath(path)
        return self._BlobWriter(self._client, _blob_name(path))

    @override
    def remove(self, path: VPath) -> None:
        self._assert_vpath(path)
        try:
            self._client.get_blob_client(_blob_name(path)).delete_blob()
        except ResourceNotFoundError:
            raise FileNotFoundError(errno.ENOENT, "No such file or directory", str(path.path)) from None

    @override
    def rename(self, src_path: VPath, dst_path: VPath) -> None:
        self._assert_vpath(src_path)
        self._assert_vpath(dst_path)
        src_name = _blob_name(src_path)
        dst_name = _blob_name(dst_path)
        src_client = self._client.get_blob_client(src_name)
        dst_client = self._client.get_blob_client(dst_name)
        src_url = src_client.url
        try:
            # Note: copy + delete is not atomic; another client may observe both names briefly.
            dst_client.start_copy_from_url(src_url)
            src_client.delete_blob()
        except ResourceNotFoundError:
            raise FileNotFoundError(errno.ENOENT, "No such file or directory", str(src_path.path)) from None

    @override
    def rmdir(self, path: VPath) -> None:
        self._assert_vpath(path)
        prefix = _blob_prefix(path)
        marker = prefix + ".keep"
        # Check for non-marker blobs — refuse if any exist
        for item in self._client.list_blobs(name_starts_with=prefix):
            if isinstance(item, BlobProperties) and item.name != marker:
                raise OSError(errno.ENOTEMPTY, "Directory not empty", str(path.path))
        # Delete the marker blob if it exists
        try:
            self._client.get_blob_client(marker).delete_blob()
        except ResourceNotFoundError:
            raise FileNotFoundError(errno.ENOENT, "No such file or directory", str(path.path)) from None

    @override
    def mkdir(self, path: VPath) -> None:
        self._assert_vpath(path)
        marker = _blob_prefix(path) + ".keep"
        try:
            self._client.get_blob_client(marker).upload_blob(b"", overwrite=False)
        except ResourceExistsError:
            raise FileExistsError(errno.EEXIST, "File exists", str(path.path)) from None

    @override
    def copy_stat(self, path: VPath, stat: Stat) -> None:
        # Azure Blob does not support POSIX file attributes — no-op.
        pass

    @override
    def refresh(self, path: VPath | None = None) -> None:
        # No local cache maintained — no-op.
        pass

    @override
    def readlink(self, path: VPath) -> str:
        raise OSError(errno.EINVAL, "Not a symbolic link", str(path.path))
