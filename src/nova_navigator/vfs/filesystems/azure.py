"""AzureFilesystem — virtual filesystem backed by one Azure Blob Storage container."""

from __future__ import annotations

import asyncio
import errno
import importlib
import io
import logging
import threading
import time
from collections.abc import AsyncIterator, Iterator
from contextlib import contextmanager
from pathlib import PurePath, PurePosixPath
from types import ModuleType
from typing import TYPE_CHECKING, override

from ..filesystem import Filesystem, FilesystemCapabilities, StreamReaderLike, StreamWriterLike
from ..types import Stat
from ..vpath import VPath

if TYPE_CHECKING:
    from azure.storage.blob import BlobPrefix, BlobProperties, ContainerClient, StorageStreamDownloader

_logger = logging.getLogger(__name__)

_COPY_TIMEOUT = 300.0  # seconds to wait for a server-side copy that is still pending
_COPY_POLL = 0.2  # seconds between two checks of a pending copy

MISSING_AZURE_EXTRA_MESSAGE = """\
Azure Blob support is not installed. Install the extra 'nova-navigator[azure]', for example: \
uv tool install 'nova-navigator[azure]' (or pipx install 'nova-navigator[azure]')."""


class MissingAzureExtraError(RuntimeError):
    """The Azure SDK, shipped by the `azure` extra, is not installed."""

    def __init__(self) -> None:
        super().__init__(MISSING_AZURE_EXTRA_MESSAGE)


def _sdk() -> ModuleType:
    """Import the Azure SDK on first use.

    Raises:
        MissingAzureExtraError: If the SDK is not installed.
    """
    try:
        return importlib.import_module("._azure_sdk", __package__)
    except ModuleNotFoundError as exc:
        if exc.name is None or exc.name.split(".")[0] != "azure":
            raise
        raise MissingAzureExtraError from exc


@contextmanager
def _as_oserror(path: str) -> Iterator[None]:
    """Re-raise an Azure SDK error from the block as the OSError that matches it."""
    sdk = _sdk()
    try:
        yield
    except sdk.ResourceNotFoundError as exc:
        raise FileNotFoundError(errno.ENOENT, "No such file or directory", path) from exc
    except sdk.ClientAuthenticationError as exc:
        raise PermissionError(errno.EACCES, str(exc), path) from exc
    except sdk.HttpResponseError as exc:
        if exc.status_code in (401, 403):
            raise PermissionError(errno.EACCES, str(exc), path) from exc
        raise OSError(errno.EIO, str(exc), path) from exc
    except sdk.AzureError as exc:
        raise OSError(errno.EIO, str(exc), path) from exc


def _blob_name(path: VPath) -> str:
    """Convert a VPath to a blob name (strip leading slash)."""
    return str(path.path).lstrip("/")


def _location_of(path: VPath) -> tuple[str, str] | None:
    """Return the (account URL, container) a path lives in, or None if it is not on an AzureFilesystem."""
    fs = path.filesystem
    if not isinstance(fs, AzureFilesystem):
        return None
    return fs._account_url.rstrip("/"), fs._container


def _blob_prefix(path: VPath) -> str:
    """Return the blob prefix for a directory VPath (empty string for root, otherwise 'dir/name/')."""
    name = _blob_name(path)
    if not name:
        return ""
    return name if name.endswith("/") else name + "/"


def _parent_prefix(blob_name: str) -> str:
    """Return the prefix of the directory that holds *blob_name* ('' for the container root)."""
    parent = str(PurePosixPath(blob_name).parent)
    return "" if parent == "." else parent + "/"


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

        @property
        def etag(self) -> str:
            """ETag from the response that created this downloader."""
            etag = self._downloader.properties.etag
            if not isinstance(etag, str):
                raise ValueError("Download response has no ETag")
            return etag

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
            with _as_oserror(self._blob_name):
                self._client.get_blob_client(self._blob_name).upload_blob(io.BytesIO(content), overwrite=True)

    _client: ContainerClient
    _emptied: set[str]

    def __init__(
        self,
        account_url: str,
        container: str,
        *,
        proxy_url: str | None = None,
        managed_identity: bool = False,
        client: ContainerClient | None = None,
    ) -> None:
        """Create an :class:`AzureFilesystem` for *container* on *account_url*.

        Args:
            account_url: Azure Storage service URL, e.g.
                ``https://myaccount.blob.core.windows.net``.
            container: Blob container name.
            proxy_url: Optional HTTP/HTTPS proxy URL, e.g. ``http://host:1080``.
                Applied to all SDK requests when set.
            managed_identity: When ``True``, ``ManagedIdentityCredential`` (IMDS) is
                included in the ``DefaultAzureCredential`` chain even when a proxy is
                configured.  The caller must ensure ``169.254.169.254`` bypasses the
                proxy (e.g. via the ``NO_PROXY`` environment variable), otherwise the
                IMDS probe will time out.  When ``False`` (default) and a proxy is
                set, IMDS is excluded to avoid ~20 s of retries.
            client: Pre-constructed :class:`~azure.storage.blob.ContainerClient`
                to reuse (used in tests to inject a mock).
                When ``None``, a real client is built using
                :class:`~azure.identity.DefaultAzureCredential`.
        """
        if client is None:
            sdk = _sdk()
            _logger.info(
                "Connecting to Azure Blob Storage: account_url=%r container=%r proxy=%r managed_identity=%r",
                account_url,
                container,
                proxy_url,
                managed_identity,
            )
            proxies: dict[str, str] | None = {"http": proxy_url, "https": proxy_url} if proxy_url else None
            # Skip ManagedIdentityCredential when a proxy is set and the user has not
            # explicitly requested managed identity.  169.254.169.254 (IMDS) is a
            # link-local address that can never be reached through a proxy; the SDK
            # retries with exponential back-off (~20 s) before giving up otherwise.
            exclude_mi = proxy_url is not None and not managed_identity
            credential = sdk.DefaultAzureCredential(
                exclude_managed_identity_credential=exclude_mi,
                proxies=proxies,
            )
            self._client = sdk.ContainerClient(account_url, container, credential=credential, proxies=proxies)
            _logger.info("ContainerClient created for %r / %r", account_url, container)
        else:
            self._client = client
        self._account_url = account_url
        self._container = container
        # Prefixes of directories that lost a child since they were last created: their marker may be gone already.
        self._emptied = set()

    @override
    def cwd(self) -> VPath:
        return self.path("/")

    @override
    def uri_for_path(self, path: PurePath) -> str:
        netloc = self._account_url.removeprefix("https://").removeprefix("http://")
        return f"azure://{netloc}/{self._container}{path}"

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
        location = _location_of(path1)
        return location is not None and location == _location_of(path2)

    @property
    @override
    def capabilities(self) -> FilesystemCapabilities:
        return FilesystemCapabilities(
            streaming_iterdir=True,
            watch=False,
        )

    @property
    @override
    def scheme(self) -> str:
        return "azure"

    @override
    async def iterdir(
        self,
        path: VPath,
        *,
        cancel: threading.Event | None = None,
    ) -> AsyncIterator[VPath]:
        self._assert_vpath(path)
        prefix = _blob_prefix(path)

        if cancel is not None and cancel.is_set():
            return

        loop = asyncio.get_running_loop()
        queue: asyncio.Queue[VPath | OSError | None] = asyncio.Queue()

        def _item_to_vpath(item: BlobProperties | BlobPrefix) -> VPath | None:
            if isinstance(item, _sdk().BlobProperties):
                name = item.name
                if name == prefix:
                    return None
                vpath = self.path("/" + name)
                modified = item.last_modified.timestamp() if item.last_modified else -1.0
                basename = PurePosixPath(name.rstrip("/")).name
                vpath._stat = Stat(
                    size=item.size or 0,
                    modified=modified,
                    is_hidden=basename.startswith("."),
                )
                return vpath
            else:
                assert isinstance(item, _sdk().BlobPrefix)
                vdir = item.name
                vpath = self.path("/" + vdir.rstrip("/"))
                basename = PurePosixPath(vdir.rstrip("/")).name
                vpath._stat = Stat(is_directory=True, size=0, is_hidden=basename.startswith("."))
                return vpath

        def _produce() -> None:
            try:
                with _as_oserror(str(path.path)):
                    for item in self._client.walk_blobs(name_starts_with=prefix or None, delimiter="/"):
                        if cancel is not None and cancel.is_set():
                            break
                        vp = _item_to_vpath(item)
                        if vp is not None:
                            loop.call_soon_threadsafe(queue.put_nowait, vp)
            except OSError as exc:
                loop.call_soon_threadsafe(queue.put_nowait, exc)
            finally:
                loop.call_soon_threadsafe(queue.put_nowait, None)  # sentinel

        threading.Thread(target=_produce, daemon=True).start()

        while True:
            item = await queue.get()
            if isinstance(item, OSError):
                raise item
            if item is None or (cancel is not None and cancel.is_set()):
                break
            yield item

    @override
    def stat(self, path: VPath) -> Stat:
        self._assert_vpath(path)
        blob_name = _blob_name(path)
        if not blob_name:
            # root is always a virtual directory
            return Stat(is_directory=True, size=0)
        sdk = _sdk()
        with _as_oserror(str(path.path)):
            try:
                props = self._client.get_blob_client(blob_name).get_blob_properties()
            except sdk.ResourceNotFoundError:
                props = None
            if props is None:
                # might be a virtual directory — check for any blobs with this prefix
                prefix = blob_name + "/"
                for _ in self._client.list_blobs(name_starts_with=prefix):
                    return Stat(is_directory=True, size=0)
                raise FileNotFoundError(errno.ENOENT, "No such file or directory", str(path.path)) from None
            modified = props.last_modified.timestamp() if props.last_modified else -1.0
            return Stat(
                size=props.size or 0,
                modified=modified,
            )

    @override
    def read(self, path: VPath) -> StreamReaderLike:
        self._assert_vpath(path)
        with _as_oserror(str(path.path)):
            downloader = self._client.get_blob_client(_blob_name(path)).download_blob(encoding=None)
            return self._BlobReader(downloader)

    @override
    def write(self, path: VPath) -> StreamWriterLike:
        self._assert_vpath(path)
        return self._BlobWriter(self._client, _blob_name(path))

    @override
    def remove(self, path: VPath) -> None:
        self._assert_vpath(path)
        blob_name = _blob_name(path)
        with _as_oserror(str(path.path)):
            self._client.get_blob_client(blob_name).delete_blob()
        self._emptied.add(_parent_prefix(blob_name))

    @override
    def rename(self, src_path: VPath, dst_path: VPath) -> None:
        self._assert_vpath(src_path)
        if not self.is_same_device(src_path, dst_path):
            raise OSError(errno.EXDEV, "Cannot rename across containers", str(src_path.path))
        src_name = _blob_name(src_path)
        dst_name = _blob_name(dst_path)
        sdk = _sdk()
        with _as_oserror(str(src_path.path)):
            try:
                self._move_blob(src_name, dst_name)
                return
            except sdk.ResourceNotFoundError:
                pass  # not a blob: a directory moves blob by blob under its prefix
            src_prefix = src_name + "/"
            dst_prefix = dst_name + "/"
            names = [item.name for item in self._client.list_blobs(name_starts_with=src_prefix)]
            if not names:
                raise FileNotFoundError(errno.ENOENT, "No such file or directory", str(src_path.path))
            if dst_prefix.startswith(src_prefix):
                raise OSError(errno.EINVAL, "Cannot move a directory into itself", str(src_path.path))
            for name in names:
                self._move_blob(name, dst_prefix + name.removeprefix(src_prefix))

    def _move_blob(self, src_name: str, dst_name: str) -> None:
        """Copy one blob, wait for the server-side copy, then delete the source.

        Raises:
            ResourceNotFoundError: When the source blob is missing.
            OSError: When the copy does not complete; the source is kept.
        """
        src_client = self._client.get_blob_client(src_name)
        dst_client = self._client.get_blob_client(dst_name)
        # Note: copy + delete is not atomic; another client may observe both names briefly.
        status = dst_client.start_copy_from_url(src_client.url).get("copy_status")
        deadline = time.monotonic() + _COPY_TIMEOUT
        while status == "pending" and time.monotonic() < deadline:
            time.sleep(_COPY_POLL)
            status = dst_client.get_blob_properties().copy.status
        if status != "success":
            raise OSError(errno.EIO, f"Server-side copy did not complete (status: {status}); the source was kept", src_name)
        src_client.delete_blob()

    @override
    def rmdir(self, path: VPath) -> None:
        self._assert_vpath(path)
        prefix = _blob_prefix(path)
        marker = prefix + ".keep"
        sdk = _sdk()
        with _as_oserror(str(path.path)):
            # Check for non-marker blobs — refuse if any exist
            for item in self._client.list_blobs(name_starts_with=prefix):
                if isinstance(item, _sdk().BlobProperties) and item.name != marker:
                    raise OSError(errno.ENOTEMPTY, "Directory not empty", str(path.path))
            # Delete the marker blob if it exists; a marker removed as a child of an erase is not missing
            try:
                self._client.get_blob_client(marker).delete_blob()
            except sdk.ResourceNotFoundError:
                if prefix not in self._emptied:
                    raise FileNotFoundError(errno.ENOENT, "No such file or directory", str(path.path)) from None
        self._emptied.discard(prefix)
        self._emptied.add(_parent_prefix(marker))

    @override
    def mkdir(self, path: VPath) -> None:
        self._assert_vpath(path)
        prefix = _blob_prefix(path)
        marker = prefix + ".keep"
        sdk = _sdk()
        with _as_oserror(str(path.path)):
            try:
                self._client.get_blob_client(marker).upload_blob(b"", overwrite=False)
            except sdk.ResourceExistsError:
                raise FileExistsError(errno.EEXIST, "File exists", str(path.path)) from None
        self._emptied.discard(prefix)

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

    @override
    def supports_symlinks(self) -> bool:
        return False

    @override
    def symlink(self, path: VPath, target: str) -> None:
        self._assert_vpath(path)
        raise OSError(errno.ENOTSUP, "Azure Blob storage does not support symbolic links", str(path.path))

    @override
    def version_tag(self, path: VPath) -> str | None:
        self._assert_vpath(path)
        with _as_oserror(str(path.path)):
            props = self._client.get_blob_client(_blob_name(path)).get_blob_properties()
        etag = props.etag
        return etag if isinstance(etag, str) else None
