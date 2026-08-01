# Security Audit & Bug Report

Date: 2026-07-31

---

## Critical

### SSH Command Injection

**File:** `src/nova_navigator/vfs/filesystems/ssh.py:216`

```python
_, stdout, stderr = self._ssh_client.exec_command(f"cd {path} && {command}")
```

The `path` variable (from user navigation) is interpolated directly into a shell command without escaping.
A crafted directory name like `/tmp/foo; rm -rf ~/; #` would execute arbitrary commands on the remote host.

**Fix:** Use `shlex.quote(path)` or switch to SFTP-based stat calls.

---

## High

### Insecure SSH Host Key Policy

**File:** `src/nova_navigator/vfs/filesystems/ssh.py:144-160`

`paramiko.AutoAddPolicy()` is used when `accept_host_key=True`, which silently accepts unknown SSH host keys — enabling MITM attacks.

**Fix:** Remove auto-accept and always require explicit user confirmation before adding a key.

---

### Polling-Based Future Wait (CPU Waste + Latency)

**File:** `src/nova_navigator/scheduler/scheduler.py:15-20`

```python
while not future.done():
    await asyncio.sleep(0.01)  # 10ms polling loop
```

This wastes CPU and adds up to 10ms latency per response.
Since the future lives in a different event loop, a proper cross-loop signaling mechanism (e.g., `threading.Event` + `asyncio.sleep(0)`) would be better.

**Fix:** Use a `threading.Event` to signal completion, then check the result.

---

### Potential Deadlock in Terminal Navigation

**File:** `src/nova_navigator/terminal/terminal.py:406-409`

```python
self._nav_future = asyncio.get_running_loop().create_future()
```

If the shell precmd hook never fires (shell crash, hook misconfiguration), this future is never resolved and `set_terminal_directory()` will await forever.

**Fix:** Add a timeout when awaiting `_nav_future`.

---

## Medium

### No Archive Bomb Protection

**Files:** `src/nova_navigator/archive/zip_archive.py`, `src/nova_navigator/archive/tar_archive.py`

No limits on member count or decompressed size.
A malicious archive with millions of entries or extreme compression ratios causes memory exhaustion.

**Fix:** Add configurable limits on member count and total decompressed size.

---

### TOCTOU Race in File Copy

**File:** `src/nova_navigator/filemanager/tasks.py:92-125`

The existence check (`dst_path.stat_or_none`) and the actual write happen with a gap, allowing symlink attacks or file replacement between the check and the write.

**Fix:** Use atomic file creation (exclusive open) or check-and-write under a lock.

---

### `copy_stat` Called Outside Error Handling

**File:** `src/nova_navigator/filemanager/tasks.py:142`

```python
dst_path.filesystem.copy_stat(dst_path, src_stat)  # After the try/finally block
```

If `copy_stat()` raises, the file is already written but has incorrect permissions/timestamps — and the error isn't handled by the cleanup logic.

**Fix:** Move `copy_stat()` inside the try block, or wrap it in its own error handler.

---

### Race in Scheduler Response Cache

**File:** `src/nova_navigator/scheduler/scheduler.py:92-93`

```python
if title in self._responses_to_all:  # Check without lock
    return self._responses_to_all[title]
async with self._request_lock:       # Lock acquired after check
```

Between the first check and lock acquisition, concurrent tasks could both miss the cache.
The second check inside the lock mitigates data corruption, but the first task may still show a redundant dialog.

**Fix:** Move the first check inside the lock, or accept the benign race (documented).

---

## Low

### Stale VPath Stat Cache

**File:** `src/nova_navigator/vfs/vpath.py`

The `_stat` attribute is lazily cached and never invalidated.
After file operations modify the filesystem, previously-created VPath objects carry stale stat info.

**Fix:** Add a cache invalidation mechanism or provide a non-cached stat option used after mutations.

---

### Missing Bounds Check on Terminal Message

**File:** `src/nova_navigator/terminal/terminal.py:535`

`message[1]` accessed without verifying the message has at least 2 elements.
A malformed `pre_cmd` message would crash with `IndexError`.

**Fix:** Add `len(message) >= 2` guard before accessing `message[1]`.

---

### PTY File Descriptor Leak on Error

**File:** `src/nova_navigator/terminal/pty_backend.py:223-240`

If `resize()` or subsequent setup fails after `os.fdopen(fd, ...)`, the file object isn't closed in the error path, leaking file descriptors.

**Fix:** Wrap post-fdopen setup in try/except that closes the file object on failure.
