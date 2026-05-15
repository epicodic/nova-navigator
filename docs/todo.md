# Task Scheduler — Pending Fixes

## 1. Replace polling loop with `asyncio.wrap_future()` — `task.py`

**Problem:** `_wait_for_future` busy-polls a GUI-loop `asyncio.Future` with `await asyncio.sleep(0.01)`.
Wastes CPU and adds up to 10 ms latency on every decision response.

**Fix:**
- Change `GuiRequestCallback` type: replace `asyncio.Future[Decision]` with `concurrent.futures.Future[Decision]`
- In `_create_decision_requester`: create `concurrent.futures.Future[Decision]()` directly — no need to
  call `run_coroutine_threadsafe(_create_future_in_loop(), ...)` just to create the future
- Replace `await _wait_for_future(gui_future)` with `await asyncio.wrap_future(gui_future)` — proper
  suspension, no polling
- Delete helpers `_create_future_in_loop` and `_wait_for_future`

**Caller impact:** GUI callback implementations receive a `concurrent.futures.Future` instead of
`asyncio.Future`. Interface is identical (`.set_result(value)`), so only a type change on that side.

---
# complete test_decision.py
---

## 3. Race condition in `erase_files` — `filemanager/tasks.py`

**Problem:** The recursive subtask is created but not awaited before `rmdir`:
```python
await ctx.subtask(erase_files(ctx, list(path.iterdir()), ...))  # returns Task, not awaited
path.filesystem.rmdir(path)  # may run before contents are erased
```

**Fix:** Collect and gather, same pattern as `_copy_dir_recursive`:
```python
t = await ctx.subtask(erase_files(ctx, list(path.iterdir()), ...))
await t
path.filesystem.rmdir(path)
```
Or just call directly without `subtask` since there's only one recursive call per directory anyway:
```python
await erase_files(ctx, list(path.iterdir()), EraseFilesOptions(ask_before_erase=False))
path.filesystem.rmdir(path)
```

---

## 4. Title-keyed "apply to all" cache is fragile — `task.py`

**Problem:** `_decisions_to_all` is a `dict[str, Decision]` keyed on the dialog title string.
Any two dialogs with the same title accidentally share a cached decision.
Currently harmless (copy and move both use `"Overwrite"` and mean the same thing), but easy to
break when adding new dialogs.

**Proposed fix:** Key on a dedicated `DecisionRequest` identity token rather than the human-readable
title. Options:
- Add an explicit `decision_id: str` field to `DecisionRequest` (separate from display title)
- Or use the full `(title, expected_decisions)` tuple as the key to at least reduce collision risk

---

## 5. `_POSITIVE = 0x0000` zero-valued Flag member — `decision.py`

**Problem:** A zero-valued member in a `Flag` enum behaves specially in Python — it is considered
"present" in every flag value, which can cause surprising results with `in` checks and `bool()`.
The code works around this by never checking `_POSITIVE` directly, only `_NEGATIVE`.

**Fix:** Remove `_POSITIVE` entirely. `is_positive` is already defined as `not is_negative`, so the
constant serves no purpose. If a named "affirmative base" is desired, use a non-zero value (e.g. `0x0000_0000` → `0x0002_0000`) or restructure as a plain `IntEnum` / two-field dataclass.

---

## 6. Global decision serialization across parallel schedulers — `scheduler.py`

**Current behaviour:** `_request_lock` only serializes decisions *within* a single
`AsyncTaskScheduler` instance. When two schedulers run in parallel (each in its own worker thread),
both can invoke `gui_request_callback` concurrently, causing two dialogs to appear at the same time.

**Proposed fix:** Add an optional `asyncio.Lock` that is shared across scheduler instances and passed
in from the GUI layer (or via a singleton). The GUI callback acquires this lock before showing any
dialog, ensuring only one dialog is ever displayed at a time regardless of how many schedulers are
running.



## 8. `execute_command` blocks the event loop — `nova_navigator.py`

`execute_command` and the subprocess call inside `edit_remote_file_task` are synchronous/blocking.
For local files, `execute_command` uses `self.suspend()` to hide the TUI, but the call is still blocking the event loop.
This behavior needs to be adapted afterwards (e.g. run subprocess in a thread, handle TUI suspend correctly for non-local case).

---

## 7. Directory Browser always runs a watch dog, also for non-local filesystems — `directory_browser.py`
- needs to be refactored.
- the filesystem should support a watch interface, that is used by the browser
- each filesystem can decide whether to implement it or not (e.g. local fs can use watchdog, ssh fs can use polling, archive fs can disable it entirely)

---

## 9. `SSHFilesystem._dir_stat` has a hard zsh dependency — `vfs/filesystems/ssh.py`

`_STAT_COMMAND` uses `*(D)` (include dotfiles in glob), which is **zsh-only** syntax.
If the remote shell is bash, sh, fish, or any other shell, the glob is treated as a literal filename and `stat` silently returns nothing or an error.

**Fix options:**
- Replace the `exec_command` + zsh glob approach with SFTP `listdir_attr()` for directory listing (shell-independent, binary protocol — but potentially slower for large directories).
- Or keep `exec_command` but detect the remote shell and emit the appropriate glob syntax per shell.
- Or use a POSIX-compatible alternative such as `find . -maxdepth 1 -exec stat -c '...' {} +`, which works on any POSIX shell.

Note: the `exec_command` approach was a deliberate design choice for performance (batches all stat data in one round-trip).
Any fix must preserve that property.

---

## ~~10. Most editor dialogs write the changes in their implementation, this should be done at the caller side~~ ✅ DONE
- `Dialog` is now `ModalScreen[Decision | None]`; `ButtonSpec` carries a `decision: Decision` field
- `on_button_pressed` routing lives in the base class; no subclass overrides it any more
- `action_accept_dialog` / `action_dismiss_dialog` are the only hooks subclasses use
- All `.save()` calls moved to callers (`nova_navigator.py`, `bookmarks_dialog.py`, `jobs.py`, `ssh.py`)

