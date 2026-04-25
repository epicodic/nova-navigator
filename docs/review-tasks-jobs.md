# Code Review: `tasks.py` and `jobs.py`

Review of `src/nova_navigator/filemanager/tasks.py`, `src/nova_navigator/filemanager/jobs.py`, and their test suites.
All 56 existing tests pass.

---

## Summary

Two **critical data-loss bugs** exist in `_move_path` for cross-device moves.
Both are untested, so they have been silently present.
There are also three additional correctness/progress bugs and several test coverage gaps.
`jobs.py` has no tests and contains a silent UX bug where a user-edited filename is ignored.

---

## Affected Call Paths

```
copy_files
 ├─ [1 src, non-dir dst]  → copy_file                  (BUG-3, BUG-5)
 ├─ [N src / dir dst]     → _copy_file_step → copy_file
 └─ [dir src]             → _copy_dir → copy_file       (BUG-4)

move_files → _move_path
 ├─ [same-device]  → rename                             (correct)
 └─ [cross-device]
      ├─ [file]    → copy_file → remove                 (BUG-1)
      └─ [dir]     → copy_file loop → erase_files       (BUG-2)
```

---

## Bugs

### BUG-1 · Cross-device single-file move: source deleted when copy is skipped

**Severity: critical (data loss)**

In the cross-device branch of `_move_path`, the source file is removed unconditionally after `copy_file` returns:

```python
else:
    await copy_file(ctx, src_path, actual_dst, options)
    src_path.filesystem.remove(src_path)   # ← executes unconditionally
```

`copy_file` returns normally (no exception) in two skip scenarios:

- `options.overwrite == "skip"` and destination already exists.
- `options.overwrite == "ask"` and the user answers NO or NONE.

In both cases `copy_file` exits early without writing anything, but `remove` still runs.
The source file is permanently deleted even though no data was written to the destination.

**Affected scenario:** Any cross-device move where the destination already holds a file with the same name and the overwrite policy is not `"overwrite"`.

**Fix:** Have `copy_file` return a boolean indicating whether the copy was actually performed, and only remove the source when `True`:

```python
copied = await copy_file(ctx, src_path, actual_dst, options)
if copied:
    src_path.filesystem.remove(src_path)
```

---

### BUG-2 · Cross-device directory move: source erased even when some files were skipped

**Severity: critical (data loss)**

In the cross-device directory branch of `_move_path`:

```python
for src_root, _src_dirs, src_files in src_path.walk():
    ...
    for f in src_files:
        await copy_file(ctx, f, dst_root / f.name, options)   # may skip silently
await erase_files(ctx, [src_path], EraseFilesOptions(ask_before_erase=False))  # always runs
```

Same root cause as BUG-1.
If any file inside the directory was skipped (destination exists + skip policy or NO answer), that file is not copied but `erase_files` still deletes the entire source directory tree, destroying any files whose copies were skipped.

**Fix:** Track whether every file was successfully copied.
If any copy was skipped, do not erase the source (or only erase the files that were actually copied successfully).
This requires the boolean return value from the BUG-1 fix.

---

### BUG-3 · `copy_files` — single directory source to non-existent destination crashes

**Severity: high (unexpected crash)**

When exactly one source path is passed to `copy_files` and the destination does not yet exist (`dst_is_directory` is `False`), the code takes the shortcut path:

```python
if len(src_paths) == 1 and not dst_is_directory:
    await copy_file(ctx, src_paths[0], destination, options)
```

The condition guards on the *destination* not being a directory but not on whether the *source* is a directory.
If `src_paths[0]` is a directory, `copy_file` calls `src_path.filesystem.read(src_path)`, which raises `IsADirectoryError`.
The destination directory is never created; the copy fails with an unexpected exception.

This is a real user-facing scenario: copying a folder to an empty destination panel.

**Fix:**

```python
if len(src_paths) == 1 and not dst_is_directory and not src_paths[0].stat.is_directory:
```

---

### BUG-4 · `_copy_dir` — progress `total` inflated, `completed` under-counted

**Severity: medium (progress permanently shows incomplete)**

`_copy_dir` calls `ctx.status.update_progress(inc_total=len(src_files))` for every directory visited during `walk()`, but only calls `ctx.status.update_progress(inc_completed=1)` once at the very end.

For a directory with N files, `total` is incremented by N but `completed` only by 1:

```
# Directory /src/root with 3 files — after _copy_dir finishes:
# total = 3, completed = 1   ← stuck at 33 %
```

The mismatch is compounded by subdirectories because each level of `walk()` adds its file count to `total`.

**Fix (option A):** Use `_copy_file_step` (which does increment `completed` per file) inside `_copy_dir` instead of raw `copy_file`.

**Fix (option B):** Remove the `inc_total=len(src_files)` calls entirely and count each directory as one unit of work, consistent with `inc_completed=1` at the end and with how `copy_files` counts each path in `src_paths`.

---

### BUG-5 · `copy_file` calls `ctx.status.set_completed()` on skip — corrupts caller's progress

**Severity: medium (invalid progress state: `completed > total`)**

When a copy is skipped (`overwrite="skip"`) or the user answers NO, `copy_file` calls `ctx.status.set_completed()`, which sets `progress.completed = progress.total` (an absolute setter, not a delta).

`copy_files` then calls `ctx.status.update_progress(inc_completed=1)` on return, leaving `completed > total`:

```
copy_files: update_progress(inc_total=1)         # total=1, completed=0
  copy_file: set_completed()                      # completed=total=1   (absolute set)
copy_files: update_progress(inc_completed=1)     # completed=2, total=1  ← invalid
```

`copy_file` is a low-level building block and should not touch overall progress state at all.
The same over-count occurs through `_copy_file_step` for multi-file copies.

**Fix:** Remove the `set_completed()` calls from `copy_file`.
The boolean return value introduced by the BUG-1 fix naturally allows callers to decide whether to increment `completed`.

---

### BUG-6 · `copy_file` — partial destination file left on error

**Severity: high (silently corrupt destination)**

When `copy_file` fails mid-copy (disk full, read error, `TaskCancelled` after partial write), the `finally` block closes the writer but does not remove the partially-written destination file:

```python
finally:
    if reader:
        reader.close()
    if writer:
        writer.close()   # ← partial file remains; not removed
```

On a subsequent retry with `overwrite="ask"`, the user is prompted "file already exists", but the existing file is corrupt.

**Fix:** In the `finally` block, if an exception is in-flight and `writer` was opened, remove the destination file (after closing it) before re-raising.

---

## Design / Robustness Issues

### `copy_file` — reader opened before the overwrite/skip decision

`src_path.filesystem.read(src_path)` is called before checking whether the destination exists and whether the operation should be skipped.
On SSH/network filesystems this opens a remote file handle unnecessarily for operations that will be skipped.
The reader should be opened after the overwrite decision.

Note: `test_copy_file_overwrite_skip` currently asserts `src_fs.readers[0].close_count == 1`, which *confirms* the reader was opened despite the skip.
Once this is fixed the assertion should change to `assert len(src_fs.readers) == 0`.

---

### `_copy_dir` / `copy_files` — concurrent subtasks not cancelled on first failure

`_copy_dir` spawns all file copies as concurrent subtasks via `asyncio.gather`.
If one subtask raises, `asyncio.gather` propagates the first exception but the remaining tasks continue running in the background, leaving a partially-populated destination with no indication of which files are missing.
This is hard to fix without a scheduler-level cancellation API, but it should be noted as a known limitation.

---

### `_move_path` — same-device directory overwrite prompt does not warn about recursive deletion

When the destination is an existing directory and the user (or policy) confirms overwrite, `erase_files` is called with `ask_before_erase=False` on the entire destination tree.
The prompt only says *"'<path>' already exists. Overwrite?"* and does not communicate that an entire directory tree will be permanently deleted.

---

## `jobs.py` Issues

### No tests

`copy_or_move_files_job` and `delete_files_job` have zero test coverage.
At minimum the following should be tested (using a mock or stub dialog):

- Dialog cancelled → function returns `None`, no job created.
- Dialog confirmed → `Job` is returned with the correct name, task function, and arguments.
- `move=True` → `Job` wraps `move_files`, not `copy_files`.

### `CopyMoveFilesDialog` filename input is silently ignored

`CopyMoveFilesDialog` renders a filename `Input` widget when exactly one source file is selected.
The user can type a different destination filename, but `jobs.py` never reads the input value when constructing the `Job`.
The destination path is always the original `dst_path` passed in.
The user believes they renamed the destination file; the operation ignores the edit.

### Magic result strings in dialog checks

```python
if result != "OK":   ...   # CopyMoveFilesDialog
if result != "YES":  ...   # DeleteFilesDialog
```

These are compared against bare string literals.
If a button label or constant is ever renamed, both callers will silently accept every result (including Cancel/No) and launch the job unexpectedly.
Consider comparing against a typed enum or constant.

---

## Test Coverage Gaps Summary

| Missing test | Related function |
|---|---|
| Cross-device move, `overwrite="skip"`, destination exists → source kept | `_move_path` (BUG-1) |
| Cross-device move, `overwrite="ask"` + NO/NONE → source kept | `_move_path` (BUG-1) |
| Cross-device directory move, some files skipped → source kept | `_move_path` (BUG-2) |
| Single directory source to non-existent destination | `copy_files` (BUG-3) |
| Progress `completed == total` after copying a directory | `_copy_dir` (BUG-4) |
| Progress `completed <= total` after skip (single file) | `copy_file`, `copy_files` (BUG-5) |
| Partial destination file removed after write error | `copy_file` (BUG-6) |
| Partial destination file removed after read error | `copy_file` (BUG-6) |
| Reader never opened when copy is skipped | `copy_file` |
| Dialog cancelled → job returns `None` | `copy_or_move_files_job` |
| Dialog confirmed copy → correct `Job` returned | `copy_or_move_files_job` |
| Dialog confirmed move → correct `Job` returned | `copy_or_move_files_job` |
| Dialog confirmed delete → correct `Job` returned | `delete_files_job` |

---

## What Is Working Well

- `copy_file` correctly closes both reader and writer via `finally` in all tested error paths (read error, write error, cancellation).
- `_move_path` correctly handles same-device vs cross-device detection and branches accordingly.
- The `ALL` / `NONE` decision caching in the scheduler is correctly tested for copy, move, and erase operations.
- `erase_files` progress counters are correct (tested in `test_erase_skipped_dir_not_counted_in_completed`).
- `MockFilesystem.rename` correctly moves child nodes when renaming a directory.
- `test_copy_file_ask_no` correctly verifies that zero writers are opened after a NO decision.
