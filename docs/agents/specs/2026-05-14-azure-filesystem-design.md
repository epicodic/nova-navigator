# Azure Filesystem Design

Date: 2026-05-14

## Overview

Implement `AzureFilesystem`, a `Filesystem` subclass that exposes one Azure Blob Storage container as a navigable virtual filesystem.
The container is the root (`/`); blobs map to file paths; virtual directories are synthesised from common blob-name prefixes.
Authentication uses `DefaultAzureCredential` exclusively — no secrets are stored in config.
An `azure://` URI scheme is registered so users can open a container directly from a path input.

---

## Section 1 — `AzureFilesystem` class

### File

`src/nova_navigator/vfs/filesystems/azure.py`

### Constructor

```python
AzureFilesystem(account_url: str, container: str, *, client: ContainerClient | None = None)
```

- `account_url`: full Azure Storage service URL, e.g. `https://myaccount.blob.core.windows.net`.
- `container`: blob container name.
- `client`: optional pre-constructed `ContainerClient` (used in tests to inject a mock).
  When `None`, a real `ContainerClient` is built from `account_url` + `container` using `DefaultAzureCredential`.

### Path model

- The VFS root is `/`.
- A blob named `foo/bar/baz.txt` maps to VPath `/foo/bar/baz.txt`.
- Virtual directories are prefixes inferred from `list_blobs(..., delimiter="/")`.
  They have no physical object in Azure; `stat()` returns a synthesised `Stat(is_directory=True)`.
- Blob names are stored without a leading slash; path-to-blob-name conversion strips the leading `/`.

### Method mapping

| `Filesystem` method | Implementation |
|---|---|
| `cwd()` | returns `self.path("/")` |
| `root()` | returns `self.path("/")` |
| `home()` | returns `self.path("/")` |
| `parent(path)` | `PurePosixPath(path.path).parent` as string, wrapped in `self.path(...)` |
| `is_same_device(p1, p2)` | `True` — both paths always belong to the same container/filesystem instance |
| `iterdir(path)` | `client.list_blobs(name_starts_with=prefix, delimiter="/")` — yields both `BlobProperties` items (files) and `BlobPrefix` items (virtual dirs) |
| `stat(path)` | blobs: `client.get_blob_client(name).get_blob_properties()` → `Stat`; virtual dirs: synthesised `Stat(is_directory=True, size=0)` |
| `read(path)` | `client.get_blob_client(name).download_blob()` — returns a `StorageStreamDownloader` which satisfies `StreamReaderLike` (has `.read(size)` and `.close()`) |
| `write(path)` | returns a `_BlobWriter` inner class that accumulates data in a `BytesIO` buffer, then calls `upload_blob(overwrite=True)` on `.close()` |
| `remove(path)` | `client.get_blob_client(name).delete_blob()` |
| `rmdir(path)` | delete the `.keep` marker blob for `path`; raise `OSError(ENOTEMPTY)` if any other blobs share the prefix (directory not empty) |
| `mkdir(path)` | upload a zero-byte marker blob `<path>/.keep`; raise `FileExistsError` if marker already exists |
| `rename(src, dst)` | copy blob to new name via `start_copy_from_url`, then delete source |
| `copy_stat(path, stat)` | no-op — Azure Blob does not support POSIX file attributes |
| `refresh(path)` | no-op — no local cache is maintained |
| `readlink(path)` | `raise OSError(errno.EINVAL, "Not a symbolic link", str(path.path))` — Azure Blob has no symlinks |

### `Stat` field mapping from `BlobProperties`

| `Stat` field | Source |
|---|---|
| `size` | `BlobProperties.size` |
| `modified` | `BlobProperties.last_modified.timestamp()` |
| `mode` | `-1` (not available) |
| `is_hidden` | `False` |
| `is_directory` | `False` for blobs, `True` for virtual-dir prefixes |
| `is_executable` | `False` |
| `is_symlink` | `False` |
| `is_broken_symlink` | `False` |

### Error handling

- `FileNotFoundError`: raised when a blob does not exist (caught from `ResourceNotFoundError` in the Azure SDK).
- `PermissionError`: raised when `AuthenticationError` or `AuthorizationError` is received from the SDK.
- All other `HttpResponseError` exceptions are re-raised as `OSError`.

---

## Section 2 — `azure://` URI scheme

### URI format

```
azure://<account-hostname>/<container>/<blob-path>
```

Examples:
```
azure://myaccount.blob.core.windows.net/mycontainer/
azure://myaccount.blob.core.windows.net/mycontainer/folder/file.txt
```

### Handler

Registered in `src/nova_navigator/vfs/scheme_registry.py` alongside the existing `file` and `ssh` handlers.

```python
def azure_uri(path: str, netloc: str | None) -> VPath:
    account_url = f"https://{netloc}"
    # path is like "/mycontainer/rest/of/path"
    parts = path.lstrip("/").split("/", 1)
    container = parts[0]
    blob_path = "/" + parts[1] if len(parts) > 1 else "/"
    fs = AzureFilesystem(account_url, container)
    return fs.path(blob_path)

SCHEME_REGISTRY.register_scheme("azure", azure_uri)
```

Registration is added inside `register_common_schemes()`.

---

## Section 3 — Testing

### Unit tests

File: `tests/vfs/test_azure_filesystem.py`

Pattern mirrors `tests/vfs/test_ssh_filesystem.py`.

**Fixture helper:**
```python
def _make_fs() -> tuple[AzureFilesystem, MagicMock]:
    mock_client = MagicMock(spec=ContainerClient)
    fs = AzureFilesystem("https://test.blob.core.windows.net", "testcontainer", client=mock_client)
    return fs, mock_client
```

**Test cases:**
- `test_root_returns_slash` — `cwd()`, `root()`, `home()` all return `/`
- `test_iterdir_returns_blobs_and_prefixes` — mock returns one `BlobProperties` and one `BlobPrefix`; assert two VPaths returned with correct names
- `test_iterdir_root` — prefix passed to SDK is empty string (not `"/"`)
- `test_stat_blob` — mock `get_blob_properties` returns size+mtime; assert `Stat` fields
- `test_stat_virtual_dir` — synthesised stat has `is_directory=True`, `size=0`
- `test_stat_missing_blob_raises` — `ResourceNotFoundError` from SDK → `FileNotFoundError`
- `test_read` — mock `download_blob()` returns object; verify it is returned from `read()`
- `test_write_uploads_on_close` — call `write()`, call `.write(b"data")`, call `.close()`; assert `upload_blob` called with correct data
- `test_remove` — assert `delete_blob` called with correct blob name
- `test_rename` — assert copy then delete called in order
- `test_mkdir_creates_marker` — assert `.keep` blob uploaded
- `test_mkdir_raises_if_exists` — mock raises `ResourceExistsError`; assert `FileExistsError`
- `test_readlink_raises` — assert `OSError` with `EINVAL`
- `test_parent` — `parent("/foo/bar")` == `"/foo"`
- `test_is_same_device` — always `True`

### Integration tests

File: `tests/vfs/integration/test_azure_filesystem_integration.py`

Uses Azurite, Microsoft's official Azure Storage emulator.

**Azurite fixture:**
```python
@pytest.fixture(scope="session")
def azurite():
    """Start Azurite blob service; skip if docker is not available."""
    pytest.importorskip("docker")  # skip if docker Python SDK absent
    # start container on port 10000, yield connection info, stop after session
```

Launch `azurite-blob` CLI if installed (`shutil.which("azurite-blob")`), matching the pattern of `StubSSHServer` — no Docker SDK dependency.

**Integration test cases:**
- `test_list_empty_container`
- `test_write_read_roundtrip` — write bytes, read back, assert equal
- `test_stat_after_write` — size and modified match
- `test_remove` — blob gone after delete
- `test_mkdir_and_iterdir` — marker blob creates virtual dir visible in iterdir
- `test_rename` — blob appears under new name, old name gone

All integration tests are gated with `pytest.mark.skipif(not _azurite_available(), ...)` so they don't fail in environments without Azurite.

---

## Out of scope

- `remote://` URI scheme integration (deferred).
- Plugin/entry-point architecture (deferred).
- SAS token or connection-string authentication (deferred).
- Resumable uploads for large blobs (deferred).
