# Azure Filesystem Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use skills:subagent-driven-development (recommended) or skills:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Implement `AzureFilesystem`, a `Filesystem` subclass that exposes one Azure Blob Storage container as a navigable virtual filesystem, plus an `azure://` URI scheme, unit tests with a mock `ContainerClient`, and integration tests against Azurite.

**Architecture:** `AzureFilesystem` is scoped to one container — the VFS root (`/`) maps to the container root, blobs map to file paths, and virtual directories are synthesised from the common prefixes returned by `list_blobs(..., delimiter="/")`. Authentication uses `DefaultAzureCredential` exclusively. The `azure://` scheme is registered in `SchemeRegistry` alongside the existing `file` handler.

**Tech Stack:** Python 3.12, pytest, azure-storage-blob, azure-identity (both already in `pyproject.toml`)

**Coding Conventions:** `docs/coding_conventions.md` — read before implementing

**Spec:** `docs/agents/specs/2026-05-14-azure-filesystem-design.md`

---

## File map

| Action | Path | Responsibility |
|---|---|---|
| Replace | `src/nova_navigator/vfs/filesystems/azure.py` | `AzureFilesystem` class |
| Modify | `src/nova_navigator/vfs/filesystems/__init__.py` | re-export `AzureFilesystem` |
| Modify | `src/nova_navigator/vfs/scheme_registry.py` | register `azure` scheme |
| Create | `tests/vfs/test_azure_filesystem.py` | unit tests (mock `ContainerClient`) |
| Create | `tests/vfs/integration/test_azure_filesystem_integration.py` | integration tests (Azurite) |

---

## Task 1: Implement `AzureFilesystem` — constructor and navigation methods

**Files:**
- Replace: `src/nova_navigator/vfs/filesystems/azure.py`
- Test: `tests/vfs/test_azure_filesystem.py`

- [ ] **Step 1.1: Write failing tests for constructor and navigation**

Create `tests/vfs/test_azure_filesystem.py`:

```python
"""Tests for AzureFilesystem using an injected mock ContainerClient."""

from __future__ import annotations

from pathlib import PurePosixPath
from unittest.mock import MagicMock

from azure.storage.blob import ContainerClient

from nova_navigator.vfs.filesystems.azure import AzureFilesystem
from nova_navigator.vfs.vpath import VPath


def _make_fs() -> tuple[AzureFilesystem, MagicMock]:
    """Return (fs, mock_client) with no real network activity."""
    mock_client = MagicMock(spec=ContainerClient)
    fs = AzureFilesystem("https://test.blob.core.windows.net", "testcontainer", client=mock_client)
    return fs, mock_client


def test_cwd_returns_root() -> None:
    fs, _ = _make_fs()
    assert str(fs.cwd().path) == "/"


def test_root_returns_slash() -> None:
    fs, _ = _make_fs()
    assert str(fs.root().path) == "/"


def test_home_returns_slash() -> None:
    fs, _ = _make_fs()
    assert str(fs.home().path) == "/"


def test_parent_returns_parent_directory() -> None:
    fs, _ = _make_fs()
    p = fs.path("/foo/bar/baz.txt")
    assert str(fs.parent(p).path) == "/foo/bar"


def test_parent_of_root_is_root() -> None:
    fs, _ = _make_fs()
    p = fs.path("/")
    assert str(fs.parent(p).path) == "/"


def test_is_same_device_always_true() -> None:
    fs, _ = _make_fs()
    p1 = fs.path("/a")
    p2 = fs.path("/b")
    assert fs.is_same_device(p1, p2) is True
```

- [ ] **Step 1.2: Run tests to verify they fail**

```
uv run pytest tests/vfs/test_azure_filesystem.py -v
```
Expected: FAIL — `AzureFilesystem` not yet implemented.

- [ ] **Step 1.3: Implement constructor and navigation methods**

Replace `src/nova_navigator/vfs/filesystems/azure.py` with:

```python
"""AzureFilesystem — virtual filesystem backed by one Azure Blob Storage container."""

from __future__ import annotations

import errno
import io
from pathlib import PurePosixPath
from typing import override

from azure.core.exceptions import HttpResponseError, ResourceExistsError, ResourceNotFoundError
from azure.identity import DefaultAzureCredential
from azure.storage.blob import BlobProperties, ContainerClient

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

    class _BlobWriter:
        """Write-through buffer that uploads to Azure on close."""

        def __init__(self, client: ContainerClient, blob_name: str) -> None:
            self._client = client
            self._blob_name = blob_name
            self._buf = io.BytesIO()

        def write(self, data: bytes) -> int:
            return self._buf.write(data)

        def close(self) -> None:
            self._buf.seek(0)
            self._client.get_blob_client(self._blob_name).upload_blob(self._buf, overwrite=True)

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
        for item in self._client.list_blobs(name_starts_with=prefix or None, delimiter="/"):
            if isinstance(item, BlobProperties):
                name = item.name
                if name == prefix:
                    continue  # skip the directory marker itself
                results.append(self.path("/" + name))
            else:
                # BlobPrefix — virtual directory
                vdir = item.get("name", "")
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
            raise OSError(errno.EIO, str(exc), str(path.path)) from exc

    @override
    def read(self, path: VPath) -> StreamReaderLike:
        self._assert_vpath(path)
        try:
            return self._client.get_blob_client(_blob_name(path)).download_blob()
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
        dst_client.start_copy_from_url(src_url)
        src_client.delete_blob()

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
```

- [ ] **Step 1.4: Run tests to verify they pass**

```
uv run pytest tests/vfs/test_azure_filesystem.py -v
```
Expected: all tests from step 1.1 PASS.

- [ ] **Step 1.5: Coding-guideline follow-up checklist**

- [ ] Conventions file read: `docs/coding_conventions.md`
- [ ] All methods have full type annotations
- [ ] `@override` decorator on all `Filesystem` method implementations
- [ ] No `# noqa` or `# type: ignore` comments
- [ ] `uv run ruff check src/nova_navigator/vfs/filesystems/azure.py` — zero errors
- [ ] `uv run ty check src/nova_navigator/vfs/filesystems/azure.py` — zero errors

---

## Task 2: Unit tests — `iterdir`, `stat`, `read`, `write`, `remove`, `rename`, `mkdir`, `rmdir`, `readlink`

**Files:**
- Modify: `tests/vfs/test_azure_filesystem.py`

- [ ] **Step 2.1: Write failing tests**

Append to `tests/vfs/test_azure_filesystem.py`:

```python
import errno
from datetime import datetime, timezone
from unittest.mock import call, patch

import pytest
from azure.core.exceptions import ResourceExistsError, ResourceNotFoundError
from azure.storage.blob import BlobPrefix, BlobProperties


def _make_blob_properties(name: str, size: int = 100, mtime: float = 1000.0) -> BlobProperties:
    """Build a minimal BlobProperties-like mock."""
    props = MagicMock(spec=BlobProperties)
    props.name = name
    props.size = size
    props.last_modified = datetime.fromtimestamp(mtime, tz=timezone.utc)
    return props


def _make_blob_prefix(name: str) -> dict:
    """Azure SDK returns BlobPrefix as a dict-like object with a 'name' key."""
    return {"name": name}


# ── iterdir ───────────────────────────────────────────────────────────────────

def test_iterdir_root_passes_none_prefix() -> None:
    fs, mock_client = _make_fs()
    mock_client.list_blobs.return_value = []
    fs.iterdir(fs.path("/"))
    mock_client.list_blobs.assert_called_once_with(name_starts_with=None, delimiter="/")


def test_iterdir_subdir_passes_prefix_with_slash() -> None:
    fs, mock_client = _make_fs()
    mock_client.list_blobs.return_value = []
    fs.iterdir(fs.path("/foo"))
    mock_client.list_blobs.assert_called_once_with(name_starts_with="foo/", delimiter="/")


def test_iterdir_returns_blobs_and_prefixes() -> None:
    fs, mock_client = _make_fs()
    blob = _make_blob_properties("docs/readme.txt")
    prefix = _make_blob_prefix("docs/images/")
    mock_client.list_blobs.return_value = [blob, prefix]
    results = fs.iterdir(fs.path("/docs"))
    paths = [str(p.path) for p in results]
    assert "/docs/readme.txt" in paths
    assert "/docs/images" in paths


def test_iterdir_skips_directory_marker() -> None:
    fs, mock_client = _make_fs()
    marker = _make_blob_properties("foo/.keep")
    blob = _make_blob_properties("foo/bar.txt")
    mock_client.list_blobs.return_value = [marker, blob]
    results = fs.iterdir(fs.path("/foo"))
    paths = [str(p.path) for p in results]
    # .keep is a blob so it shows — the directory marker (prefix/.keep = foo/.keep) is skipped
    # Note: directory marker at "foo/" is the prefix itself; "foo/.keep" is a regular blob
    assert "/foo/bar.txt" in paths


# ── stat ──────────────────────────────────────────────────────────────────────

def test_stat_root_is_directory() -> None:
    fs, _ = _make_fs()
    s = fs.stat(fs.path("/"))
    assert s.is_directory is True


def test_stat_blob_returns_size_and_mtime() -> None:
    fs, mock_client = _make_fs()
    props = _make_blob_properties("data/file.txt", size=42, mtime=1234.0)
    mock_client.get_blob_client.return_value.get_blob_properties.return_value = props
    s = fs.stat(fs.path("/data/file.txt"))
    assert s.size == 42
    assert s.modified == pytest.approx(1234.0)
    assert s.is_directory is False


def test_stat_virtual_dir_synthesised_when_blobs_exist() -> None:
    fs, mock_client = _make_fs()
    mock_client.get_blob_client.return_value.get_blob_properties.side_effect = ResourceNotFoundError
    mock_client.list_blobs.return_value = [_make_blob_properties("folder/file.txt")]
    s = fs.stat(fs.path("/folder"))
    assert s.is_directory is True


def test_stat_missing_raises_file_not_found() -> None:
    fs, mock_client = _make_fs()
    mock_client.get_blob_client.return_value.get_blob_properties.side_effect = ResourceNotFoundError
    mock_client.list_blobs.return_value = []
    with pytest.raises(FileNotFoundError):
        fs.stat(fs.path("/missing.txt"))


# ── read ──────────────────────────────────────────────────────────────────────

def test_read_returns_downloader() -> None:
    fs, mock_client = _make_fs()
    downloader = MagicMock()
    mock_client.get_blob_client.return_value.download_blob.return_value = downloader
    result = fs.read(fs.path("/file.txt"))
    assert result is downloader


def test_read_missing_raises_file_not_found() -> None:
    fs, mock_client = _make_fs()
    mock_client.get_blob_client.return_value.download_blob.side_effect = ResourceNotFoundError
    with pytest.raises(FileNotFoundError):
        fs.read(fs.path("/missing.txt"))


# ── write ─────────────────────────────────────────────────────────────────────

def test_write_uploads_on_close() -> None:
    fs, mock_client = _make_fs()
    writer = fs.write(fs.path("/out/file.txt"))
    writer.write(b"hello")
    writer.close()
    mock_client.get_blob_client.assert_called_with("out/file.txt")
    mock_client.get_blob_client.return_value.upload_blob.assert_called_once()
    _, kwargs = mock_client.get_blob_client.return_value.upload_blob.call_args
    assert kwargs.get("overwrite") is True


# ── remove ────────────────────────────────────────────────────────────────────

def test_remove_calls_delete_blob() -> None:
    fs, mock_client = _make_fs()
    fs.remove(fs.path("/data/file.txt"))
    mock_client.get_blob_client.assert_called_with("data/file.txt")
    mock_client.get_blob_client.return_value.delete_blob.assert_called_once()


def test_remove_missing_raises_file_not_found() -> None:
    fs, mock_client = _make_fs()
    mock_client.get_blob_client.return_value.delete_blob.side_effect = ResourceNotFoundError
    with pytest.raises(FileNotFoundError):
        fs.remove(fs.path("/missing.txt"))


# ── rename ────────────────────────────────────────────────────────────────────

def test_rename_copies_then_deletes() -> None:
    fs, mock_client = _make_fs()
    src_blob_client = MagicMock()
    dst_blob_client = MagicMock()
    src_blob_client.url = "https://test.blob.core.windows.net/testcontainer/old.txt"

    def _get_client(name: str) -> MagicMock:
        return src_blob_client if name == "old.txt" else dst_blob_client

    mock_client.get_blob_client.side_effect = _get_client
    fs.rename(fs.path("/old.txt"), fs.path("/new.txt"))
    dst_blob_client.start_copy_from_url.assert_called_once_with(src_blob_client.url)
    src_blob_client.delete_blob.assert_called_once()


# ── mkdir ─────────────────────────────────────────────────────────────────────

def test_mkdir_uploads_keep_marker() -> None:
    fs, mock_client = _make_fs()
    fs.mkdir(fs.path("/newdir"))
    mock_client.get_blob_client.assert_called_with("newdir/.keep")
    mock_client.get_blob_client.return_value.upload_blob.assert_called_once_with(b"", overwrite=False)


def test_mkdir_raises_if_exists() -> None:
    fs, mock_client = _make_fs()
    mock_client.get_blob_client.return_value.upload_blob.side_effect = ResourceExistsError
    with pytest.raises(FileExistsError):
        fs.mkdir(fs.path("/existing"))


# ── rmdir ─────────────────────────────────────────────────────────────────────

def test_rmdir_deletes_keep_marker() -> None:
    fs, mock_client = _make_fs()
    mock_client.list_blobs.return_value = []
    fs.rmdir(fs.path("/emptydir"))
    mock_client.get_blob_client.assert_called_with("emptydir/.keep")
    mock_client.get_blob_client.return_value.delete_blob.assert_called_once()


def test_rmdir_raises_enotempty_when_blobs_exist() -> None:
    fs, mock_client = _make_fs()
    extra = _make_blob_properties("mydir/extra.txt")
    mock_client.list_blobs.return_value = [extra]
    with pytest.raises(OSError) as exc_info:
        fs.rmdir(fs.path("/mydir"))
    assert exc_info.value.errno == errno.ENOTEMPTY


# ── readlink ──────────────────────────────────────────────────────────────────

def test_readlink_raises_einval() -> None:
    fs, _ = _make_fs()
    with pytest.raises(OSError) as exc_info:
        fs.readlink(fs.path("/file.txt"))
    assert exc_info.value.errno == errno.EINVAL
```

- [ ] **Step 2.2: Run tests to verify they fail**

```
uv run pytest tests/vfs/test_azure_filesystem.py -v
```
Expected: FAIL — methods not yet implemented (we only added constructor and navigation in Task 1).

- [ ] **Step 2.3: Run tests to verify they pass with the Task 1 implementation**

The full implementation was provided in Task 1 step 1.3. After adding the tests above, re-run:

```
uv run pytest tests/vfs/test_azure_filesystem.py -v
```
Expected: all tests PASS.

If any test fails, fix the implementation in `src/nova_navigator/vfs/filesystems/azure.py`.
Key things to verify:
- `iterdir` passes `name_starts_with=None` (not empty string) for root.
- `rmdir` lists blobs to check emptiness, then deletes the marker; the prefix computation must be `"emptydir/"`, and `list_blobs` is called before `get_blob_client`.
- `_make_blob_prefix` returns a plain dict, so `isinstance(item, BlobProperties)` is `False` for prefixes — `iterdir` must handle both cases.

- [ ] **Step 2.4: Coding-guideline follow-up checklist**

- [ ] Conventions file read: `docs/coding_conventions.md`
- [ ] All test functions have `-> None` return annotation
- [ ] `uv run ruff check tests/vfs/test_azure_filesystem.py` — zero errors
- [ ] `uv run ty check tests/vfs/test_azure_filesystem.py` — zero errors

---

## Task 3: Export `AzureFilesystem` and register `azure://` scheme

**Files:**
- Modify: `src/nova_navigator/vfs/filesystems/__init__.py`
- Modify: `src/nova_navigator/vfs/scheme_registry.py`
- Modify: `tests/vfs/test_scheme_registry.py`

- [ ] **Step 3.1: Write failing test for `azure://` scheme**

Append to `tests/vfs/test_scheme_registry.py`:

```python
from unittest.mock import MagicMock, patch


def test_vfspath_from_uri_azure_scheme() -> None:
    from nova_navigator.vfs.filesystems.azure import AzureFilesystem
    register_common_schemes()
    mock_client = MagicMock()
    with patch("nova_navigator.vfs.filesystems.azure.ContainerClient", return_value=mock_client), \
         patch("nova_navigator.vfs.filesystems.azure.DefaultAzureCredential"):
        vpath = vfspath_from_uri("azure://myaccount.blob.core.windows.net/mycontainer/folder/file.txt")
    assert isinstance(vpath.filesystem, AzureFilesystem)
    assert str(vpath.path) == "/folder/file.txt"


def test_vfspath_from_uri_azure_root() -> None:
    from nova_navigator.vfs.filesystems.azure import AzureFilesystem
    register_common_schemes()
    with patch("nova_navigator.vfs.filesystems.azure.ContainerClient"), \
         patch("nova_navigator.vfs.filesystems.azure.DefaultAzureCredential"):
        vpath = vfspath_from_uri("azure://myaccount.blob.core.windows.net/mycontainer/")
    assert isinstance(vpath.filesystem, AzureFilesystem)
    assert str(vpath.path) == "/"
```

- [ ] **Step 3.2: Run test to verify it fails**

```
uv run pytest tests/vfs/test_scheme_registry.py -v -k azure
```
Expected: FAIL — `azure` scheme not yet registered.

- [ ] **Step 3.3: Export `AzureFilesystem` from the package**

Read `src/nova_navigator/vfs/filesystems/__init__.py` and add the export:

```python
from .azure import AzureFilesystem
```

alongside the existing exports (e.g. `LocalFilesystem`, `SSHFilesystem`).

- [ ] **Step 3.4: Register the `azure://` scheme**

In `src/nova_navigator/vfs/scheme_registry.py`, add after `local_uri`:

```python
def azure_uri(path: str, netloc: str | None) -> VPath:
    """Resolve an ``azure://`` URI to a :class:`~nova_navigator.vfs.VPath`.

    URI format: ``azure://<account-hostname>/<container>/<blob-path>``
    """
    from nova_navigator.vfs.filesystems.azure import AzureFilesystem

    account_url = f"https://{netloc}"
    # path is like "/mycontainer/rest/of/path" or "/mycontainer/"
    parts = path.lstrip("/").split("/", 1)
    container = parts[0]
    blob_path = "/" + parts[1] if len(parts) > 1 else "/"
    if blob_path == "//":
        blob_path = "/"
    fs = AzureFilesystem(account_url, container)
    return fs.path(blob_path)
```

Then add `SCHEME_REGISTRY.register_scheme("azure", azure_uri)` inside `register_common_schemes()`.

- [ ] **Step 3.5: Run tests to verify they pass**

```
uv run pytest tests/vfs/test_scheme_registry.py -v
```
Expected: all tests PASS.

- [ ] **Step 3.6: Coding-guideline follow-up checklist**

- [ ] `uv run ruff check src/nova_navigator/vfs/scheme_registry.py src/nova_navigator/vfs/filesystems/__init__.py` — zero errors
- [ ] `uv run ty check src/nova_navigator/vfs/scheme_registry.py` — zero errors

---

## Task 4: Integration tests against Azurite

**Files:**
- Create: `tests/vfs/integration/test_azure_filesystem_integration.py`

- [ ] **Step 4.1: Create the integration test file**

Create `tests/vfs/integration/test_azure_filesystem_integration.py`:

```python
"""Integration tests for AzureFilesystem against a local Azurite emulator.

Skipped automatically when `azurite-blob` is not on PATH.
Run Azurite manually or install with: npm install -g azurite
"""

from __future__ import annotations

import shutil
import subprocess
import time

import pytest
from azure.storage.blob import BlobServiceClient

from nova_navigator.vfs.filesystems.azure import AzureFilesystem


# ── Azurite availability ──────────────────────────────────────────────────────

def _azurite_available() -> bool:
    return shutil.which("azurite-blob") is not None


_AZURITE_URL = "http://127.0.0.1:10000/devstoreaccount1"
_AZURITE_CONN_STR = (
    "DefaultEndpointsProtocol=http;"
    "AccountName=devstoreaccount1;"
    "AccountKey=Eby8vdM02xNOcqFlqUwJPLlmEtlCDXJ1OUzFT50uSRZ6IFsuFq2UVErCz4I6tq/K1SZFPTOtr/KBHBeksoGMGw==;"
    "BlobEndpoint=http://127.0.0.1:10000/devstoreaccount1;"
)
_CONTAINER = "testcontainer"


@pytest.fixture(scope="session")
def azurite_process():
    """Start azurite-blob on port 10000; skip session if not available."""
    if not _azurite_available():
        pytest.skip("azurite-blob not on PATH — skipping integration tests")
    proc = subprocess.Popen(
        ["azurite-blob", "--blobPort", "10000", "--blobHost", "127.0.0.1", "--silent"],
        stdout=subprocess.DEVNULL,
        stderr=subprocess.DEVNULL,
    )
    time.sleep(1.5)  # wait for emulator to start
    yield proc
    proc.terminate()
    proc.wait()


@pytest.fixture(scope="session")
def container_client(azurite_process: subprocess.Popen):  # type: ignore[type-arg]
    """Create the test container and return a ContainerClient."""
    svc = BlobServiceClient.from_connection_string(_AZURITE_CONN_STR)
    try:
        svc.create_container(_CONTAINER)
    except Exception:
        pass  # already exists from a previous run
    return svc.get_container_client(_CONTAINER)


@pytest.fixture
def fs(container_client) -> AzureFilesystem:  # type: ignore[no-untyped-def]
    """Return an AzureFilesystem backed by the Azurite container."""
    # Delete all blobs to start each test with a clean container
    for blob in container_client.list_blobs():
        container_client.get_blob_client(blob.name).delete_blob()
    return AzureFilesystem(_AZURITE_URL, _CONTAINER, client=container_client)


# ── tests ─────────────────────────────────────────────────────────────────────

def test_list_empty_container(fs: AzureFilesystem) -> None:
    result = fs.iterdir(fs.path("/"))
    assert result == []


def test_write_read_roundtrip(fs: AzureFilesystem) -> None:
    writer = fs.write(fs.path("/hello.txt"))
    writer.write(b"hello world")
    writer.close()

    reader = fs.read(fs.path("/hello.txt"))
    data = reader.read(1024)
    reader.close()
    assert data == b"hello world"


def test_stat_after_write(fs: AzureFilesystem) -> None:
    writer = fs.write(fs.path("/sized.txt"))
    writer.write(b"abc")
    writer.close()

    s = fs.stat(fs.path("/sized.txt"))
    assert s.size == 3
    assert s.modified > 0


def test_remove(fs: AzureFilesystem) -> None:
    writer = fs.write(fs.path("/todelete.txt"))
    writer.write(b"x")
    writer.close()

    fs.remove(fs.path("/todelete.txt"))

    with pytest.raises(FileNotFoundError):
        fs.stat(fs.path("/todelete.txt"))


def test_mkdir_and_iterdir(fs: AzureFilesystem) -> None:
    fs.mkdir(fs.path("/mydir"))
    root_entries = fs.iterdir(fs.path("/"))
    paths = [str(p.path) for p in root_entries]
    assert "/mydir" in paths


def test_rename(fs: AzureFilesystem) -> None:
    writer = fs.write(fs.path("/original.txt"))
    writer.write(b"content")
    writer.close()

    fs.rename(fs.path("/original.txt"), fs.path("/renamed.txt"))

    with pytest.raises(FileNotFoundError):
        fs.stat(fs.path("/original.txt"))

    s = fs.stat(fs.path("/renamed.txt"))
    assert s.size == len(b"content")
```

- [ ] **Step 4.2: Run integration tests (skip if Azurite not installed)**

```
uv run pytest tests/vfs/integration/test_azure_filesystem_integration.py -v
```
Expected: either all PASS (if Azurite installed) or all SKIP (if not).

If Azurite is not installed, verify skip message appears: `SKIPPED — azurite-blob not on PATH`.

---

## Task 5: Full QA pass

- [ ] **Step 5.1: Run full test suite**

```
uv run pytest -v
```
Expected: all existing tests PASS; new tests PASS (or SKIP for Azurite).

- [ ] **Step 5.2: Run QA checks**

```
uv run qa
```
Expected: zero lint errors, zero type errors, all tests pass.
