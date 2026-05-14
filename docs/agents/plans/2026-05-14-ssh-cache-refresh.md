# SSH Cache & Refresh Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use skills:subagent-driven-development (recommended) or skills:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Replace `SSHFilesystem`'s `lru_cache` with a per-instance dict cache, add `Filesystem.refresh()` to the ABC, wire it into all mutation points, and add `Ctrl+R` reload and per-directory eviction on `set_path()` in `DirectoryBrowser`.

**Architecture:** `Filesystem` gets a no-op `refresh(path=None)` method. `SSHFilesystem` overrides it to pop entries from `_stat_cache`. All mutating methods call `self.refresh(parent)` after success. `DirectoryBrowser` calls `filesystem.refresh(path)` before loading a new directory and exposes `action_reload()` bound to `Ctrl+R`. Polling stubs are added but left empty.

**Tech Stack:** Python 3.12, pytest

**Coding Conventions:** `docs/coding_conventions.md` — read before implementing

**Spec:** `docs/agents/specs/2026-05-14-ssh-cache-refresh-design.md`

---

### Task 1: Add `refresh()` to `Filesystem` ABC

**Files:**
- Modify: `src/nova_navigator/vfs/filesystem.py`
- Test: `tests/vfs/test_filesystem_refresh.py`

- [ ] **Step 1: Write the failing test**

Create `tests/vfs/test_filesystem_refresh.py`:

```python
"""Tests for Filesystem.refresh() default no-op."""
from __future__ import annotations

from nova_navigator.vfs.filesystems import LocalFilesystem


def test_refresh_none_is_noop_on_local_filesystem() -> None:
    fs = LocalFilesystem()
    fs.refresh()  # must not raise


def test_refresh_path_is_noop_on_local_filesystem() -> None:
    import os
    fs = LocalFilesystem()
    fs.refresh(fs.path(os.path.expanduser("~")))  # must not raise
```

- [ ] **Step 2: Run test to verify it fails**

```
uv run pytest tests/vfs/test_filesystem_refresh.py -v
```

Expected: `AttributeError` — `refresh` not yet defined.

- [ ] **Step 3: Add `refresh()` to `Filesystem` in `filesystem.py`**

Open `src/nova_navigator/vfs/filesystem.py`.
Add the following method after `copy_stat()` (before `readlink()`):

```python
def refresh(self, path: VPath | None = None) -> None:  # noqa: B027
    """Discard any cached data for *path* (or all cached data if *path* is None).

    The next read after this call will fetch fresh data from the source.
    The default implementation is a no-op for filesystems without caching.
    """
```

- [ ] **Step 4: Run test to verify it passes**

```
uv run pytest tests/vfs/test_filesystem_refresh.py -v
```

Expected: PASS

- [ ] **Step 5: Coding-guideline follow-up checklist**

```
uv run qa
```

- [ ] Conventions file read: `docs/coding_conventions.md`
- [ ] `refresh` uses `X | None` (not `Optional[X]`)
- [ ] No abstract decorator — default no-op matches `copy_stat` pattern
- [ ] `# noqa: B027` suppresses ruff warning for non-abstract empty method in ABC
- [ ] QA passes: zero lint, type-check, and test failures

---

### Task 2: Replace `lru_cache` with dict cache in `SSHFilesystem`

**Files:**
- Modify: `src/nova_navigator/vfs/filesystems/ssh.py`
- Test: `tests/vfs/test_ssh_filesystem.py`

The existing tests call `fs._dir_stat.cache_clear()` in two places — those must be updated as part of this task.

- [ ] **Step 1: Write failing tests for dict-cache behaviour**

Append to `tests/vfs/test_ssh_filesystem.py`:

```python
# ── dict cache ────────────────────────────────────────────────────────────────


def test_dir_stat_caches_result() -> None:
    """Second call with same args must not fire exec_command again."""
    fs, mock_ssh, _ = _make_fs()
    output = _stat_line("f.txt")
    _set_exec_output(mock_ssh, output)
    fs._dir_stat("/home/user", follow_symlinks=False)
    fs._dir_stat("/home/user", follow_symlinks=False)
    assert mock_ssh.exec_command.call_count == 1


def test_refresh_path_evicts_both_follow_symlink_variants() -> None:
    fs, mock_ssh, _ = _make_fs()
    output = _stat_line("f.txt")
    _set_exec_output(mock_ssh, output)
    # warm both cache keys
    fs._dir_stat("/home/user", follow_symlinks=False)
    fs._dir_stat("/home/user", follow_symlinks=True)
    assert mock_ssh.exec_command.call_count == 2
    # evict
    fs.refresh(fs.path("/home/user"))
    # both keys must have been removed
    assert ("/home/user", False) not in fs._stat_cache
    assert ("/home/user", True) not in fs._stat_cache


def test_refresh_none_clears_all_cache_entries() -> None:
    fs, mock_ssh, _ = _make_fs()
    _set_exec_output(mock_ssh, _stat_line("f.txt"))
    fs._dir_stat("/home/user", follow_symlinks=False)
    fs._dir_stat("/tmp", follow_symlinks=False)
    assert len(fs._stat_cache) == 2
    fs.refresh()
    assert len(fs._stat_cache) == 0


def test_refresh_nonexistent_path_is_safe() -> None:
    fs, _, _ = _make_fs()
    fs.refresh(fs.path("/no/such/dir"))  # must not raise
```

- [ ] **Step 2: Run tests to verify they fail**

```
uv run pytest tests/vfs/test_ssh_filesystem.py::test_dir_stat_caches_result \
               tests/vfs/test_ssh_filesystem.py::test_refresh_path_evicts_both_follow_symlink_variants \
               tests/vfs/test_ssh_filesystem.py::test_refresh_none_clears_all_cache_entries \
               tests/vfs/test_ssh_filesystem.py::test_refresh_nonexistent_path_is_safe -v
```

Expected: failures — `_stat_cache` attribute missing and `cache_clear` still in place.

- [ ] **Step 3: Implement dict cache and `refresh()` in `ssh.py`**

Open `src/nova_navigator/vfs/filesystems/ssh.py`.

**3a.** Remove the import:
```python
from functools import lru_cache
```

**3b.** In `__init__`, add after `self._sftp_client = self._ssh_client.open_sftp()`:
```python
self._stat_cache: dict[tuple[str, bool], dict[str, StatEntry]] = {}
```

**3c.** Replace the `_dir_stat` method (remove `@lru_cache(maxsize=64)  # noqa: B019`, rewrite body):
```python
def _dir_stat(self, path: str, follow_symlinks: bool) -> dict[str, StatEntry]:
    key = (path, follow_symlinks)
    if key not in self._stat_cache:
        command = _STAT_COMMAND_FOLLOW_LINKS if follow_symlinks else _STAT_COMMAND
        _, stdout, _ = self._ssh_client.exec_command(f"cd {path} && {command}")
        self._stat_cache[key] = _parse_stat_output(stdout.read().decode())
    return self._stat_cache[key]
```

**3d.** Add `refresh()` override after `_dir_stat`:
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

- [ ] **Step 4: Fix the two existing tests that call `cache_clear()`**

In `tests/vfs/test_ssh_filesystem.py`, find and update:

`test_stat_symlink` — replace:
```python
    fs._dir_stat.cache_clear()
```
with:
```python
    fs.refresh()
```

`test_stat_missing_file_raises_file_not_found` — replace:
```python
    fs._dir_stat.cache_clear()
```
with:
```python
    fs.refresh()
```

- [ ] **Step 5: Run all ssh tests to verify they pass**

```
uv run pytest tests/vfs/test_ssh_filesystem.py -v
```

Expected: all PASS

- [ ] **Step 6: Coding-guideline follow-up checklist**

```
uv run qa
```

- [ ] `_stat_cache` type annotation uses builtin `dict` and `tuple` (not `Dict`, `Tuple`)
- [ ] `refresh()` decorated with `@override`
- [ ] `lru_cache` import removed
- [ ] QA passes: zero failures

---

### Task 3: Add `_PipelinedWriter` and post-mutation cache eviction

**Files:**
- Modify: `src/nova_navigator/vfs/filesystems/ssh.py`
- Test: `tests/vfs/test_ssh_filesystem.py`

This task replaces the earlier standalone `set_pipelined(True)` change with the `_PipelinedWriter` wrapper, and adds `refresh(parent)` calls to all mutating methods.

- [ ] **Step 1: Write failing tests**

Append to `tests/vfs/test_ssh_filesystem.py`:

```python
# ── _PipelinedWriter ──────────────────────────────────────────────────────────


def test_write_returns_pipelined_writer() -> None:
    fs, _, mock_sftp = _make_fs()
    mock_file = MagicMock()
    mock_sftp.open.return_value = mock_file
    writer = fs.write(fs.path("/home/user/out.bin"))
    mock_file.set_pipelined.assert_called_once_with(True)
    writer.write(b"hello")
    mock_file.write.assert_called_once_with(b"hello")


def test_write_close_evicts_parent_cache() -> None:
    fs, mock_ssh, mock_sftp = _make_fs()
    # warm cache for /home/user
    _set_exec_output(mock_ssh, _stat_line("out.bin"))
    fs._dir_stat("/home/user", follow_symlinks=False)
    assert ("/home/user", False) in fs._stat_cache
    # write and close
    mock_sftp.open.return_value = MagicMock()
    writer = fs.write(fs.path("/home/user/out.bin"))
    writer.close()
    assert ("/home/user", False) not in fs._stat_cache


# ── post-mutation eviction ────────────────────────────────────────────────────


def test_remove_evicts_parent_cache() -> None:
    fs, mock_ssh, _ = _make_fs()
    _set_exec_output(mock_ssh, _stat_line("file.txt"))
    fs._dir_stat("/home/user", follow_symlinks=False)
    fs.remove(fs.path("/home/user/file.txt"))
    assert ("/home/user", False) not in fs._stat_cache


def test_rename_evicts_both_parent_caches() -> None:
    fs, mock_ssh, _ = _make_fs()
    _set_exec_output(mock_ssh, _stat_line("old.txt"))
    fs._dir_stat("/home/user", follow_symlinks=False)
    fs._dir_stat("/tmp", follow_symlinks=False)
    fs.rename(fs.path("/home/user/old.txt"), fs.path("/tmp/new.txt"))
    assert ("/home/user", False) not in fs._stat_cache
    assert ("/tmp", False) not in fs._stat_cache


def test_mkdir_evicts_parent_cache() -> None:
    fs, mock_ssh, _ = _make_fs()
    _set_exec_output(mock_ssh, _stat_line("subdir", ftype="directory"))
    fs._dir_stat("/home/user", follow_symlinks=False)
    fs.mkdir(fs.path("/home/user/newdir"))
    assert ("/home/user", False) not in fs._stat_cache


def test_rmdir_evicts_parent_cache() -> None:
    fs, mock_ssh, _ = _make_fs()
    _set_exec_output(mock_ssh, _stat_line("emptydir", ftype="directory"))
    fs._dir_stat("/home/user", follow_symlinks=False)
    fs.rmdir(fs.path("/home/user/emptydir"))
    assert ("/home/user", False) not in fs._stat_cache
```

- [ ] **Step 2: Run tests to verify they fail**

```
uv run pytest tests/vfs/test_ssh_filesystem.py::test_write_returns_pipelined_writer \
               tests/vfs/test_ssh_filesystem.py::test_write_close_evicts_parent_cache \
               tests/vfs/test_ssh_filesystem.py::test_remove_evicts_parent_cache \
               tests/vfs/test_ssh_filesystem.py::test_rename_evicts_both_parent_caches \
               tests/vfs/test_ssh_filesystem.py::test_mkdir_evicts_parent_cache \
               tests/vfs/test_ssh_filesystem.py::test_rmdir_evicts_parent_cache -v
```

Expected: all FAIL

- [ ] **Step 3: Add `_PipelinedWriter` inner class to `SSHFilesystem`**

Add this class inside `SSHFilesystem` (before the first `@override` method is fine):

```python
class _PipelinedWriter:
    """Wraps a paramiko SFTPFile for pipelined writes with a close callback."""

    def __init__(self, f: paramiko.SFTPFile, on_close: Callable[[], None]) -> None:
        self._f = f
        self._on_close = on_close

    def write(self, data: bytes) -> int:
        return self._f.write(data)

    def close(self) -> None:
        self._f.close()
        self._on_close()
```

Add `from collections.abc import Callable` to the imports at the top of `ssh.py`.

- [ ] **Step 4: Replace `write()` method to use `_PipelinedWriter`**

Replace the existing `write()` method:
```python
@override
def write(self, path: VPath) -> StreamWriterLike:
    f = self._sftp_client.open(path.path.as_posix(), "wb")
    f.set_pipelined(True)
    return self._PipelinedWriter(f, lambda: self.refresh(self.parent(path)))
```

Note: this replaces the earlier standalone `set_pipelined(True)` change — the pipelining is now inside `_PipelinedWriter`.

Also update the existing test `test_write_opens_sftp_file` — the return value is now a `_PipelinedWriter`, not a bare `SFTPFile`. The test checks `mock_sftp.open.assert_called_once_with(...)` which still holds; no change needed there.

- [ ] **Step 5: Add `refresh(parent)` calls to mutating methods**

Update `remove`, `rename`, `mkdir`, `rmdir` in `ssh.py`:

```python
@override
def remove(self, path: VPath) -> None:
    self._assert_vpath(path)
    self._sftp_client.remove(path.path.as_posix())
    self.refresh(self.parent(path))

@override
def rename(self, src_path: VPath, dst_path: VPath) -> None:
    self._assert_vpath(src_path)
    self._assert_vpath(dst_path)
    self._sftp_client.rename(src_path.path.as_posix(), dst_path.path.as_posix())
    self.refresh(self.parent(src_path))
    self.refresh(self.parent(dst_path))

@override
def rmdir(self, path: VPath) -> None:
    self._assert_vpath(path)
    self._sftp_client.rmdir(path.path.as_posix())
    self.refresh(self.parent(path))

@override
def mkdir(self, path: VPath) -> None:
    self._assert_vpath(path)
    self._sftp_client.mkdir(path.path.as_posix())
    self.refresh(self.parent(path))
```

- [ ] **Step 6: Run all ssh tests**

```
uv run pytest tests/vfs/test_ssh_filesystem.py -v
```

Expected: all PASS

- [ ] **Step 7: Coding-guideline follow-up checklist**

```
uv run qa
```

- [ ] `Callable` imported from `collections.abc`
- [ ] `_PipelinedWriter` is a private inner class (underscore prefix)
- [ ] All methods have full type annotations
- [ ] QA passes: zero failures

---

### Task 4: `DirectoryBrowser` — `Ctrl+R` reload, `set_path` eviction, polling stubs

**Files:**
- Modify: `src/nova_navigator/widgets/directory_browser.py`
- Test: `tests/nova_navigator/test_directory_browser_refresh.py`

- [ ] **Step 1: Write failing tests**

Create `tests/nova_navigator/test_directory_browser_refresh.py`:

```python
"""Tests for DirectoryBrowser.refresh() integration."""
from __future__ import annotations

from unittest.mock import MagicMock, patch

import pytest

from nova_navigator.vfs.filesystem import Filesystem
from nova_navigator.vfs.filesystems import LocalFilesystem
from nova_navigator.vfs.vpath import VPath


def _make_mock_filesystem(path: str = "/tmp") -> tuple[MagicMock, VPath]:
    """Return a mock Filesystem with a VPath rooted at *path*."""
    fs = MagicMock(spec=Filesystem)
    fs.path.side_effect = lambda p: VPath(p, fs)
    fs.parent.side_effect = lambda vp: VPath(str(vp.path.parent), fs)
    fs.iterdir.return_value = []
    fs.stat.return_value = MagicMock(is_directory=True)
    vp = VPath(path, fs)
    return fs, vp


def test_action_reload_calls_filesystem_refresh() -> None:
    """action_reload() must call filesystem.refresh() with no args."""
    from textual.app import App, ComposeResult
    from nova_navigator.widgets.directory_browser import DirectoryBrowser
    import asyncio

    fs, vp = _make_mock_filesystem("/tmp")

    class TestApp(App[None]):
        def compose(self) -> ComposeResult:
            yield DirectoryBrowser(vp)

    async def run() -> None:
        app = TestApp()
        async with app.run_test() as pilot:
            await pilot.pause()
            browser = app.query_one(DirectoryBrowser)
            browser.action_reload()
            fs.refresh.assert_called_with()

    asyncio.get_event_loop().run_until_complete(run())


def test_set_path_calls_filesystem_refresh_with_path() -> None:
    """set_path(new_path) must call filesystem.refresh(new_path) before loading."""
    from textual.app import App, ComposeResult
    from nova_navigator.widgets.directory_browser import DirectoryBrowser
    import asyncio

    fs, vp = _make_mock_filesystem("/tmp")
    new_vp = VPath("/tmp/subdir", fs)

    class TestApp(App[None]):
        def compose(self) -> ComposeResult:
            yield DirectoryBrowser(vp)

    async def run() -> None:
        app = TestApp()
        async with app.run_test() as pilot:
            await pilot.pause()
            browser = app.query_one(DirectoryBrowser)
            fs.refresh.reset_mock()
            browser.set_path(new_vp)
            fs.refresh.assert_any_call(new_vp)

    asyncio.get_event_loop().run_until_complete(run())
```

- [ ] **Step 2: Run tests to verify they fail**

```
uv run pytest tests/nova_navigator/test_directory_browser_refresh.py -v
```

Expected: `AttributeError` — `action_reload` not yet defined.

- [ ] **Step 3: Add `Ctrl+R` binding and `action_reload()` to `DirectoryBrowser`**

In `src/nova_navigator/widgets/directory_browser.py`, add to `BINDINGS`:
```python
Binding("ctrl+r", "reload", "Reload", show=True),
```

Add method after `update()`:
```python
def action_reload(self) -> None:
    self._path.filesystem.refresh()
    self.update(self.WhatChanged.ALL)
```

- [ ] **Step 4: Add `refresh(path)` call and polling stubs to `set_path()`**

In `set_path()`, add `path.filesystem.refresh(path)` immediately before the existing `self.update(self.WhatChanged.ALL)` call:

```python
        path.filesystem.refresh(path)
        self.update(self.WhatChanged.ALL)
```

Add the two polling stub methods (after `set_path()`):

```python
def _start_polling(self) -> None:
    pass  # TODO: start per-directory polling for non-local filesystems

def _stop_polling(self) -> None:
    pass  # TODO: stop polling timer
```

Call them in `set_path()` alongside the existing watchdog schedule/unschedule block:

```python
        # stop any existing polling
        self._stop_polling()
        if self._watch is not None:
            self._observer.unschedule(self._watch)
            self._watch = None

        if isinstance(self._path.filesystem, LocalFilesystem):
            ...
            self._watch = self._observer.schedule(...)

        # start polling for non-local filesystems
        self._start_polling()
```

- [ ] **Step 5: Run new tests**

```
uv run pytest tests/nova_navigator/test_directory_browser_refresh.py -v
```

Expected: PASS

- [ ] **Step 6: Run full test suite**

```
uv run pytest -v
```

Expected: all PASS

- [ ] **Step 7: Coding-guideline follow-up checklist**

```
uv run qa
```

- [ ] `action_reload` name matches Textual `Binding` action convention
- [ ] `_start_polling` / `_stop_polling` are private (underscore prefix)
- [ ] `Ctrl+R` binding has `show=True` to appear in footer
- [ ] QA passes: zero failures

---

## Final verification

```
uv run qa
```

All tasks complete when QA reports zero lint, type-check, and test failures.
