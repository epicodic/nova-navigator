# Design: Terminal Sub-Package Refactoring

Date: 2026-05-01
Status: Approved

## Background

The `Terminal` widget in `src/nova_navigator/widgets/terminal.py` is a 680-line monolith mixing three concerns:

1. **PTY transport** — `pty.fork`, fd management, `fcntl.ioctl`, `SIGTERM`/`waitpid`
2. **Shell language** — zsh-specific init hooks, `shlex.quote` for cd, `precmd_functions`
3. **Terminal rendering** — pyte screen, Rich text, draining state machine, Textual widget

Two bugs motivate this refactoring:

1. **Race condition in `set_terminal_directory`** — a stale precmd from a previous navigation can clear the `_draining` flag that belongs to a later navigation.
The current workaround is a `_nav_pending` counter with `nav_start` barrier messages.
The root cause is that the shell runs freely and Nova Navigator tries to suppress output asynchronously.

2. **No extensibility** — adding bash support, a POSIX sh fallback, or an SSH-backed terminal requires changes scattered across `terminal.py`.

### Root cause of the race

Midnight Commander solves this cleanly: the shell sends `kill -STOP $$` in its precmd hook after writing the cwd to the pipe.
The shell is frozen until MC sends `SIGCONT`.
This makes the precmd an unambiguous synchronisation point.
Nova Navigator's current design runs the shell freely and tries to suppress its output asynchronously, which creates an unavoidable race window.

### Prior design documents

Two earlier documents informed this design:

- `docs/agents/specs/2026-05-01-terminal-shell-driver-design.md` — proposed `PtyBackend` + `ShellDriver` extraction with SIGSTOP synchronisation.
- `docs/terminal-ssh-refactor.md` — proposed a `TerminalBackend` protocol with fat-backend API and OSC 7 CWD tracking for SSH.

This spec reconciles both, taking the `ShellDriver` extraction and SIGSTOP model from the first, and the SSH CWD story from the second.

---

## Goals

- Eliminate the `_nav_pending` counter and `nav_start` barrier by using `kill -STOP $$` for local shells that support it (bash, zsh).
- Isolate shell-specific knowledge (hook code, argument quoting) in a `ShellDriver` ABC.
- Isolate OS-level PTY plumbing in a `PtyBackend` ABC, preparing for SSH terminal support.
- Move all terminal code into a `nova_navigator/terminal/` sub-package.
- Provide a `FallbackDriver` path where the cd command is visible to the user and no SIGSTOP is used (acceptable degraded behaviour for POSIX sh and future SSH).

---

## Non-goals

- SSH terminal support is not implemented in this iteration; only the abstractions that make it possible are put in place.
- Supporting fish, tcsh, ksh, or mksh is not in scope; only bash, zsh, and a POSIX sh fallback.
- No changes to the pyte screen rendering, display rebuild, mouse handling, or Textual widget structure.

---

## Package structure

### New layout

```
src/nova_navigator/terminal/
    __init__.py          # re-exports: Terminal, ShellDriver, PtyBackend, detect_driver
    terminal.py          # Terminal widget (trimmed)
    pty_backend.py       # PtyBackend ABC + LocalPtyBackend
    shell_driver.py      # ShellDriver ABC + ZshDriver, BashDriver, FallbackDriver, detect_driver()
```

### What moves

`widgets/terminal.py` → `terminal/terminal.py`.
The `widgets/` directory no longer contains any terminal code.

### Test layout

```
tests/terminal/
    __init__.py
    test_terminal.py      # existing tests (moved, imports updated)
    test_pty_backend.py   # new: LocalPtyBackend lifecycle tests
    test_shell_driver.py  # new: driver unit tests (pure, no PTY)
```

### Import path changes

| Before | After |
|---|---|
| `from nova_navigator.widgets.terminal import Terminal` | `from nova_navigator.terminal import Terminal` |
| `from nova_navigator.widgets.terminal import shell_cmd_cd` | Removed (moved to ShellDriver) |
| `from nova_navigator.widgets.terminal import shell_init_code` | Removed (moved to ShellDriver) |
| `from nova_navigator.widgets.terminal import shell_clear_prompt` | Removed entirely |

Call sites to update:
- `src/nova_navigator/nova_navigator.py` — imports `Terminal`
- `tests/widgets/test_terminal.py` — moves to `tests/terminal/test_terminal.py`

---

## Architecture

### Relationship

```
Terminal
  ├── PtyBackend   (how the shell process is started and I/O flows)
  └── ShellDriver  (what shell language to use for hooks and quoting)
```

`Terminal` selects defaults automatically:

```python
Terminal(command="/usr/bin/zsh")
# → LocalPtyBackend(), detect_driver("/usr/bin/zsh") → ZshDriver()

Terminal(command="/usr/bin/bash")
# → LocalPtyBackend(), detect_driver("/usr/bin/bash") → BashDriver()
```

---

## `PtyBackend` ABC

File: `src/nova_navigator/terminal/pty_backend.py`

```python
class PtyBackend(ABC):
    @abstractmethod
    def open(self, command: str, rows: int, cols: int) -> int | None:
        """Start the shell.
        Returns the precmd pipe child-side fd number for embedding in init_code,
        or None if this backend does not support a precmd pipe.
        """

    @abstractmethod
    def write(self, data: bytes) -> None:
        """Write raw bytes to the shell's stdin."""

    @abstractmethod
    def resize(self, rows: int, cols: int) -> None:
        """Resize the terminal (TIOCSWINSZ for local, channel.resize_pty() for SSH)."""

    @abstractmethod
    def resume(self) -> None:
        """Send SIGCONT to the managed shell process.
        No-op for backends that do not support stop/resume (e.g. SSH).
        The backend owns the pid internally.
        """

    @abstractmethod
    def attach_readers(
        self,
        loop: asyncio.AbstractEventLoop,
        recv_queue: asyncio.Queue[list[object]],
    ) -> None:
        """Register callbacks that pump stdout and precmd data into recv_queue.
        LocalPtyBackend uses loop.add_reader().
        SSH backends would use a reader thread.
        The backend stores the loop reference internally.
        """

    @abstractmethod
    def detach_readers(self) -> None:
        """Remove previously registered callbacks.
        Uses the loop reference stored during attach_readers().
        """

    @abstractmethod
    def teardown(self) -> None:
        """Terminate the shell process and close all file objects."""

    @property
    @abstractmethod
    def supports_precmd_pipe(self) -> bool:
        """True if this backend creates a separate out-of-band precmd pipe.
        False for SSH, where the precmd output travels via the main PTY channel.
        """
```

### `LocalPtyBackend`

Direct extraction of the current code with no behaviour changes:

- `open()`: calls `os.pipe()` + `pty.fork()` + `execvpe`.
  Returns `fd_pre_cmd_child` (the write-end fd number inherited by the child).
  Stores `pid` internally.
- `write()`: `self._p_out.write(data)`.
- `resize()`: `fcntl.ioctl(TIOCSWINSZ)`.
- `resume()`: `os.kill(self._pid, signal.SIGCONT)`.
  Suppresses `ProcessLookupError` (shell already exited).
- `attach_readers()`: `loop.add_reader(self._p_out, on_output)` + `loop.add_reader(self._p_out_pre_cmd, on_pre_cmd)`.
  Stores the loop reference.
- `detach_readers()`: `loop.remove_reader(...)` on both fds using stored loop reference.
- `teardown()`: `os.kill(SIGTERM)` + `os.waitpid()` + close file objects.
- `supports_precmd_pipe = True`.

---

## `ShellDriver` ABC

File: `src/nova_navigator/terminal/shell_driver.py`

```python
class ShellDriver(ABC):
    @abstractmethod
    def init_code(self, precmd_fd: int | None) -> str:
        """Shell code injected once at startup via the PTY.
        Sets up the precmd hook and (for stop/resume drivers) includes kill -STOP $$.
        precmd_fd is None when the backend has no separate precmd pipe.
        """

    @abstractmethod
    def quote(self, arg: str) -> str:
        """Return a shell-safe quoted form of arg.
        Uses ANSI-C $'...\\ooo...' quoting for bash/zsh.
        Escapes all bytes outside [a-zA-Z0-9/._-] as \\ooo (octal).
        Inserts line continuations every 250 bytes.
        """

    def cd_command(self, path: str) -> str:
        """Return a complete shell command that changes directory to path.
        Default implementation: 'cd <quoted-path>'.
        FallbackDriver overrides with the printf '%b_' trick.
        """
        return f"cd {self.quote(path)}"

    @property
    @abstractmethod
    def supports_stop_resume(self) -> bool:
        """True if init_code() includes kill -STOP $$ and resume() is expected."""

    @abstractmethod
    def parse_precmd_payload(self, raw: str) -> tuple[int | None, PurePath]:
        """Parse a raw precmd pipe message.
        Returns (shell_pid_or_None, cwd).
        shell_pid is None when stop/resume is not used.
        """
```

### Shared quoting helper

A module-level function used by both `ZshDriver` and `BashDriver`:

```python
def _ansi_c_quote(arg: str) -> str:
    """ANSI-C $'...' quoting with octal escapes and 250-byte line continuations."""
```

### Concrete drivers

#### `ZshDriver`

```
init_code(fd):
    " _nn_precmd() { printf '%d:%s\n' $$ $(pwd) >&{fd}; kill -STOP $$ };"
    " precmd_functions+=(_nn_precmd)\n"

quote(arg):  _ansi_c_quote(arg)

cd_command(path):  default (f"cd {self.quote(path)}")

supports_stop_resume = True

parse_precmd_payload("12345:/home/user\n") → (12345, PurePath("/home/user"))
```

#### `BashDriver`

```
init_code(fd):
    " _nn_precmd() { printf '%d:%s\n' $$ $(pwd) >&{fd}; kill -STOP $$; };"
    " PROMPT_COMMAND=${PROMPT_COMMAND:+${PROMPT_COMMAND}$'\n'}_nn_precmd\n"

quote(arg):  _ansi_c_quote(arg)

cd_command(path):  default (f"cd {self.quote(path)}")

supports_stop_resume = True

parse_precmd_payload: same as ZshDriver
```

#### `FallbackDriver`

For generic POSIX sh.
No stop/resume.
The cd command appears on screen (accepted degraded behaviour).
Typed input is lost on navigation (`_pending_yank` is not set for fallback drivers).

```
init_code(fd):
    If fd is not None:
        " MC_PRECMD() { pwd >&{fd}; }; PS1='$(MC_PRECMD)'"'"$PS1\n"
    Else:
        ""  (no hook)

quote(arg):
    Not used directly — cd_command() overrides.

cd_command(path):
    Returns the MC-style printf command:
    "_mc_newdir_=`printf '%b_' '{octal-escaped}'`; cd \"${_mc_newdir_%_}\""

supports_stop_resume = False

parse_precmd_payload("/home/user\n") → (None, PurePath("/home/user"))
```

### `detect_driver(command: str) -> ShellDriver`

```python
def detect_driver(command: str) -> ShellDriver:
    name = PurePath(command.split()[0]).name
    if name == "zsh":
        return ZshDriver()
    if name == "bash":
        return BashDriver()
    return FallbackDriver()
```

---

## `Terminal` changes

### Constructor

```python
def __init__(
    self,
    command: str,
    backend: PtyBackend | None = None,
    driver: ShellDriver | None = None,
    name: str | None = None,
    id: str | None = None,
    classes: str | None = None,
    keep_alive: bool = False,
) -> None:
    self.command = command
    self._backend = backend or LocalPtyBackend()
    self._driver = driver or detect_driver(command)
    ...
```

### State removed

| Removed | Reason |
|---|---|
| `_nav_pending: int` | Replaced by SIGSTOP synchronisation |
| `nav_start` recv_queue messages | No longer needed |
| `pid`, `fd`, `fd_pre_cmd`, `fd_pre_cmd_child` | Owned by backend |
| `_p_out`, `_p_out_pre_cmd` | Owned by backend |
| `_loop` stored reference | Backend manages its own readers |

### State retained

| Kept | Reason |
|---|---|
| `_draining: bool` | Suppress cd echo between write and SIGSTOP |
| `_pending_yank: bool` | Restore typed text after navigation |
| `_snapshot_prompt_cursor: bool` | Capture cursor after new prompt |
| `_prompt_cursor_x: int` | `has_input()` baseline |

### Module-level functions removed

| Removed | Replacement |
|---|---|
| `shell_init_code()` | `ShellDriver.init_code()` |
| `shell_cmd_cd()` | `ShellDriver.cd_command()` |
| `shell_clear_prompt()` | Deleted — unused in production code |

### `_spawn_pty()` → `_start_backend()`

```python
def _start_backend(self) -> None:
    precmd_fd = self._backend.open(self.command, self.nrow, self.ncol)
    self.send_queue = asyncio.Queue()
    self._run_task = asyncio.create_task(self._run())
    if self._driver.supports_stop_resume:
        self._draining = True
    init = self._driver.init_code(precmd_fd)
    if init:
        self._backend.write(init.encode())
```

When `supports_stop_resume` is True, the shell will stop after the first precmd.
`recv()` sends SIGCONT then and ends draining.
No `_nav_pending` needed — the stop is the barrier.

### `set_terminal_directory()` simplified

```python
async def set_terminal_directory(self, path: PurePath) -> None:
    if not self._started:
        return
    if self._driver.supports_stop_resume:
        self._pending_yank = self.has_input()
        if self._pending_yank:
            self._backend.write(_KILL_LINE.encode())
    cmd = self._driver.cd_command(str(path)) + "\n"
    if self._driver.supports_stop_resume:
        self._draining = True
    self._backend.write(cmd.encode())
```

For `FallbackDriver`: no yank, no draining.
The cd is written directly and its echo is visible.

### `send()` simplified

```python
async def send(self, data: str, mode: Literal["normal", "silent"] = "normal") -> None:
    if not self._started:
        return
    if mode == "silent" and self._driver.supports_stop_resume:
        self._draining = True
    self._backend.write(data.encode())
```

No `nav_start` injection.
No counter.
Draining is set synchronously before the write.

### `recv()` pre_cmd handler simplified

```python
elif cmd == "pre_cmd":
    raw = str(message[1])
    pid, cwd = self._driver.parse_precmd_payload(raw)
    if self._draining:
        if self._pending_yank:
            self._pending_yank = False
            self._backend.write((_YANK + _END_OF_LINE).encode())
        self._backend.resume()
        self._draining = False
    self._snapshot_prompt_cursor = True
    self.post_message(Terminal.PreCmd(self, cwd))
```

### `_run()` simplified

```python
async def _run(self) -> None:
    loop = asyncio.get_running_loop()
    assert self.recv_queue is not None
    self._backend.attach_readers(loop, self.recv_queue)
    self.recv_queue.put_nowait(["setup", {}])
    try:
        assert self.send_queue is not None
        while True:
            msg = list(await self.send_queue.get())
            if msg[0] == "stdin":
                self._backend.write(str(msg[1]).encode())
            elif msg[0] == "set_size":
                self._backend.resize(int(msg[2]), int(msg[1]))
            elif msg[0] in ("click", "scroll"):
                self._backend.write(_encode_mouse(msg))
    except asyncio.CancelledError:
        pass
```

Mouse encoding extracted into a `_encode_mouse(msg)` helper — stays in `terminal.py`.
All `fcntl`, `struct`, `termios`, `signal` imports move to `pty_backend.py`.

### `stop()` updated

```python
def stop(self) -> None:
    if not self._started:
        return
    self._display = self.initial_display()
    self._started = False
    if self._rebuild_handle is not None:
        self._rebuild_handle.cancel()
        self._rebuild_handle = None
    if self.recv_task_t is not None:
        self.recv_task_t.cancel()
    if self._run_task is not None:
        self._run_task.cancel()
    self._backend.detach_readers()
    self._backend.teardown()
```

### `respawn()` updated

```python
def respawn(self) -> None:
    if self._run_task is not None:
        self._run_task.cancel()
        self._run_task = None
    self._backend.detach_readers()
    self._backend.teardown()
    self._screen = TerminalPyteScreen(self.ncol, self.nrow)
    self._stream = pyte.Stream(self._screen)
    self._start_backend()
```

---

## `set_terminal_directory` timing diagram (SIGSTOP case)

```
Nova Navigator                         Shell (zsh/bash)
──────────────────────────────────     ──────────────────────────────────
_draining = True
backend.write("cd /path\n")
                                   →   shell runs: cd /path
                                       precmd fires:
                                         printf "$$:$(pwd)\n" >&pipe
                                         kill -STOP $$           ← shell pauses here
recv_queue ← ["pre_cmd", "PID:/path"]
  write YANK+EOL if pending
  backend.resume()                 →   shell resumes, prints new prompt
_draining = False
_snapshot_prompt_cursor = True
  next rebuild snapshots cursor
```

## `set_terminal_directory` timing diagram (FallbackDriver case)

```
Nova Navigator                         Shell
──────────────────────────────────     ──────────────────────────────────
backend.write("cd /path\n")        →   cd /path (echoed and executed)
                                       precmd fires: printf "$(pwd)\n" >&pipe
recv_queue ← ["pre_cmd", "/path"]
_snapshot_prompt_cursor = True
  next rebuild snapshots cursor
```

cd echo is visible.
No draining.
No SIGSTOP.
Typed input is lost (no yank dance).

---

## Error handling

- If the shell process exits before sending SIGSTOP, `resume()` is called on a dead pid.
  `LocalPtyBackend.resume()` catches `ProcessLookupError` and suppresses it.
- If `parse_precmd_payload` receives malformed data (e.g. no `:` separator), it logs a warning and returns `(None, PurePath("/"))` so the app does not crash.
- If `_backend.open()` raises, `Terminal.start()` catches it, logs the error, and leaves `_started = False`.

---

## Testing

### `ShellDriver` unit tests (pure — no PTY)

File: `tests/terminal/test_shell_driver.py`

- `ZshDriver.init_code(7)` embeds `>&7` and `kill -STOP $$`.
- `ZshDriver.quote("/home/user/O'Brien")` produces a correctly escaped `$'...'` string.
- `ZshDriver.parse_precmd_payload("12345:/home/user\n")` returns `(12345, PurePath("/home/user"))`.
- `ZshDriver.cd_command("/tmp")` returns `"cd $'...'"`.
- `BashDriver.init_code(5)` uses `PROMPT_COMMAND`.
- `FallbackDriver.supports_stop_resume` is `False`.
- `FallbackDriver.cd_command(...)` returns the MC-style printf statement.
- `detect_driver("/usr/bin/zsh")` returns `ZshDriver`.
- `detect_driver("/bin/bash")` returns `BashDriver`.
- `detect_driver("/bin/sh")` returns `FallbackDriver`.
- `_ansi_c_quote` handles paths with special characters, long paths (line continuation), and empty strings.

### `LocalPtyBackend` tests

File: `tests/terminal/test_pty_backend.py`

- Existing PTY lifecycle tests that call `terminal.start()` / `terminal.stop()` are adapted.
- `resume()` on a dead pid does not raise.

### `Terminal` tests (adapted existing + new)

File: `tests/terminal/test_terminal.py`

Existing tests from `tests/widgets/test_terminal.py` are moved and imports updated.

Tests removed:
- Tests for `shell_init_code()`, `shell_cmd_cd()`, `shell_clear_prompt()` module-level functions — replaced by ShellDriver tests.

Tests adapted:
- Race condition tests (`test_race_a_*`, `test_race_c_*`) are kept as safety nets, adapted to the new design.
  They verify that SIGSTOP-based synchronisation prevents stale precmds from interfering.
- Draining tests updated to remove `_nav_pending` references.

New tests with `FakePtyBackend`:
- A `FakePtyBackend` that records `write()` calls, allows posting to `recv_queue`, and records `resume()` calls.
- `set_terminal_directory` sets `_draining = True` and calls `backend.write(cd_cmd)`.
- On `pre_cmd`, `_draining` is cleared, `resume()` is called, cursor snapshot is armed.
- `FallbackDriver`: `_draining` is never set; cd is written normally; `pre_cmd` arms snapshot directly.

---

## Migration: public API

`Terminal.__init__` gains two optional keyword arguments (`backend`, `driver`).
Existing call sites pass only `command` and are unaffected (defaults auto-select `LocalPtyBackend` and `detect_driver`).

`__init__.py` re-exports:
- `Terminal`
- `TerminalDisplay`
- `TerminalPyteScreen`
- `PtyBackend`
- `LocalPtyBackend`
- `ShellDriver`
- `ZshDriver`
- `BashDriver`
- `FallbackDriver`
- `detect_driver`

---

## Imports moved from `terminal.py`

| Module | Moves to |
|---|---|
| `fcntl`, `struct`, `termios`, `signal`, `pty` | `pty_backend.py` |
| `shlex` | Removed (replaced by `_ansi_c_quote` in `shell_driver.py`) |

`terminal.py` retains: `asyncio`, `contextlib`, `logging`, `os`, `re`, and the Textual/pyte/Rich imports.

---

## Documentation

### Architecture document

Create `docs/terminal.md` (replacing the existing file) as the definitive architecture reference for the terminal sub-package.
Structure:

1. **Overview** — what the terminal sub-package does, the three-layer architecture (Terminal widget, PtyBackend, ShellDriver), and how they relate.

2. **Package layout** — file listing with one-line descriptions.

3. **PtyBackend layer** — purpose, ABC contract, how `LocalPtyBackend` implements it.
   Lifecycle: `open()` → `attach_readers()` → (normal operation) → `detach_readers()` → `teardown()`.
   Reader callback mechanism: how `add_reader` pumps data into `recv_queue`.

4. **ShellDriver layer** — purpose, ABC contract, per-driver differences.
   Hook installation: how `init_code()` sets up `precmd` for each shell.
   Quoting: ANSI-C `$'...\ooo...'` scheme, why not `shlex.quote()`, 250-byte line continuations.
   `cd_command()` default vs FallbackDriver override.

5. **SIGSTOP synchronisation model** — the core mechanism explained in detail.
   Why the shell sends `kill -STOP $$` after writing to the precmd pipe.
   How `resume()` releases the shell.
   Why this eliminates the race condition that the `_nav_pending` counter solved.
   Timing diagrams for both SIGSTOP and FallbackDriver paths.

6. **Directory navigation flow** — step-by-step walkthrough of `set_terminal_directory()`.
   Input preservation: KILL_LINE → cd → precmd → YANK + END_OF_LINE.
   The `_draining` flag: what it suppresses and when it clears.
   `_snapshot_prompt_cursor`: how the prompt position is captured after the new prompt renders.

7. **Degraded mode (FallbackDriver)** — what works differently when SIGSTOP is unavailable.
   cd echo is visible.
   Typed input is lost.
   No draining.

8. **recv_queue message protocol** — table of all message types (`stdout`, `pre_cmd`, `setup`, `disconnect`, `set_size`, `stdin`, `click`, `scroll`) with format and producer/consumer.

9. **Extending for new backends** — what to implement for SSH or other backends.
   Which ShellDriver to pair with which backend.
   How `attach_readers` accommodates thread-based I/O.

10. **Extending for new shells** — what to implement for a new ShellDriver subclass.

### Code documentation

Each source file in `nova_navigator/terminal/` must have:

- **Module docstring** — one paragraph explaining the file's role in the architecture and its relationship to the other files.

- **Class docstrings** — for every ABC and concrete class.
  ABCs: explain the contract and what implementors must provide.
  Concrete classes: explain the specific strategy used (e.g. "Uses `pty.fork()` and `os.pipe()` for precmd communication").

- **Method docstrings** — for all public and ABC methods (Google style).
  Include parameter semantics, return value meaning, and any important side effects.

- **Inline comments** — for non-obvious mechanisms:
  - The SIGSTOP/SIGCONT handshake in the precmd hook.
  - Why `_draining` is set synchronously before `backend.write()`.
  - The ANSI-C quoting algorithm.
  - The 250-byte line continuation rule.

### Existing docs to update

- `docs/terminal.md` — replaced entirely (see above).
- `docs/terminal-set-directory-race.md` — add a section at the top noting that the race is resolved by the SIGSTOP model, with a link to the new architecture doc.
  Keep the analysis for historical reference.
- `AGENTS.md` — update the architecture section to reflect the new `terminal/` sub-package instead of `widgets/terminal.py`.
