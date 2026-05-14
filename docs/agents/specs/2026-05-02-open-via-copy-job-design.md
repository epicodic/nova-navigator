# Design: OpenViaCopyJob — Open Non-Local Files via Temp Copy

## Problem

`open_path` in `NovaNavigatorCore` opens a file by passing its path string to `execute_command`.
This works for `LocalFilesystem` paths, but fails for SSH or archive filesystems: the path doesn't exist locally and the external app cannot access it.

Scope: this design covers only the **file-open branch** (the `conf_.filetypes.get_open_command_for_file_path` call at the bottom of `open_path`).
The executable branch is left unchanged for now.

---

## Approach

**Approved approach: private helper + Job subclass.**

`open_path` detects non-local filesystems and delegates to `_open_nonlocal_file(path)`, which creates and starts an `OpenViaCopyJob`.
The job wraps a three-step task function `open_via_copy_task` that runs in a worker thread via the existing scheduler.

---

## Components

### 1. `open_via_copy_task` — `filemanager/tasks.py`

New async task function with signature:

```python
async def open_via_copy_task(ctx: TaskContext, path: VPath) -> None:
```

Three sequential steps:

**Step 1 — Copy to temp:**
Create a unique temp directory via `make_temp_dir()` (see component 2 below).
Create a local `VPath` for the destination: `VPath(tmp_dir / path.name, LocalFilesystem.singleton())`.
Call `copy_file(ctx, path, temp_vpath, FileCopyOptions(overwrite="overwrite"))`.

**Step 2 — Record mtime and run command:**
Read initial mtime via `LocalFilesystem.singleton().stat(temp_vpath).modified` (bypasses VPath cache).
Derive the open command via `conf_.filetypes.get_open_command_for_file_path(temp_vpath.path)` — same extension → same section, but `%f` is substituted with the temp path.
Run the command with `subprocess.run(open_cmd, cwd=tmp_dir)` (blocking — see todo item 8).

**Step 3 — Copy back and cleanup:**
Re-read mtime via `LocalFilesystem.singleton().stat(temp_vpath).modified`.
If mtime changed: call `copy_file(ctx, temp_vpath, path, FileCopyOptions(overwrite="overwrite"))`.
Always delete the temp file and temp directory in a `finally` block.

### 2. `make_temp_dir` — `nova_navigator/tempdir.py`

New module with a single public helper:

```python
def make_temp_dir() -> Path:
    """Create and return a fresh temporary directory for Nova Navigator.

    Directory structure: /tmp/nova-navigator/<pid>/<uuid4>/

    The caller is responsible for deleting the directory when done.
    Adapt this function when porting to other operating systems.
    """
```

Uses `os.getpid()` for the PID segment and `uuid.uuid4().hex` for the unique leaf.
Creates the directory with `Path.mkdir(parents=True, exist_ok=True)` and returns the `Path`.
The `BASE_TEMP_DIR = Path("/tmp/nova-navigator")` constant is defined at module level for easy OS-porting.

### 3. `OpenViaCopyJob` — `filemanager/jobs.py`

New `Job` subclass:

```python
class OpenViaCopyJob(Job):
    def __init__(self, path: VPath) -> None:
        super().__init__(f"Open: {path.name}", open_via_copy_task, path)
```

Title `"Open: <filename>"` appears in the Processes dialog.

### 4. `NovaNavigatorCore.start_job` — abstract method

```python
async def start_job(self, job: Job) -> None:
    raise NotImplementedError
```

This replaces the repeated inline pattern:
```python
self.app.job_registry.add_job(job)
await job.start(self.app.request_callback)
```

### 5. `NovaNavigator.start_job` — implementation

```python
async def start_job(self, job: Job) -> None:
    self.job_registry.add_job(job)
    await job.start(self.request_callback)
```

All three existing call sites in `MainScreen` (`action_copy_or_move_files`, `action_delete_files`, `action_start_dummy_operation`) are updated to call `await self.app.start_job(job)`.

### 6. `open_path` change — `NovaNavigatorCore`

The file-open branch is split:

```python
if isinstance(path.filesystem, LocalFilesystem):
    open_cmd = conf_.filetypes.get_open_command_for_file_path(path.path)
    await self.execute_command(open_cmd, path.parent.path)
else:
    await self._open_nonlocal_file(path)
```

### 7. `_open_nonlocal_file` — private method on `NovaNavigatorCore`

```python
async def _open_nonlocal_file(self, path: VPath) -> None:
    await self.start_job(OpenViaCopyJob(path))
```

---

## Error handling

If any step of `open_via_copy_task` raises, the `Job` catches it (existing `Job.start` machinery sets `state = FAILED` with an error message).
The `finally` block in the task ensures the temp directory is cleaned up even on failure.

---

## Known limitations

- The `subprocess.run` call in step 2 blocks the worker thread's event loop for the duration of the external command.
  This is acceptable for now and tracked in todo item 8.
- No TUI suspend is performed for the non-local case (the Textual UI stays visible while the external app runs).
  This differs from the local case where `self.suspend()` is used.
  Tracked in todo item 8 for future adaptation.

---

## Files changed

| File | Change |
|------|--------|
| `src/nova_navigator/tempdir.py` | New module: `make_temp_dir()`, `BASE_TEMP_DIR` |
| `src/nova_navigator/nova_navigator_core.py` | Add `start_job` abstract method; split file-open branch; add `_open_nonlocal_file` |
| `src/nova_navigator/nova_navigator.py` | Implement `start_job`; replace 3 inline `add_job` + `job.start` patterns |
| `src/nova_navigator/filemanager/tasks.py` | Add `open_via_copy_task` |
| `src/nova_navigator/filemanager/jobs.py` | Add `OpenViaCopyJob` |

---

## Testing

- Unit test `open_via_copy_task` with a mock non-local filesystem (e.g. `MockFilesystem`), a mock `subprocess.run`, and two scenarios: mtime unchanged (no copy-back) and mtime changed (copy-back performed).
- Verify temp dir is deleted in both scenarios and on failure.
- Test `open_path` dispatching: local path → `execute_command`; non-local path → `start_job` called.
