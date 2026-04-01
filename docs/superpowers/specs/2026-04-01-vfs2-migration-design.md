# VFS → VFS2 Migration Design

**Date:** 2026-04-01
**Status:** Approved

## Overview

Migrate all `src/` and `tests/` code from the legacy `nova_navigator.vfs` package to the current `nova_navigator.vfs2` package. `ArchiveFilesystem` is explicitly out of scope and stays in `vfs/` until a separate effort ports it.

## Scope

### In scope
- Update imports in four `src/` files that reference `nova_navigator.vfs`
- Rename `VPath.stats` → `VPath.stat` and `PathStats` → `Stat` at all affected call sites
- Expand `vfs2/__init__.py` to export `LocalFilesystem` and `SSHFilesystem`
- Prune `vfs/` to the minimum needed to support `ArchiveFilesystem`

### Out of scope
- Porting `ArchiveFilesystem` to `vfs2` (deferred)
- Updating `archive/` module (`tar_archive.py`, `zip_archive.py`) to use `vfs2.Stat`
- Any new functionality or refactoring beyond mechanical import/rename changes

## File-by-file changes

### `src/nova_navigator/vfs2/__init__.py`
Add `LocalFilesystem` and `SSHFilesystem` to the public exports so consumers do not need to reach into sub-packages.

### `src/nova_navigator/editor.py`
- `from .vfs import VPath` → `from .vfs2 import VPath`

### `src/nova_navigator/uri.py`
- `from .vfs import LocalFilesystem, VPath` → `from .vfs2 import LocalFilesystem, VPath`

### `src/nova_navigator/main.py`
- Keep: `from nova_navigator.vfs import ArchiveFilesystem`
- Add: `from nova_navigator.vfs2 import LocalFilesystem, VPath`
- Remove: `LocalFilesystem` and `VPath` from the `vfs` import line

### `src/nova_navigator/widgets/directory_browser.py`
- Replace `vfs` imports with `vfs2` equivalents
- `UpPath` base class changes from `vfs.VPath` to `vfs2.VPath`
- Rename `UpPath.stats` property → `UpPath.stat`; return `Stat(is_directory=True)` instead of `PathStats(is_directory=True)`
- Rename all 7 `path.stats` call sites in column formatters → `path.stat`
- Remove the `PathStats` import (no longer needed here)

## vfs/ pruning

Files to **delete** (no remaining callers outside `vfs/` itself):
- `vfs/local.py`
- `vfs/ssh.py`
- `vfs/scheme.py`

Files to **keep** (still required by `ArchiveFilesystem`):
- `vfs/archive.py`
- `vfs/filesystem.py` (defines the legacy `VPath`/`Filesystem` that `archive.py` depends on)

`vfs/__init__.py` — slim down to export only `ArchiveFilesystem`.

## Verification

Run `uv run qa` after each file change (lint + type check + tests). The existing test suite exercises `vfs2` via `MockFilesystem` directly and will catch missed renames or broken imports. No new tests are required for this mechanical refactor.

## Key naming differences resolved

| Legacy (`vfs`) | Current (`vfs2`) |
|---|---|
| `VPath.stats` | `VPath.stat` |
| `PathStats` (from `nova_navigator.path_stats`) | `Stat` (from `nova_navigator.vfs2.types`) |
| `LocalFilesystem` (not exported from `vfs2`) | `LocalFilesystem` (added to `vfs2` exports) |
| `SSHFilesystem` (not exported from `vfs2`) | `SSHFilesystem` (added to `vfs2` exports) |
