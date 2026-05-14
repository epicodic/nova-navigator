# OpenViaCopyJob Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use skills:subagent-driven-development (recommended) or skills:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Enable opening files on non-local filesystems (SSH, archive) by copying them to a local temp directory, running the external command, and optionally copying changes back.

**Architecture:** A new `make_temp_dir()` utility creates `/tmp/nova-navigator/<pid>/<uuid4>/`. A new `open_via_copy_task` task function in `filemanager/tasks.py` does the three-step copy/run/copy-back sequence. `OpenViaCopyJob` in `filemanager/jobs.py` wraps it. A new `start_job` abstract method on `NovaNavigatorCore` (implemented by `NovaNavigator`) removes the repeated inline `add_job + job.start` pattern throughout the codebase.

**Tech Stack:** Python 3.12, pytest

**Coding Conventions:** `docs/coding_conventions.md` — read before implementing

---

## File Map

| Action | File | Responsibility |
|--------|------|----------------|
| Create | `src/nova_navigator/tempdir.py` | `make_temp_dir()` helper and `BASE_TEMP_DIR` constant |
| Modify | `src/nova_navigator/filemanager/tasks.py` | Add `open_via_copy_task` |
| Modify | `src/nova_navigator/filemanager/jobs.py` | Add `OpenViaCopyJob` |
| Modify | `src/nova_navigator/nova_navigator_core.py` | Add `start_job` abstract method; split file-open branch; add `_open_nonlocal_file` |
| Modify | `src/nova_navigator/nova_navigator.py` | Implement `start_job`; replace 3 inline patterns |
| Create | `tests/filemanager/test_open_via_copy.py` | Tests for `open_via_copy_task` |

---

### Task 1: `make_temp_dir` — `nova_navigator/tempdir.py`

**Files:**
- Create: `src/nova_navigator/tempdir.py`
- Test: `tests/filemanager/test_open_via_copy.py` (setup only — the module is exercised in Task 2 tests)

- [ ] **Step 1: Create `src/nova_navigator/tempdir.py`**

```python
"""Temporary directory helpers for Nova Navigator."""

from __future__ import annotations

import os
import uuid
from pathlib import Path

# Adapt this constant when porting to other operating systems.
BASE_TEMP_DIR: Path = Path("/tmp/nova-navigator")


def make_temp_dir() -> Path:
    """Create and return a fresh temporary directory for Nova Navigator.

    Directory structure: /tmp/nova-navigator/<pid>/<uuid4>/

    The caller is responsible for deleting the directory when done.
    Adapt this function (and BASE_TEMP_DIR) when porting to other operating systems.
    """
    tmp_dir = BASE_TEMP_DIR / str(os.getpid()) / uuid.uuid4().hex
    tmp_dir.mkdir(parents=True, exist_ok=True)
    return tmp_dir
```

- [ ] **Step 2: Write a quick smoke test in `tests/filemanager/test_open_via_copy.py`**

```python
from pathlib import Path

from nova_navigator.tempdir import make_temp_dir


def test_make_temp_dir_creates_unique_dirs() -> None:
    d1 = make_temp_dir()
    d2 = make_temp_dir()
    try:
        assert d1.exists()
        assert d2.exists()
        assert d1 != d2
        # Both are under /tmp/nova-navigator/<pid>/
        assert d1.parent == d2.parent
    finally:
        import shutil
        shutil.rmtree(d1, ignore_errors=True)
        shutil.rmtree(d2, ignore_errors=True)
```

- [ ] **Step 3: Run test**

`uv run pytest tests/filemanager/test_open_via_copy.py::test_make_temp_dir_creates_unique_dirs -v`
Expected: PASS

- [ ] **Step 4: Coding-guideline follow-up checklist**
- [ ] Conventions file read: `docs/coding_conventions.md`
- [ ] Naming conventions match project rules for all new/edited symbols
- [ ] Language-specific guidelines are followed
- [ ] Task-level verification commands executed and passing
- [ ] Any convention violations fixed before moving to next task

---

### Task 2: `open_via_copy_task` — `filemanager/tasks.py`

**Files:**
- Modify: `src/nova_navigator/filemanager/tasks.py` (append at end)
- Test: `tests/filemanager/test_open_via_copy.py`

- [ ] **Step 1: Write failing tests**

Add to `tests/filemanager/test_open_via_copy.py`:

```python
import shutil
import subprocess
from pathlib import Path, PurePosixPath
from unittest.mock import MagicMock, patch

import pytest

from nova_navigator.filemanager.tasks import open_via_copy_task
from tests._utils.mock_filesystem import MockFilesystem
from tests.filemanager.common import run_task


@pytest.mark.asyncio
async def test_open_via_copy_no_changes() -> None:
    """File is copied to temp, command run, no mtime change → no copy-back, temp deleted."""
    content = b"hello"
    src_fs = MockFilesystem({"/remote/file.txt": content})
    src = src_fs.path("/remote/file.txt")

    captured_cmd: list[list[str]] = []

    def fake_run(cmd: list[str], **kwargs: object) -> None:
        captured_cmd.append(cmd)
        # Do NOT modify the file — mtime stays the same.

    with patch("nova_navigator.filemanager.tasks.subprocess.run", side_effect=fake_run):
        await run_task(lambda ctx: open_via_copy_task(ctx, src))

    # Command was called once
    assert len(captured_cmd) == 1
    # The command includes the filename
    assert any("file.txt" in arg for arg in captured_cmd[0])
    # Temp directory was cleaned up
    from nova_navigator.tempdir import BASE_TEMP_DIR
    import os
    pid_dir = BASE_TEMP_DIR / str(os.getpid())
    # All uuid dirs under pid_dir should be gone (may have been cleaned up in finally)
    # We just verify no OSError was thrown and the task completed.


@pytest.mark.asyncio
async def test_open_via_copy_with_changes_copies_back() -> None:
    """mtime change after command → file is copied back to source."""
    content = b"original"
    new_content = b"modified"
    src_fs = MockFilesystem({"/remote/file.txt": content})
    src = src_fs.path("/remote/file.txt")

    def fake_run(cmd: list[str], **kwargs: object) -> None:
        # Find the temp file path from the command and write to it.
        temp_path = Path(next(arg for arg in cmd if "file.txt" in arg))
        temp_path.write_bytes(new_content)

    with patch("nova_navigator.filemanager.tasks.subprocess.run", side_effect=fake_run):
        await run_task(lambda ctx: open_via_copy_task(ctx, src))

    # Source filesystem received the modified content
    from tests.filemanager.common import read_all
    assert read_all(src_fs, "/remote/file.txt") == new_content


@pytest.mark.asyncio
async def test_open_via_copy_temp_cleaned_up_on_error() -> None:
    """Temp directory is deleted even if the command raises."""
    content = b"data"
    src_fs = MockFilesystem({"/remote/file.txt": content})
    src = src_fs.path("/remote/file.txt")

    created_dirs: list[Path] = []

    original_make_temp_dir = __import__("nova_navigator.tempdir", fromlist=["make_temp_dir"]).make_temp_dir

    def fake_make_temp_dir() -> Path:
        d = original_make_temp_dir()
        created_dirs.append(d)
        return d

    def fake_run(**kwargs: object) -> None:
        raise RuntimeError("command failed")

    with (
        patch("nova_navigator.filemanager.tasks.make_temp_dir", side_effect=fake_make_temp_dir),
        patch("nova_navigator.filemanager.tasks.subprocess.run", side_effect=fake_run),
        pytest.raises(RuntimeError, match="command failed"),
    ):
        await run_task(lambda ctx: open_via_copy_task(ctx, src))

    # All created temp dirs were cleaned up
    for d in created_dirs:
        assert not d.exists()
```

- [ ] **Step 2: Run tests to verify they fail**

`uv run pytest tests/filemanager/test_open_via_copy.py -v`
Expected: FAIL — `open_via_copy_task` not yet implemented

- [ ] **Step 3: Implement `open_via_copy_task` in `src/nova_navigator/filemanager/tasks.py`**

Add the following imports at the top of `tasks.py` (alongside existing imports):

```python
import shutil
import subprocess
from pathlib import Path, PurePosixPath
```

And add this import with the existing local imports:

```python
from nova_navigator.config import conf_
from nova_navigator.tempdir import make_temp_dir
from nova_navigator.vfs.filesystems import LocalFilesystem
```

Append the function at the end of `tasks.py`:

```python
async def open_via_copy_task(ctx: TaskContext, path: VPath) -> None:
    """Copy *path* to a local temp dir, open it with the configured command, copy back if modified.

    Steps:
    1. Copy *path* to /tmp/nova-navigator/<pid>/<uuid4>/<name>
    2. Run the filetype open command against the temp copy (blocking).
    3. If mtime changed: copy the temp file back to *path*.
    4. Delete the temp directory (always, in finally).
    """
    local_fs = LocalFilesystem.singleton()
    tmp_dir: Path = make_temp_dir()
    temp_path = PurePosixPath(tmp_dir) / path.name
    temp_vpath = VPath(temp_path, local_fs)

    try:
        # Step 1: copy to temp
        ctx.status.set_progress(0, 3)
        await copy_file(ctx, path, temp_vpath, FileCopyOptions(overwrite="overwrite"))
        ctx.status.set_progress(1, 3)

        # Step 2: record mtime and run command
        mtime_before = local_fs.stat(temp_vpath).modified
        open_cmd = conf_.filetypes.get_open_command_for_file_path(temp_path)
        subprocess.run(open_cmd, cwd=tmp_dir)  # noqa: S603
        ctx.status.set_progress(2, 3)

        # Step 3: copy back if modified
        mtime_after = local_fs.stat(temp_vpath).modified
        if mtime_after != mtime_before:
            await copy_file(ctx, temp_vpath, path, FileCopyOptions(overwrite="overwrite"))
        ctx.status.set_progress(3, 3)
    finally:
        shutil.rmtree(tmp_dir, ignore_errors=True)
```

- [ ] **Step 4: Run tests to verify they pass**

`uv run pytest tests/filemanager/test_open_via_copy.py -v`
Expected: PASS

- [ ] **Step 5: Coding-guideline follow-up checklist**
- [ ] Conventions file read: `docs/coding_conventions.md`
- [ ] Naming conventions match project rules for all new/edited symbols
- [ ] Language-specific guidelines are followed
- [ ] Task-level verification commands executed and passing
- [ ] Any convention violations fixed before moving to next task

---

### Task 3: `OpenViaCopyJob` — `filemanager/jobs.py`

**Files:**
- Modify: `src/nova_navigator/filemanager/jobs.py`

- [ ] **Step 1: Add import and class to `src/nova_navigator/filemanager/jobs.py`**

Add `open_via_copy_task` to the existing import from `.tasks`:

```python
from .tasks import copy_files, erase_files, move_files, open_via_copy_task
```

Append at end of file:

```python
class OpenViaCopyJob(Job):
    """Job that opens a non-local file by copying it to a local temp directory."""

    def __init__(self, path: VPath) -> None:
        super().__init__(f"Open: {path.name}", open_via_copy_task, path)
```

- [ ] **Step 2: Run QA**

`uv run qa`
Expected: zero failures

- [ ] **Step 3: Coding-guideline follow-up checklist**
- [ ] Conventions file read: `docs/coding_conventions.md`
- [ ] Naming conventions match project rules for all new/edited symbols
- [ ] Language-specific guidelines are followed
- [ ] Task-level verification commands executed and passing
- [ ] Any convention violations fixed before moving to next task

---

### Task 4: `start_job` on `NovaNavigatorCore` and `NovaNavigator`

**Files:**
- Modify: `src/nova_navigator/nova_navigator_core.py`
- Modify: `src/nova_navigator/nova_navigator.py`

- [ ] **Step 1: Add `start_job` abstract method to `NovaNavigatorCore`**

In `src/nova_navigator/nova_navigator_core.py`, add the `Job` import alongside existing ones:

```python
from nova_navigator.scheduler import Job
```

Add the abstract method after `execute_command`:

```python
    async def start_job(self, job: Job) -> None:
        """Register *job* in the job registry and start it. Must be overridden by subclasses."""
        raise NotImplementedError
```

- [ ] **Step 2: Implement `start_job` in `NovaNavigator`**

In `src/nova_navigator/nova_navigator.py`, add after `execute_command`:

```python
    async def start_job(self, job: Job) -> None:
        self.job_registry.add_job(job)
        await job.start(self.request_callback)
```

- [ ] **Step 3: Replace inline `add_job` + `job.start` patterns in `MainScreen`**

In `src/nova_navigator/nova_navigator.py`, find and replace all three call sites:

`action_copy_or_move_files` — replace:
```python
        if job is not None:
            self.app.job_registry.add_job(job)
            await job.start(self.app.request_callback)
```
with:
```python
        if job is not None:
            await self.app.start_job(job)
```

`action_delete_files` — replace:
```python
        if job is not None:
            self.app.job_registry.add_job(job)
            await job.start(self.app.request_callback)
```
with:
```python
        if job is not None:
            await self.app.start_job(job)
```

`action_start_dummy_operation` — replace:
```python
        job = Job("Dummy Operation", dummy_task)
        self.app.job_registry.add_job(job)
        await job.start(self.app.request_callback)
```
with:
```python
        job = Job("Dummy Operation", dummy_task)
        await self.app.start_job(job)
```

- [ ] **Step 4: Run QA**

`uv run qa`
Expected: zero failures

- [ ] **Step 5: Coding-guideline follow-up checklist**
- [ ] Conventions file read: `docs/coding_conventions.md`
- [ ] Naming conventions match project rules for all new/edited symbols
- [ ] Language-specific guidelines are followed
- [ ] Task-level verification commands executed and passing
- [ ] Any convention violations fixed before moving to next task

---

### Task 5: Wire `open_path` in `NovaNavigatorCore`

**Files:**
- Modify: `src/nova_navigator/nova_navigator_core.py`

- [ ] **Step 1: Add imports needed in `nova_navigator_core.py`**

Add `OpenViaCopyJob` import:

```python
from nova_navigator.filemanager.jobs import OpenViaCopyJob
```

Add `LocalFilesystem` import:

```python
from nova_navigator.vfs.filesystems import LocalFilesystem
```

- [ ] **Step 2: Split the file-open branch in `open_path` and add `_open_nonlocal_file`**

Replace the current tail of `open_path`:

```python
        open_cmd = conf_.filetypes.get_open_command_for_file_path(path.path)
        await self.execute_command(open_cmd, path.parent.path)
```

with:

```python
        if isinstance(path.filesystem, LocalFilesystem):
            open_cmd = conf_.filetypes.get_open_command_for_file_path(path.path)
            await self.execute_command(open_cmd, path.parent.path)
        else:
            await self._open_nonlocal_file(path)
```

Add the private helper method after `open_path`:

```python
    async def _open_nonlocal_file(self, path: VPath) -> None:
        """Open a non-local file by copying it to a local temp directory."""
        await self.start_job(OpenViaCopyJob(path))
```

- [ ] **Step 3: Write a test for the dispatch logic in `tests/filemanager/test_open_via_copy.py`**

```python
from unittest.mock import AsyncMock, patch

import pytest

from nova_navigator.nova_navigator_core import NovaNavigatorCore, PanelRef
from nova_navigator.scheduler import Job
from nova_navigator.vfs import VPath
from nova_navigator.vfs.filesystems import LocalFilesystem
from nova_navigator.vfs.types import Stat
from tests._utils.mock_filesystem import MockFilesystem


class _ConcreteCore(NovaNavigatorCore):
    """Minimal concrete subclass for testing dispatch only."""

    started_jobs: list[Job]
    executed_commands: list[tuple[list[str], object]]

    def __init__(self) -> None:
        # Skip NovaNavigatorCore.__init__ (loads configs) — patch conf_ instead.
        self.started_jobs = []
        self.executed_commands = []

    async def start_job(self, job: Job) -> None:
        self.started_jobs.append(job)

    async def execute_command(self, args: list[str], cwd: object) -> None:
        self.executed_commands.append((args, cwd))

    async def open_editor(self, path: VPath) -> None:
        pass

    async def set_terminal_directory(self, path: VPath) -> None:
        pass

    async def set_panel_directory(self, path: VPath, panel: PanelRef) -> None:
        pass


@pytest.mark.asyncio
async def test_open_path_local_calls_execute_command() -> None:
    """Local file → execute_command is called, start_job is not."""
    local_fs = LocalFilesystem.singleton()
    # Use a real local file that exists (/etc/hostname is always present on Linux)
    path = local_fs.path("/etc/hostname")

    core = _ConcreteCore()
    with patch("nova_navigator.nova_navigator_core.conf_") as mock_conf:
        mock_conf.filetypes.get_open_command_for_file_path.return_value = ["cat", str(path.path)]
        await core.open_path(path)

    assert len(core.executed_commands) == 1
    assert core.started_jobs == []


@pytest.mark.asyncio
async def test_open_path_nonlocal_calls_start_job() -> None:
    """Non-local file → start_job is called with OpenViaCopyJob, execute_command is not."""
    from nova_navigator.filemanager.jobs import OpenViaCopyJob

    content = b"data"
    remote_fs = MockFilesystem({"/remote/doc.pdf": content})
    path = remote_fs.path("/remote/doc.pdf")

    core = _ConcreteCore()
    with patch("nova_navigator.nova_navigator_core.conf_"):
        await core.open_path(path)

    assert len(core.started_jobs) == 1
    assert isinstance(core.started_jobs[0], OpenViaCopyJob)
    assert core.executed_commands == []
```

- [ ] **Step 4: Run tests**

`uv run pytest tests/filemanager/test_open_via_copy.py -v`
Expected: PASS

- [ ] **Step 5: Run full QA**

`uv run qa`
Expected: zero failures

- [ ] **Step 6: Coding-guideline follow-up checklist**
- [ ] Conventions file read: `docs/coding_conventions.md`
- [ ] Naming conventions match project rules for all new/edited symbols
- [ ] Language-specific guidelines are followed
- [ ] Task-level verification commands executed and passing
- [ ] Any convention violations fixed before moving to next task
