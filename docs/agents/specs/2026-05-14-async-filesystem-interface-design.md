# Async Filesystem Interface Design

**Date:** 2026-05-14
**Status:** Proposal — not yet implemented

---

## Motivation

The current `Filesystem` ABC is fully synchronous.
`iterdir(path) -> list[VPath]` must return a complete list before the caller can see any entry.
`DirectoryBrowser.update()` calls this directly on the Textual event loop thread (no `asyncio.to_thread`), which blocks rendering until every entry has been fetched.

For large directories this causes visible freezes:

- **Local:** 100,000-entry directory — milliseconds, tolerable but still blocks.
- **SSH:** one `listdir_attr` RPC over a high-latency link — 2–10 s freeze.
- **Azure Blob Storage:** `walk_blobs` paginates at 5,000 items/page.
  A 90,000-entry container requires 18 sequential HTTP calls (~200 ms each = ~3.6 s) before the first item appears.

In addition, the watchdog integration is hardcoded to `LocalFilesystem` in `DirectoryBrowser`.
SSH and Azure have no equivalent; users must press F5 manually.

This spec replaces the synchronous `iterdir` with an async streaming interface, makes capabilities a runtime instance property, and moves directory watching (including polling) fully into the filesystem layer.

---

## Current State

### `Filesystem` ABC — `src/nova_navigator/vfs/filesystem.py`

Key signatures that change:

```python
# current — blocking, returns full list
def iterdir(self, path: VPath) -> list[VPath]: ...
def stat(self, path: VPath) -> Stat: ...
def read(self, path: VPath) -> StreamReaderLike: ...
def write(self, path: VPath) -> StreamWriterLike: ...
def refresh(self, path: VPath | None = None) -> None: ...
```

No capability flags exist.
No watch interface exists in the ABC (only hardcoded watchdog in the browser).

### `VPath` — `src/nova_navigator/vfs/vpath.py`

- `_stat: Stat | None = None` — lazily populated on first `.stat` access via `_ensure_stat()`.
- `.iterdir()` delegates to `self._filesystem.iterdir(self)`.
- `.walk()` iterates recursively using `.iterdir()` and `.stat`.

### `DirectoryBrowser` — `src/nova_navigator/widgets/directory_browser.py`

- `update(WhatChanged.ALL)` calls `self._path.iterdir()` synchronously, blocking the event loop.
- Watchdog observer is started in `on_mount`; schedule/unschedule is called directly in `set_path` for `LocalFilesystem` only.

### `LocalFilesystem` — `src/nova_navigator/vfs/filesystems/local.py`

- `iterdir` uses `os.listdir` (no stat data bundled).
  Every item subsequently triggers a separate `os.stat` call via `VPath._ensure_stat`.

### `AzureFilesystem` — `src/nova_navigator/vfs/filesystems/azure.py`

- `iterdir` drains the full `walk_blobs` lazy iterator into a list, blocking until all pages arrive.
- Stats are pre-populated from `BlobProperties` to avoid a separate HTTP call per item.

### Settings — `src/nova_navigator/config/settings.py`

```python
@dataclass
class NetworkSettings(BaseModel):
    ssh_timeout: int = ...
    proxy: str = ...
```

No watch-poll interval settings exist today.

---

## Proposed Design

### 1. `FilesystemCapabilities`

Capabilities are exposed as an **instance property**, not a `ClassVar`.
This is necessary because capabilities can vary at runtime:

- `is_same_device` differs across mount points on the same `LocalFilesystem`.
- SSH `watch` depends on whether the remote server supports inotify extensions.
- Azure `streaming_iterdir` could depend on which API version is active.
- NTFS-via-Samba has no symlinks even though the class is `LocalFilesystem`.

```python
@dataclass(frozen=True)
class FilesystemCapabilities:
    """Runtime capabilities of a filesystem instance."""

    streaming_iterdir: bool = False
    """True if iterdir yields items incrementally before all entries are ready.

    False means all items arrive in a single burst (e.g. SSH listdir_attr RPC).
    The browser uses this flag to decide whether to show a spinner.
    """

    watch: bool = False
    """True if the filesystem can notify on directory changes.

    The mechanism (inotify, kqueue, polling) is an implementation detail.
    """

    symlinks: bool = False
    """True if this filesystem instance supports symbolic links."""

    permissions: bool = False
    """True if this filesystem instance exposes POSIX permission bits."""
```

Added to the `Filesystem` ABC as:

```python
@property
def capabilities(self) -> FilesystemCapabilities:
    """Return the runtime capabilities of this filesystem instance."""
    return FilesystemCapabilities()
```

Default implementation returns all-False.
Subclasses override to return accurate values.

`is_same_device` stays as an abstract method — it is path-scoped, not a scalar flag.

---

### 2. `iterdir` — async streaming with bundled stat

```python
@abstractmethod
async def iterdir(
    self,
    path: VPath,
    *,
    cancel: asyncio.Event | None = None,
) -> AsyncIterator[VPath]:
    """Yield directory entries as they arrive, with stat pre-populated.

    Callers iterate with `async for vpath in fs.iterdir(path)`.
    Each yielded VPath has `_stat` already set from data bundled in the
    listing response; no separate stat() call is needed for the listing.

    The optional `cancel` event lets callers abort a slow listing.
    Implementations must check `cancel.is_set()` between batches and stop
    iteration cleanly (closing open network connections or file handles).
    """
```

**Why `AsyncIterator` not `AsyncGenerator`?**
`AsyncIterator` is the correct return type annotation for methods that use `yield` inside an `async def`.
Callers type-annotate as `AsyncIterator[VPath]`.

**Why `cancel: asyncio.Event` not `asyncio.CancelledError`?**
Python task cancellation is coarse-grained and can interfere with Textual's own event loop machinery.
An explicit event lets the filesystem do clean shutdown (closing HTTP sessions, SFTP channels) before exiting.

**Per-backend implementation:**

| Filesystem | Implementation |
|---|---|
| `LocalFilesystem` | Wraps `os.scandir()` generator. Each `DirEntry` is packed into a `VPath` with `_stat` populated from `DirEntry.stat()` (single syscall, no extra `stat`). Yields each entry immediately. `streaming_iterdir = True`. |
| `SSHFilesystem` | Calls `sftp.listdir_attr()` via `asyncio.to_thread` (single RPC). After the RPC returns, yields all entries in a tight loop. `streaming_iterdir = False` (no intermediate visibility). |
| `AzureFilesystem` | Runs `walk_blobs` in `asyncio.to_thread`, yielding a batch of `VPath` after each SDK page (5,000 items). `BlobProperties` stats are pre-populated. Checks `cancel` between pages. `streaming_iterdir = True`. |
| `ArchiveFilesystem` | Reads archive index in one pass; yields all entries in a tight burst. `streaming_iterdir = False`. |

---

### 3. `stat` — stays async, still required

`iterdir` bundles stat as an optimisation for the listing case.
An explicit `stat()` is still needed for:

- Overwrite dialog — target may have changed since the last listing.
- Stat after a write — verify size and mtime.
- Symlink resolution — stat the link target, not the link.
- Any context where a `VPath` was constructed directly, not via `iterdir`.

```python
@abstractmethod
async def stat(self, path: VPath) -> Stat:
    """Return fresh stat data for path, bypassing any cached value."""
```

`VPath._ensure_stat` becomes async accordingly:

```python
async def _ensure_stat(self) -> None:
    if self._stat is None:
        self._stat = await self._filesystem.stat(self)
```

`VPath.stat` becomes an async property (or callers call `await vpath.refresh_stat()` explicitly to get a fresh value while `vpath.stat` still returns cached):

**Preferred approach — keep `vpath.stat` sync-cached, add `vpath.fresh_stat()` async:**

```python
@property
def stat(self) -> Stat:
    """Return cached stat. Raises if stat has not been populated yet."""
    if self._stat is None:
        raise RuntimeError("stat not populated; call await vpath.fresh_stat() first")
    return self._stat

async def fresh_stat(self) -> Stat:
    """Fetch and cache a fresh stat from the filesystem."""
    self._stat = await self._filesystem.stat(self)
    return self._stat
```

Items coming out of `iterdir` always have `_stat` set, so `vpath.stat` is always safe for browser use.
The overwrite dialog and other callers that need fresh data call `await vpath.fresh_stat()`.

---

### 4. `watch` — async context manager, polling as implementation detail

```python
@abstractmethod
async def watch(
    self,
    path: VPath,
    callback: Callable[[VPath], Awaitable[None]],
) -> AsyncContextManager[None]:
    """Return an async context manager that invokes callback when path changes.

    async with fs.watch(path, on_change):
        ...  # callback fires whenever directory content may have changed

    The mechanism is an implementation detail:
    - LocalFilesystem uses watchdog (inotify/kqueue).
    - SSHFilesystem polls via periodic listdir_attr diffs.
    - AzureFilesystem polls via periodic list_blobs diffs.
    Filesystems that cannot watch return a no-op context manager and
    advertise capabilities.watch = False.

    The callback receives the path that changed.
    It is called from within the filesystem's async context and must be
    awaitable (async def).
    """
```

Default no-op implementation in the ABC (for filesystems that don't support watching):

```python
@asynccontextmanager
async def watch(
    self,
    path: VPath,
    callback: Callable[[VPath], Awaitable[None]],
) -> AsyncIterator[None]:
    yield  # no-op
```

**`DirectoryBrowser` change:**
The browser calls `await self._path.filesystem.watch(self._path, self._on_directory_changed)` inside a `@work` coroutine.
It no longer contains any `isinstance(fs, LocalFilesystem)` checks.
The watchdog observer setup moves entirely into `LocalFilesystem.watch`.

---

### 5. Watch polling interval — user-configurable via settings

Polling-based watch implementations (SSH, Azure) must use an interval from settings, not a hardcoded constant.

Add to `src/nova_navigator/config/settings.py`:

```python
@dataclass
class WatchSettings(BaseModel):
    """Directory watch settings."""

    ssh_poll_interval: float = field_comment(
        5.0,
        "Seconds between directory refresh polls for SSH filesystems. "
        "Lower values mean faster change detection but more network traffic.",
    )
    azure_poll_interval: float = field_comment(
        30.0,
        "Seconds between directory refresh polls for Azure Blob Storage. "
        "Lower values mean faster change detection but more API calls.",
    )
```

And add `watch: WatchSettings` to `Settings`:

```python
@dataclass
class Settings(BaseModel, ModelConfig):
    CONFIG_NAME: ClassVar[str] = "settings"
    general: GeneralSettings = field(default_factory=GeneralSettings)
    network: NetworkSettings = field(default_factory=NetworkSettings)
    watch: WatchSettings = field(default_factory=WatchSettings)
```

The interval is read at watch startup time:

```python
# inside SSHFilesystem.watch
interval = conf_.settings.watch.ssh_poll_interval
```

This means the interval is applied for new watch sessions; a running watch is not hot-reloaded when the setting changes.
Hot-reload can be added later if needed.

---

### 6. `read` / `write` — async context managers

The current `StreamReaderLike` / `StreamWriterLike` protocols are replaced with async context managers that yield async binary IO objects.
This guarantees resource cleanup and enables truly async streaming reads (important for Azure, where `download_blob` is already async).

```python
@abstractmethod
def read(self, path: VPath) -> AsyncContextManager[AsyncBinaryIO]:
    """Return an async context manager yielding an async binary reader."""

@abstractmethod
def write(self, path: VPath) -> AsyncContextManager[AsyncBinaryIO]:
    """Return an async context manager yielding an async binary writer."""
```

Usage:

```python
async with fs.read(vpath) as reader:
    data = await reader.read(4096)
```

`AsyncBinaryIO` is a `Protocol`:

```python
class AsyncBinaryIO(Protocol):
    async def read(self, size: int = -1) -> bytes: ...
    async def write(self, data: bytes) -> int: ...
    async def seek(self, offset: int, whence: int = 0) -> int: ...
```

The existing `_BlobReader` / `_BlobWriter` classes in `AzureFilesystem` become proper `@asynccontextmanager` implementations.

---

### 7. `refresh` — unchanged

`refresh(path | None)` remains sync and is a no-op for filesystems without caching.
The async transition does not require changes here.

---

### 8. `DirectoryBrowser` changes

The key change is `update(WhatChanged.ALL)` must no longer block the event loop.

New pattern using a Textual `@work` coroutine:

```python
_BATCH_SIZE: int = 200  # entries to accumulate before re-rendering

@work(exclusive=True)
async def _load_directory(self) -> None:
    """Load directory contents incrementally into _all_items."""
    cancel = asyncio.Event()
    self._load_cancel = cancel
    self._all_items = []

    try:
        batch: list[VPath] = []
        async for vpath in self._path.filesystem.iterdir(self._path, cancel=cancel):
            batch.append(vpath)
            if len(batch) >= _BATCH_SIZE:
                self._all_items.extend(batch)
                batch.clear()
                self._apply_sort_and_filter()
                self.refresh()
        self._all_items.extend(batch)
    except Exception:
        # path disappeared, permission denied, network error, etc.
        self._all_items = []

    self._apply_sort_and_filter()
    self.refresh()
```

`update(WhatChanged.ALL)` calls `self._load_directory()` (which creates a new worker, cancelling any previous one via `exclusive=True`).
`update(WhatChanged.SORTING)` and `update(WhatChanged.FILTERING)` do not re-load; they only call `_apply_sort_and_filter()` and `refresh()`.

`_apply_sort_and_filter` is the extracted sort+filter step from the current `update` body.

**Loading indicator:**
When `capabilities.streaming_iterdir` is False, the browser shows a spinner until the burst arrives.
When it is True, items appear progressively and no spinner is needed.

**Watch integration:**
`set_path` starts a watch worker:

```python
@work(exclusive=True, group="watch")
async def _start_watch(self) -> None:
    async with self._path.filesystem.watch(self._path, self._on_directory_changed):
        await asyncio.Event().wait()  # run until cancelled
```

`_on_directory_changed` simply calls `self.update(WhatChanged.ALL)`.
The old watchdog observer setup in `on_mount` / `set_path` is removed entirely.

---

## File-by-file change summary

| File | Change |
|---|---|
| `src/nova_navigator/vfs/filesystem.py` | Add `FilesystemCapabilities`; add `capabilities` property; change `iterdir` to `async def ... -> AsyncIterator[VPath]` with `cancel` param; change `stat` to `async def`; change `read`/`write` to return `AsyncContextManager`; add abstract `watch` with default no-op |
| `src/nova_navigator/vfs/vpath.py` | Add `fresh_stat() -> Awaitable[Stat]`; guard `stat` property to raise if unpopulated; make `iterdir()` an `async def` delegating to filesystem |
| `src/nova_navigator/vfs/types.py` | Add `AsyncBinaryIO` protocol |
| `src/nova_navigator/vfs/filesystems/local.py` | Rewrite `iterdir` as `async def` using `os.scandir`; override `capabilities`; implement `watch` using watchdog |
| `src/nova_navigator/vfs/filesystems/ssh.py` | Rewrite `iterdir` as `async def` using `asyncio.to_thread`; implement `watch` with polling |
| `src/nova_navigator/vfs/filesystems/azure.py` | Rewrite `iterdir` as `async def` yielding per-page batches; implement `watch` with polling; use `conf_.settings.watch.azure_poll_interval` |
| `src/nova_navigator/vfs/filesystems/archive.py` | Rewrite `iterdir` as `async def` single-burst |
| `src/nova_navigator/widgets/directory_browser.py` | Replace sync `update(ALL)` with `_load_directory` worker; replace watchdog observer with `_start_watch` worker |
| `src/nova_navigator/config/settings.py` | Add `WatchSettings`; add `watch` field to `Settings` |
| `src/nova_navigator/filemanager/tasks.py` | Update callers of `iterdir` and `stat` to use `async for` and `await` |
| `src/nova_navigator/operations/` | Update `copy_file`, `erase` etc. to `await` stat and read/write |

---

## Migration notes

### Existing filesystems

All concrete `Filesystem` subclasses must implement the new signatures.
The type checker (`ty check`) will catch any missed overrides.

The default `capabilities` property returns all-False — safe default, opt-in to features.

`watch` has a working no-op default in the ABC, so subclasses that don't support watching compile without changes.
They just won't fire change notifications.

### VPath.walk

`walk()` uses `iterdir()` and `stat` internally.
It must be rewritten as `async def walk()` yielding `AsyncIterator[tuple[VPath, list[VPath], list[VPath]]]`.
Callers of `walk()` (archive write, recursive copy) must switch to `async for`.

### StreamReaderLike / StreamWriterLike

The old protocols should be retained as deprecated aliases until all callers have been migrated, then removed.

---

## Out of scope

- Hot-reloading the poll interval while a watch is running.
- Exposing per-filesystem capability differences within a single `LocalFilesystem` instance based on mount-point-level features (e.g. btrfs vs ext4 reflink support) — this can be added later via path-scoped methods if needed.
- Azure event-grid based push notifications — polling is sufficient for the current use case.
