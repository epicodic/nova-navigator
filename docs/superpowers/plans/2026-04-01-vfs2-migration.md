# VFS → VFS2 Migration Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Replace all `nova_navigator.vfs` imports in `src/` with `nova_navigator.vfs2`, rename `VPath.stats` → `VPath.stat` and `PathStats` → `Stat` at all call sites, and prune the `vfs/` package to only what `ArchiveFilesystem` needs.

**Architecture:** This is a mechanical in-place migration with no new functionality. Each task touches one file, runs `uv run qa` to verify, and commits. `ArchiveFilesystem` stays in `vfs/` untouched; all other consumers move to `vfs2/`.

**Tech Stack:** Python, `uv` (run `uv run qa` to lint + type-check + test)

**Spec note:** The design spec listed `vfs/local.py` as a file to delete, but `vfs/archive.py` imports `LocalFilesystem` from it — so `vfs/local.py` must be kept. Only `vfs/ssh.py` and `vfs/scheme.py` are deleted.

---

## File map

| File | Action | Reason |
|---|---|---|
| `src/nova_navigator/vfs2/__init__.py` | Modify | Add `LocalFilesystem`, `SSHFilesystem` exports |
| `src/nova_navigator/editor.py` | Modify | Swap `vfs` import for `vfs2` |
| `src/nova_navigator/uri.py` | Modify | Swap `vfs` import for `vfs2` |
| `src/nova_navigator/main.py` | Modify | Split `vfs` import: keep `ArchiveFilesystem`, move rest to `vfs2` |
| `src/nova_navigator/widgets/directory_browser.py` | Modify | Swap imports, rename `UpPath.stats→stat`, 9× `path.stats→path.stat` |
| `src/nova_navigator/vfs/__init__.py` | Modify | Slim to export only `ArchiveFilesystem` |
| `src/nova_navigator/vfs/ssh.py` | Delete | No callers remain |
| `src/nova_navigator/vfs/scheme.py` | Delete | No callers remain |

---

## Task 1: Expand `vfs2/__init__.py` exports

**Files:**
- Modify: `src/nova_navigator/vfs2/__init__.py`

- [ ] **Step 1: Verify baseline QA passes**

```bash
uv run qa
```
Expected: all checks pass. If anything fails, stop and fix before continuing.

- [ ] **Step 2: Replace the file content**

```python
from .filesystem import Filesystem
from .filesystems.local import LocalFilesystem
from .filesystems.ssh import SSHFilesystem
from .types import Stat
from .vpath import VPath

__all__ = ["Filesystem", "LocalFilesystem", "SSHFilesystem", "Stat", "VPath"]
```

- [ ] **Step 3: Run QA**

```bash
uv run qa
```
Expected: all checks pass.

- [ ] **Step 4: Commit**

```bash
git add src/nova_navigator/vfs2/__init__.py
git commit -m "refactor: export LocalFilesystem and SSHFilesystem from vfs2"
```

---

## Task 2: Migrate `editor.py`

**Files:**
- Modify: `src/nova_navigator/editor.py`

- [ ] **Step 1: Change the import on line 10**

Old:
```python
from .vfs import VPath
```

New:
```python
from .vfs2 import VPath
```

- [ ] **Step 2: Run QA**

```bash
uv run qa
```
Expected: all checks pass.

- [ ] **Step 3: Commit**

```bash
git add src/nova_navigator/editor.py
git commit -m "refactor: migrate editor.py to vfs2"
```

---

## Task 3: Migrate `uri.py`

**Files:**
- Modify: `src/nova_navigator/uri.py`

- [ ] **Step 1: Change the import on line 5**

Old:
```python
from .vfs import LocalFilesystem, VPath
```

New:
```python
from .vfs2 import LocalFilesystem, VPath
```

- [ ] **Step 2: Run QA**

```bash
uv run qa
```
Expected: all checks pass.

- [ ] **Step 3: Commit**

```bash
git add src/nova_navigator/uri.py
git commit -m "refactor: migrate uri.py to vfs2"
```

---

## Task 4: Migrate `main.py`

**Files:**
- Modify: `src/nova_navigator/main.py`

- [ ] **Step 1: Split the vfs import (around line 31)**

Old:
```python
from nova_navigator.vfs import ArchiveFilesystem, LocalFilesystem, VPath
```

New (two lines):
```python
from nova_navigator.vfs import ArchiveFilesystem
from nova_navigator.vfs2 import LocalFilesystem, VPath
```

- [ ] **Step 2: Run QA**

```bash
uv run qa
```
Expected: all checks pass.

- [ ] **Step 3: Commit**

```bash
git add src/nova_navigator/main.py
git commit -m "refactor: migrate main.py to vfs2 (keep ArchiveFilesystem on vfs)"
```

---

## Task 5: Migrate `directory_browser.py`

This is the most involved file. It has import changes, a renamed property on `UpPath`, and 9 call sites where `path.stats` becomes `path.stat`.

**Files:**
- Modify: `src/nova_navigator/widgets/directory_browser.py`

- [ ] **Step 1: Update the import block (lines 34–36)**

Old:
```python
from ..path_stats import PathStats
from ..vfs import VPath
from ..vfs.local import LocalFilesystem
```

New:
```python
from ..vfs2 import VPath
from ..vfs2.filesystems.local import LocalFilesystem
from ..vfs2.types import Stat
```

- [ ] **Step 2: Rename `UpPath.stats` → `UpPath.stat` and update its return type**

Old:
```python
    @property
    def stats(self) -> PathStats:
        return PathStats(is_directory=True)
```

New:
```python
    @property
    def stat(self) -> Stat:
        return Stat(is_directory=True)
```

- [ ] **Step 3: Update `column_formatter_icon`**

Old:
```python
def column_formatter_icon(path: VPath) -> str:
    """Convert path to display icon."""
    stats = path.stats

    if stats.is_directory:
        icon = ico_("folder")
    else:
        icon = conf_.filetypes.get_icon_for_filename(path.name, default=ico_("file"))

    if not stats.is_directory and not path.guess_mimetype() and stats.is_executable:
        icon = ico_("executable")

    if stats.is_symlink:
        icon_str = icon + "~"
    else:
        icon_str = icon + " "

    if stats.is_symlink and stats.is_broken_symlink:
        icon_str = ico_("broken link") + "!"
    return icon_str
```

New:
```python
def column_formatter_icon(path: VPath) -> str:
    """Convert path to display icon."""
    stat = path.stat

    if stat.is_directory:
        icon = ico_("folder")
    else:
        icon = conf_.filetypes.get_icon_for_filename(path.name, default=ico_("file"))

    if not stat.is_directory and not path.guess_mimetype() and stat.is_executable:
        icon = ico_("executable")

    if stat.is_symlink:
        icon_str = icon + "~"
    else:
        icon_str = icon + " "

    if stat.is_symlink and stat.is_broken_symlink:
        icon_str = ico_("broken link") + "!"
    return icon_str
```

- [ ] **Step 4: Update `column_formatter_size`**

Old:
```python
def column_formatter_size(path: VPath) -> str:
    """Convert size in bytes to human-readable format."""
    stats = path.stats
    if stats.size < 0:
        return ""

    if stats.is_directory:
        return "-"
    ...
```

New (only the first three lines change; the `size //= DECIMAL_MAGNITUDE` loop is unchanged):
```python
def column_formatter_size(path: VPath) -> str:
    """Convert size in bytes to human-readable format."""
    stat = path.stat
    if stat.size < 0:
        return ""

    if stat.is_directory:
        return "-"

    size = stat.size
    for unit in ["", "K", "M", "G", "T"]:
        if size < DECIMAL_MAGNITUDE:
            return f"{size}{unit}"
        size //= DECIMAL_MAGNITUDE
    return f"{size}P"
```

- [ ] **Step 5: Update `column_formatter_modified`**

Old:
```python
def column_formatter_modified(path: VPath) -> str:
    """Convert timestamp to human-readable date."""
    stats = path.stats
    if stats.modified < 0:
        return ""
    dt = datetime.fromtimestamp(stats.modified)
    ...
```

New (only the first two lines change; the datetime formatting logic is unchanged):
```python
def column_formatter_modified(path: VPath) -> str:
    """Convert timestamp to human-readable date."""
    stat = path.stat
    if stat.modified < 0:
        return ""

    dt = datetime.fromtimestamp(stat.modified)

    now = datetime.now()
    if dt.date() == now.date():
        return dt.strftime("Today  %H:%M")

    if dt.year == now.year:
        return dt.strftime("%b %d %H:%M")

    return dt.strftime("%b %d  %Y")
```

- [ ] **Step 6: Update `column_sorter_name`**

Old:
```python
    stats = path.stats
    if stats.is_directory and stats.is_hidden:
        return 1, path.name

    if stats.is_directory and not stats.is_hidden:
        return 2, path.name

    if stats.is_hidden:
        return 3, path.name
```

New:
```python
    stat = path.stat
    if stat.is_directory and stat.is_hidden:
        return 1, path.name

    if stat.is_directory and not stat.is_hidden:
        return 2, path.name

    if stat.is_hidden:
        return 3, path.name
```

- [ ] **Step 7: Update `column_sorter_size`**

Old:
```python
    stats = path.stats
    if stats.is_directory:
        return 1, stats.size
    return 2, stats.size
```

New:
```python
    stat = path.stat
    if stat.is_directory:
        return 1, stat.size
    return 2, stat.size
```

- [ ] **Step 8: Update `column_sorter_modified`**

Old:
```python
    stats = path.stats
    return 1, stats.modified
```

New:
```python
    stat = path.stat
    return 1, stat.modified
```

- [ ] **Step 9: Update inline `filter_func` (inside `_load_directory` or similar)**

Old:
```python
                    if not self.show_hidden_files and item.stats.is_hidden:
```

New:
```python
                    if not self.show_hidden_files and item.stat.is_hidden:
```

- [ ] **Step 10: Update `render_line` method (the highlight-type map)**

Old:
```python
        stats = path.stats
        hightlight_type_map: dict[str, bool] = {
            "highlight-directory": stats.is_directory,
            "highlight-hidden": stats.is_hidden,
            "highlight-executable": stats.is_executable and not stats.is_directory,
            "highlight-symlink": stats.is_symlink,
            "highlight-broken-symlink": stats.is_symlink and stats.is_broken_symlink,
            "highlight-selected": path in self._selected_items,
        }
```

New:
```python
        stat = path.stat
        hightlight_type_map: dict[str, bool] = {
            "highlight-directory": stat.is_directory,
            "highlight-hidden": stat.is_hidden,
            "highlight-executable": stat.is_executable and not stat.is_directory,
            "highlight-symlink": stat.is_symlink,
            "highlight-broken-symlink": stat.is_symlink and stat.is_broken_symlink,
            "highlight-selected": path in self._selected_items,
        }
```

- [ ] **Step 11: Update `_on_directory_browser_path_selected`**

Old:
```python
        if event.path.stats.is_directory:
```

New:
```python
        if event.path.stat.is_directory:
```

- [ ] **Step 12: Verify no remaining `.stats` references in this file**

```bash
grep -n '\.stats' src/nova_navigator/widgets/directory_browser.py
```
Expected: no output.

- [ ] **Step 13: Run QA**

```bash
uv run qa
```
Expected: all checks pass.

- [ ] **Step 14: Commit**

```bash
git add src/nova_navigator/widgets/directory_browser.py
git commit -m "refactor: migrate directory_browser.py to vfs2, rename .stats → .stat"
```

---

## Task 6: Prune `vfs/`

**Files:**
- Modify: `src/nova_navigator/vfs/__init__.py`
- Delete: `src/nova_navigator/vfs/ssh.py`
- Delete: `src/nova_navigator/vfs/scheme.py`
- Keep (do not delete): `src/nova_navigator/vfs/local.py` — `vfs/archive.py` imports `LocalFilesystem` from it

- [ ] **Step 1: Slim `vfs/__init__.py` to export only `ArchiveFilesystem`**

Replace the entire file with:
```python
from .archive import ArchiveFilesystem

__all__ = ["ArchiveFilesystem"]
```

- [ ] **Step 2: Delete the unused files**

```bash
git rm src/nova_navigator/vfs/ssh.py src/nova_navigator/vfs/scheme.py
```

- [ ] **Step 3: Verify no stray imports of the deleted files remain**

```bash
grep -rn 'vfs\.ssh\|vfs\.scheme\|from.*vfs.*ssh\|from.*vfs.*scheme' src/ tests/
```
Expected: no output.

- [ ] **Step 4: Run QA**

```bash
uv run qa
```
Expected: all checks pass.

- [ ] **Step 5: Commit**

```bash
git add src/nova_navigator/vfs/__init__.py
git commit -m "refactor: prune vfs/ to ArchiveFilesystem only, remove ssh.py and scheme.py"
```

---

## Task 7: Final verification

- [ ] **Step 1: Confirm no `src/` file imports from `vfs` except for `ArchiveFilesystem`**

```bash
grep -rn 'from.*\.vfs[^2]' src/
```
Expected output (only this line — `vfs/archive.py`'s internal imports don't match the pattern):
```
src/nova_navigator/main.py:from nova_navigator.vfs import ArchiveFilesystem
```

- [ ] **Step 2: Run the full QA suite one final time**

```bash
uv run qa
```
Expected: all checks pass.
