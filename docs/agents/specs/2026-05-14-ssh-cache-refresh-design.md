# SSH Directory Cache & Refresh Design

## Problem

`SSHFilesystem._dir_stat()` is decorated with `@lru_cache(maxsize=64)`.
The cache is never invalidated.
After any mutation (copy, delete, rename, mkdir) the directory listing shown in `DirectoryBrowser` remains stale until the process restarts.
Additionally, navigating into a directory can show outdated contents if the remote side changed since the last visit.

## Goals

- Fix stale listings after local mutations.
- Provide explicit manual refresh (`Ctrl+R`).
- Always show fresh contents when entering a directory.
- Lay groundwork for future polling-based auto-refresh.

## Non-goals

- Auto-refresh via SSH inotify (server-side dependency, deferred).
- Polling timer implementation (stub only in this iteration).

---

## Design

### 1. `Filesystem` ABC — new `refresh()` method

Add one no-op method to `vfs/filesystem.py`:

```python
def refresh(self, path: VPath | None = None) -> None:  # noqa: B027
    """Discard any cached data for *path* (or all cached data if *path* is None).

    The next read after this call will fetch fresh data from the source.
    The default implementation is a no-op for filesystems without caching
    (LocalFilesystem, ArchiveFilesystem, etc.).
    """
```

Signature: `path=None` means "refresh everything"; a specific path means "refresh that directory only".
The `# noqa: B027` suppresses the ruff warning for a non-abstract method with no body in an ABC (same pattern already used for `copy_stat`).

### 2. `SSHFilesystem` — dict-based cache + `refresh()` override

**Replace `@lru_cache` with an instance dict.**

In `__init__`, add:
```python
self._stat_cache: dict[tuple[str, bool], dict[str, StatEntry]] = {}
```

Remove `from functools import lru_cache` and the `@lru_cache(maxsize=64)  # noqa: B019` decorator.
Replace `_dir_stat` body with a manual cache check:

```python
def _dir_stat(self, path: str, follow_symlinks: bool) -> dict[str, StatEntry]:
    key = (path, follow_symlinks)
    if key not in self._stat_cache:
        command = _STAT_COMMAND_FOLLOW_LINKS if follow_symlinks else _STAT_COMMAND
        _, stdout, _ = self._ssh_client.exec_command(f"cd {path} && {command}")
        self._stat_cache[key] = _parse_stat_output(stdout.read().decode())
    return self._stat_cache[key]
```

**`refresh()` override:**

```python
@override
def refresh(self, path: VPath | None = None) -> None:
    if path is None:
        self._stat_cache.clear()
    else:
        p = str(path)
        self._stat_cache.pop((p, True), None)
        self._stat_cache.pop((p, False), None)
```

**Post-mutation cache eviction.**

Each mutating method calls `self.refresh(parent)` after the operation succeeds:

| Method | Evicts |
|--------|--------|
| `remove(path)` | `path.parent` |
| `rename(src, dst)` | `src.parent`, `dst.parent` |
| `mkdir(path)` | `path.parent` |
| `rmdir(path)` | `path.parent` |
| `write(path)` close | `path.parent` (via `_PipelinedWriter`) |

**`_PipelinedWriter` wrapper** (private inner class of `SSHFilesystem`):

`write()` currently returns a bare `paramiko.SFTPFile`.
To intercept `close()` for cache eviction (and to call `set_pipelined(True)`), a thin wrapper is used:

```python
class _PipelinedWriter:
    def __init__(self, f: paramiko.SFTPFile, on_close: Callable[[], None]) -> None:
        self._f = f
        self._on_close = on_close

    def write(self, data: bytes) -> int:
        return self._f.write(data)

    def close(self) -> None:
        self._f.close()
        self._on_close()
```

`write()` becomes:
```python
@override
def write(self, path: VPath) -> StreamWriterLike:
    f = self._sftp_client.open(path.path.as_posix(), "wb")
    f.set_pipelined(True)
    return self._PipelinedWriter(f, lambda: self.refresh(path.parent))
```

Note: `set_pipelined(True)` (previously landed) is folded into this wrapper, replacing the earlier standalone change.

### 3. `DirectoryBrowser` — explicit refresh, set_path eviction, polling stubs

**New `Ctrl+R` binding:**

```python
Binding("ctrl+r", "reload", "Reload", show=True),
```

**New `action_reload()`:**

```python
def action_reload(self) -> None:
    self._path.filesystem.refresh()      # full cache clear
    self.update(self.WhatChanged.ALL)
```

**`set_path()` — evict destination before loading:**

Add one line before the existing `self.update(self.WhatChanged.ALL)` call:
```python
path.filesystem.refresh(path)
```

**Polling stubs** — two no-op methods, called from `set_path()`:

```python
def _start_polling(self) -> None:
    pass  # TODO: start per-directory polling for non-local filesystems

def _stop_polling(self) -> None:
    pass  # TODO: stop polling timer
```

`_stop_polling()` is called at the top of `set_path()` alongside the existing `self._observer.unschedule` block.
`_start_polling()` is called at the bottom of `set_path()` alongside the existing `self._observer.schedule` block.
Note: `DirectoryBrowser` has no `on_unmount` — the watchdog observer is also never explicitly stopped. The polling stubs follow the same pattern.

---

## File change summary

| File | Change |
|------|--------|
| `vfs/filesystem.py` | Add `refresh()` no-op method |
| `vfs/filesystems/ssh.py` | Replace `lru_cache` with dict cache; add `_PipelinedWriter`; add `refresh()` override; evict in mutating methods |
| `widgets/directory_browser.py` | Add `Ctrl+R` binding + `action_reload()`; call `refresh(path)` in `set_path()`; add `_start_polling` / `_stop_polling` stubs |

---

## Testing

- Unit test: after `remove()`, `rename()`, `mkdir()`, `rmdir()`, assert `_stat_cache` no longer contains the parent directory entry.
- Unit test: `refresh(path)` evicts only that path's two keys; `refresh(None)` clears all.
- Unit test: `refresh()` on `LocalFilesystem` does not raise.
- Integration: `action_reload()` calls `filesystem.refresh()` then `update(ALL)`.
- Integration: `set_path(new_path)` calls `filesystem.refresh(new_path)` before `update(ALL)`.
