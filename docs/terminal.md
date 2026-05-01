# Terminal Sub-Package

Architecture documentation for `nova_navigator/terminal/`.

---

## Overview

The terminal sub-package embeds an interactive shell inside the Textual application.
It is split into three layers, each with a single responsibility:

- **PtyBackend** — OS-level process and PTY management (start, stop, read, write, resize, signal).
- **ShellDriver** — shell-language knowledge (init hooks, argument quoting, precmd parsing).
- **Terminal** — Textual widget that owns rendering, draining, and event handling.

Each layer is unaware of the layers above it.
`PtyBackend` knows nothing about shell syntax.
`ShellDriver` knows nothing about rendering or Textual.
`Terminal` delegates downward to both.

---

## Package Layout

```
nova_navigator/terminal/
├── __init__.py          # Public API re-exports
├── pty_backend.py       # PtyBackend ABC + LocalPtyBackend
├── shell_driver.py      # ShellDriver ABC + ZshDriver, BashDriver, FallbackDriver, detect_driver()
└── terminal.py          # Terminal widget, TerminalDisplay, TerminalPyteScreen
```

---

## PtyBackend Layer

`PtyBackend` is an abstract base class defining the contract for terminal process backends.
It manages the lifecycle of a shell process and provides byte-level I/O.

### ABC Contract

| Method | Purpose |
|--------|---------|
| `open(command, rows, cols)` | Start the shell process; return precmd pipe fd or None |
| `write(data)` | Write raw bytes to the shell's stdin |
| `resize(rows, cols)` | Resize the terminal via `TIOCSWINSZ` |
| `resume()` | Send `SIGCONT` to the shell process |
| `attach_readers(loop, recv_queue)` | Register asyncio reader callbacks for stdout and precmd pipe |
| `detach_readers()` | Remove reader callbacks |
| `teardown()` | Terminate the process and close all file descriptors |
| `supports_precmd_pipe` | Property — True if the backend has an out-of-band precmd pipe |

### LocalPtyBackend

`LocalPtyBackend` is the concrete implementation for local shell processes.
It uses `pty.fork()` to create a pseudo-terminal.
It creates an `os.pipe()` for the out-of-band precmd communication channel.

In `open()`, the child process closes the parent end of the precmd pipe, marks the child fd inheritable, and calls `os.execvpe()`.
The parent process closes the child end, stores the master fd and precmd reader, and applies the initial terminal size.

Reader callbacks registered via `attach_readers()` push `["stdout", ...]` and `["pre_cmd", ...]` messages into the `recv_queue`.
On read errors, a `["disconnect", 1]` message is pushed.

### Lifecycle

The full lifecycle is: `open()` → `attach_readers()` → normal operation → `detach_readers()` → `teardown()`.
`teardown()` sends `SIGTERM`, reaps the child with `waitpid(WNOHANG)`, and closes file objects.

---

## ShellDriver Layer

`ShellDriver` is an abstract base class that isolates all shell-language knowledge from the Terminal widget.

### ABC Contract

| Method | Purpose |
|--------|---------|
| `init_code(precmd_fd)` | Return shell code to inject at startup (installs precmd hook) |
| `quote(arg)` | Return a shell-safe quoted form of an argument |
| `cd_command(path)` | Return a complete `cd` command string |
| `supports_stop_resume` | Property — True if init code includes `kill -STOP $$` |
| `parse_precmd_payload(raw)` | Parse precmd pipe output into `(pid, cwd)` |

### Concrete Drivers

**ZshDriver** — installs a precmd hook via `precmd_functions+=(_nn_precmd)`.
The hook writes `PID:CWD` to the precmd pipe and sends `kill -STOP $$`.

**BashDriver** — installs a precmd hook via `PROMPT_COMMAND`.
Uses the same hook body and quoting as ZshDriver.

**FallbackDriver** — generic POSIX sh driver with no SIGSTOP synchronisation.
Installs a simple `PS1`-based hook that writes only the CWD (no PID).
Uses `printf '%b_'` with octal escapes for cd commands (Midnight Commander technique).

### Quoting

`ZshDriver` and `BashDriver` use ANSI-C `$'...'` quoting with octal escapes for every byte outside `[a-zA-Z0-9/._-]`.
Line continuations (`\\\n`) are inserted every 250 bytes to stay within kernel cooked-mode buffer limits.

`FallbackDriver.cd_command()` uses POSIX octal escapes via `printf '%b_'` because POSIX sh does not support `$'...'`.

### detect_driver()

`detect_driver(command)` inspects the basename of the first word in the command string.
It returns `ZshDriver` for `zsh`, `BashDriver` for `bash`, and `FallbackDriver` for anything else.

---

## SIGSTOP Synchronisation

The core design challenge is directory navigation: the Terminal widget needs to send `cd /path` to the shell and suppress the echo, without a race between the shell printing its prompt and the widget ending suppression.

### The Problem

Without synchronisation, a `pre_cmd` message from a previous command can arrive after a new `cd` is enqueued.
This stale `pre_cmd` clears the draining flag too early, causing the `cd` echo to leak onto screen.
See `docs/terminal-set-directory-race.md` for the original race condition analysis.

### The Solution

The precmd hook in ZshDriver and BashDriver ends with `kill -STOP $$`.
This freezes the shell process after it writes `PID:CWD` to the precmd pipe but before it prints the next prompt.

The Terminal widget's `recv()` loop processes the `pre_cmd` message, clears draining, restores any yanked input, and then calls `backend.resume()` to send `SIGCONT`.
Because the shell is frozen, there is zero window for a race — the shell cannot produce output between the precmd write and the resume.

This eliminates all three race conditions documented in `terminal-set-directory-race.md`.

---

## Directory Navigation Flow

A walkthrough of `set_terminal_directory(path)`:

1. **Check for typed input.** `has_input()` compares the cursor position to `_prompt_cursor_x`.
2. **Save typed text.** If the user has typed something, send `Ctrl+U` (kill line) to save it to the shell's kill ring.
3. **Enable draining.** Set `_draining = True` so `recv()` suppresses subsequent stdout.  Increment `_nav_pending`.
4. **Send cd command.** Write ` cd <quoted_path>\n` to the backend (leading space for history exclusion).
5. **Await completion.** The method creates an `asyncio.Future` and awaits it.  The future resolves when `_nav_pending` reaches zero.
6. **Shell executes cd.** The shell changes directory and runs the precmd hook.
7. **Precmd hook fires.** The hook writes `PID:CWD` to the precmd pipe and sends `kill -STOP $$`.
8. **Shell freezes.** The shell is stopped; no prompt is rendered.
9. **recv() processes pre_cmd.** Decrements `_nav_pending`.  If it reaches zero: clears `_draining`, resolves the future with the actual CWD, and sets `_snapshot_prompt_cursor = True`.
10. **Restore typed text.** If `_pending_yank` was set, the widget writes `Ctrl+Y` (yank) and `Ctrl+E` (end of line) before resuming.
11. **Resume shell.** `backend.resume()` sends `SIGCONT`; the shell prints its prompt.
12. **Snapshot cursor.** On the next display rebuild, `_prompt_cursor_x` is captured from the new prompt position.

### Rapid Panel Switching

When the user switches panels faster than the shell can process cd commands, multiple navigations may be in flight.
The `_nav_pending` counter tracks how many navigations have not yet been acknowledged by a `pre_cmd`.
Draining stays on until the counter reaches zero, preventing intermediate cd echoes from leaking.

The caller (`MainScreen._set_terminal_directory`) wraps the call in `asyncio.create_task` and cancels the previous task before starting a new one.
It captures the requesting panel at call time so the result updates the correct panel, even if the active panel changes before the cd completes.
The `_on_terminal_pre_cmd` handler ignores `PreCmd` events while a programmatic navigation is in flight, so only user-typed cd commands update the active panel.

### Awaitable Return Value

`set_terminal_directory` returns the actual CWD reported by the shell (a `PurePath`).
For `FallbackDriver` (no stop/resume), it returns the requested path immediately without blocking.

### History Exclusion

Navigation cd commands must not pollute the shell's command history.
The cd command is prefixed with a leading space (step 4 above).
The init code also configures the shell to honour this convention:

- **zsh:** `setopt HIST_IGNORE_SPACE` — commands starting with a space are excluded from history.
- **bash:** `HISTCONTROL="${HISTCONTROL:+${HISTCONTROL}:}ignorespace"` — appended without overwriting user settings.

Both settings are idempotent and have no effect on user-typed commands that do not start with a space.

For `FallbackDriver`, steps 2, 6–10, and 12 are skipped.
The cd echo is visible and typed input is lost.

---

## Degraded Mode (FallbackDriver)

When the shell is not zsh or bash, `detect_driver()` returns `FallbackDriver`.

In degraded mode:
- `supports_stop_resume` is False.
- No `kill -STOP $$` is injected.
- Directory navigation echoes the `cd` command visibly.
- Typed input is lost on navigation.
- CWD tracking still works via a `PS1`-based precmd hook.
- The precmd payload contains only the path (no PID).

This is an accepted trade-off for shells that lack `precmd_functions` or `PROMPT_COMMAND`.

---

## recv_queue Message Protocol

The `recv_queue` is an `asyncio.Queue[list[object]]` shared between the backend's reader callbacks and the Terminal widget's `recv()` loop.

| Message | Format | Source | Purpose |
|---------|--------|--------|---------|
| `setup` | `["setup", {}]` | `_run()` | Initial setup signal after readers are attached |
| `stdout` | `["stdout", str]` | `on_output` reader | Shell output bytes (decoded) |
| `pre_cmd` | `["pre_cmd", str]` | `on_pre_cmd` reader | Precmd pipe payload (`PID:CWD` or just CWD) |
| `disconnect` | `["disconnect", int]` | `on_output` reader | Shell process exited or read error |

The `recv()` loop drains up to `_RECV_DRAIN_LIMIT` (100) messages per wakeup to batch processing.
When `_draining` is True, `stdout` messages are silently discarded.

---

## Extending for New Backends

To add a new backend (e.g. SSH-based PTY):

1. Subclass `PtyBackend`.
2. Implement all abstract methods: `open()`, `write()`, `resize()`, `resume()`, `attach_readers()`, `detach_readers()`, `teardown()`.
3. Set `supports_precmd_pipe` to True if the backend can relay an out-of-band pipe, or False if not.
4. Pass the backend instance to `Terminal(command, backend=my_backend)`.

If `supports_precmd_pipe` is False, `ShellDriver.init_code()` receives None for the fd and should return an empty string.
CWD tracking and SIGSTOP synchronisation are unavailable without the precmd pipe.

---

## Extending for New Shells

To add a new shell driver:

1. Subclass `ShellDriver`.
2. Implement `init_code(precmd_fd)` — install a precmd hook that writes CWD (and optionally PID) to the fd.
3. Implement `quote(arg)` — return a safely quoted string for that shell's syntax.
4. Implement `parse_precmd_payload(raw)` — parse the hook's output.
5. Set `supports_stop_resume` to True if the hook includes `kill -STOP $$`.
6. Update `detect_driver()` in `shell_driver.py` to recognise the shell name.

If the shell supports a precmd mechanism but not `kill -STOP $$`, set `supports_stop_resume` to False.
The driver will work like `FallbackDriver` — CWD tracking without synchronised navigation.
