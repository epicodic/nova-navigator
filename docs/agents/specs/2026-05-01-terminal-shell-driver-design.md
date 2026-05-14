# Design: Terminal Shell Driver Abstraction

Date: 2026-05-01
Status: Draft

## Background

The `Terminal` widget in `src/nova_navigator/widgets/terminal.py` is a PTY-backed terminal emulator.
It currently hardcodes zsh-specific shell hooks and mixes OS-level PTY plumbing with shell language details.

Two bugs motivate this redesign:

1. **Race condition in `set_terminal_directory`** — a stale precmd from a previous navigation can clear the `_draining` flag that belongs to a later navigation, causing the `cd` echo to appear on screen and snapshotting the prompt cursor at the wrong position.
The `_nav_pending` counter (already implemented) is the current workaround.

2. **No extensibility** — adding bash support, a POSIX sh fallback, or an SSH-backed terminal requires changes scattered across `terminal.py`.

### Root cause of the race

MC solves this cleanly: the shell sends `kill -STOP $$` in its precmd hook after writing the cwd to the pipe.
The shell is frozen until Nova Navigator sends `SIGCONT`.
This makes the precmd an unambiguous synchronisation point: no output can arrive between the precmd write and SIGCONT.
Nova Navigator's current design runs the shell freely and tries to suppress its output asynchronously, which creates an unavoidable race window.

---

## Goals

- Eliminate the `_nav_pending` counter and `nav_start` barrier by using `kill -STOP $$` for local shells that support it (bash, zsh).
- Isolate shell-specific knowledge (hook code, argument quoting) in a `ShellDriver` ABC.
- Isolate OS-level PTY plumbing in a `PtyBackend` ABC, preparing for SSH terminal support via Paramiko.
- Provide a `FallbackDriver` / `SshDriver` path where the cd command is visible to the user and no SIGSTOP is used (acceptable degraded behaviour for SSH and generic POSIX sh).

---

## Non-goals

- SSH terminal support is not implemented in this iteration; only the abstractions that make it possible are put in place.
- Supporting fish, tcsh, ksh, or mksh is not in scope; only bash, zsh, and a POSIX sh fallback.
- No changes to the pyte screen rendering, display rebuild, mouse handling, or Textual widget structure.

---

## Architecture

### New files

```
src/nova_navigator/widgets/
    terminal.py        # Terminal widget — trimmed, uses PtyBackend + ShellDriver
    pty_backend.py     # PtyBackend ABC + LocalPtyBackend
    shell_driver.py    # ShellDriver ABC + ZshDriver + BashDriver + FallbackDriver
                       # + SshShellDriver + detect_driver()
```

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

# SSH (future):
Terminal(command="zsh", backend=SshPtyBackend(channel), driver=ZshDriver())
```

---

## `PtyBackend` ABC

File: `src/nova_navigator/widgets/pty_backend.py`

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
    def resume(self, pid: int) -> None:
        """Send SIGCONT to the shell process.
        No-op for backends that do not support stop/resume (e.g. SSH).
        """

    @abstractmethod
    def attach_readers(
        self,
        loop: asyncio.AbstractEventLoop,
        recv_queue: asyncio.Queue[list[object]],
    ) -> None:
        """Register callbacks that pump stdout and precmd data into recv_queue.
        LocalPtyBackend uses loop.add_reader(); SshPtyBackend uses a reader thread.
        """

    @abstractmethod
    def detach_readers(self, loop: asyncio.AbstractEventLoop) -> None:
        """Remove previously registered callbacks."""

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

Implements the current behaviour:

- `open()`: calls `os.pipe()` + `pty.fork()` + `execvpe`.
  Returns `fd_pre_cmd_child` (the write-end fd number inherited by the child).
- `write()`: `self._p_out.write(data)`.
- `resize()`: `fcntl.ioctl(TIOCSWINSZ)`.
- `resume(pid)`: `os.kill(pid, signal.SIGCONT)`.
- `attach_readers()`: `loop.add_reader(self._p_out, on_output)` + `loop.add_reader(self._p_out_pre_cmd, on_pre_cmd)`.
- `detach_readers()`: `loop.remove_reader(...)` on both fds.
- `teardown()`: `os.kill(SIGTERM)` + `os.waitpid()` + close file objects.
- `supports_precmd_pipe = True`.

### `SshPtyBackend` (stub — not implemented in this iteration)

- `open()`: `channel.invoke_shell()`, `channel.resize_pty()`. Returns `None`.
- `write()`: `channel.send(data)`.
- `resize()`: `channel.resize_pty(cols, rows)`.
- `resume()`: no-op.
- `attach_readers()`: starts a daemon thread that reads from `channel` and posts to `recv_queue`.
- `supports_precmd_pipe = False`.

---

## `ShellDriver` ABC

File: `src/nova_navigator/widgets/shell_driver.py`

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
        """Return a shell-safe quoted form of arg using this shell's quoting rules.
        Used by Terminal to construct cd commands.
        """

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

### Concrete drivers

#### `ZshDriver`

```
init_code(fd):
    " _nn_precmd() { printf '%d:%s\n' $$ $(pwd) >&{fd}; kill -STOP $$ };"
    " precmd_functions+=(_nn_precmd)\n"

quote(arg):
    Use $'...\ooo...' ANSI-C quoting (same as MC bash/zsh).
    Escape all bytes outside [a-zA-Z0-9/._-] as \ooo (octal).
    Wrap long paths at 250 bytes with line continuation $'\\\n$'.

supports_stop_resume = True

parse_precmd_payload("12345:/home/user\n") → (12345, PurePath("/home/user"))
```

#### `BashDriver`

```
init_code(fd):
    " _nn_precmd() { printf '%d:%s\n' $$ $(pwd) >&{fd}; kill -STOP $$; };"
    " PROMPT_COMMAND=${PROMPT_COMMAND:+${PROMPT_COMMAND}$'\n'}_nn_precmd\n"
    (The $'\n' is a bash ANSI-C literal newline used as the PROMPT_COMMAND separator.)

quote(arg):  same $'...\ooo...' logic as ZshDriver (shared helper).

supports_stop_resume = True

parse_precmd_payload: same as ZshDriver.
```

#### `FallbackDriver`

For generic POSIX sh (and SSH where no separate pipe is available).
No stop/resume.
The cd command appears on screen (accepted degraded behaviour).

```
init_code(fd):
    If fd is not None:
        " MC_PRECMD() { pwd >&{fd}; }; PS1='$(MC_PRECMD)'"'"$PS1\n"
    Else:
        ""  (no hook — cwd sync not available for SSH in this iteration)

quote(arg):
    Returns the full cd command as a self-contained shell expression:
    "_mc_newdir_=`printf '%b_' '{octal-escaped}'`; cd \"${_mc_newdir_%_}\""
    The returned string is a complete statement; Terminal writes it directly as-is
    (does not prepend 'cd ').  Other drivers return just the quoted path and
    Terminal prepends 'cd '.

supports_stop_resume = False

parse_precmd_payload("/home/user\n") → (None, PurePath("/home/user"))
    (The shell expands $(pwd); the pipe receives the resolved path as plain text.)
```

#### `SshShellDriver`

Thin subclass of `FallbackDriver` for SSH connections.
`init_code()` always returns `""` (no pipe available).
`supports_stop_resume = False`.

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
    self._backend = backend or LocalPtyBackend()
    self._driver = driver or detect_driver(command)
    ...
```

### State removed

| Removed | Reason |
|---|---|
| `_nav_pending: int` | Replaced by SIGSTOP synchronisation |
| `nav_start` recv_queue barrier | No longer needed |
| `shell_init_code()` module fn | Moved to `ShellDriver.init_code()` |
| `shell_cmd_cd()` module fn | Replaced by `ShellDriver.quote()` |
| `_KILL_LINE`, `_YANK`, `_END_OF_LINE` constants | Kept — still used for input preservation |

### State retained

| Kept | Reason |
|---|---|
| `_draining: bool` | Still needed: suppress cd echo between write and SIGSTOP |
| `_pending_yank: bool` | Still needed: restore typed text after navigation |
| `_snapshot_prompt_cursor: bool` | Still needed: capture cursor after new prompt |
| `_prompt_cursor_x: int` | Still needed: `has_input()` baseline |

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

Note: when `supports_stop_resume` is True, the shell will stop after the first precmd.
`recv()` sends SIGCONT then and ends draining.
No `_nav_pending` needed — the stop is the barrier.

### `_run()` simplified

```python
async def _run(self) -> None:
    loop = asyncio.get_running_loop()
    self._loop = loop
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

All `fcntl`, `struct`, `termios` imports move to `LocalPtyBackend`.

### `recv()` pre_cmd handler simplified

```python
elif cmd == "pre_cmd":
    raw = str(message[1])
    pid, cwd = self._driver.parse_precmd_payload(raw)
    if self._draining:
        # Navigation cd has completed.  Write yank bytes before resuming
        # so they arrive at the shell before it prints the new prompt.
        if self._pending_yank:
            self._pending_yank = False
            self._backend.write((_YANK + _END_OF_LINE).encode())
        if pid is not None:
            self._backend.resume(pid)
        self._draining = False
    self._snapshot_prompt_cursor = True
    self.post_message(Terminal.PreCmd(self, cwd))
```

### `set_terminal_directory()` simplified

```python
async def set_terminal_directory(self, path: PurePath) -> None:
    if not self._started:
        return
    self._pending_yank = self.has_input()
    if self._pending_yank:
        self._backend.write((_KILL_LINE).encode())
    cmd = f"cd {self._driver.quote(str(path))}\n"
    if self._driver.supports_stop_resume:
        self._draining = True
    self._backend.write(cmd.encode())
```

No `send()` mode needed. `_draining` is set synchronously before the write.

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
  backend.resume(pid)              →   shell resumes, prints new prompt
_draining = False
_snapshot_prompt_cursor = True
  next rebuild snapshots cursor
```

---

## `set_terminal_directory` timing diagram (FallbackDriver / SSH case)

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

---

## Error handling

- If the shell process exits before sending SIGSTOP, `resume()` is called on a dead pid.
  `os.kill(pid, SIGCONT)` raises `ProcessLookupError`; `LocalPtyBackend.resume()` suppresses it.
- If `parse_precmd_payload` receives malformed data (e.g. no `:` separator), it logs a warning and returns `(None, PurePath("/"))` so the app does not crash.
- If `_backend.open()` raises, `Terminal.start()` catches it, logs the error, and leaves `_started = False`.

---

## Testing

### `ShellDriver` (pure unit tests — no PTY)

- `ZshDriver.init_code(7)` embeds `>&7` and `kill -STOP $$`.
- `ZshDriver.quote("/home/user/O'Brien")` produces a correctly escaped `$'...'` string with no unmatched quotes.
- `ZshDriver.parse_precmd_payload("12345:/home/user\n")` returns `(12345, PurePath("/home/user"))`.
- `BashDriver.init_code(5)` uses `PROMPT_COMMAND`.
- `FallbackDriver.supports_stop_resume` is `False`.
- `detect_driver("/usr/bin/zsh")` returns `ZshDriver`.
- `detect_driver("/bin/bash")` returns `BashDriver`.
- `detect_driver("/bin/sh")` returns `FallbackDriver`.

### `LocalPtyBackend` (existing PTY lifecycle tests)

Existing tests that call `terminal.start()` / `terminal.stop()` continue to pass unchanged.
Tests that stub `send_queue`/`recv_queue` continue to work; they inject `recv_queue` messages directly.

### `Terminal` integration (mock backend)

A `FakePtyBackend` injectable in tests:
- Records all `write()` calls.
- Allows tests to post `["pre_cmd", payload]` into `recv_queue` directly.
- `resume()` records the call without sending a signal.

Tests using `FakePtyBackend` verify:
- `set_terminal_directory` sets `_draining = True` and calls `backend.write(cd_cmd)`.
- On `pre_cmd`, `_draining` is cleared, SIGCONT is requested, cursor snapshot is armed.
- Double rapid navigation: second `pre_cmd` correctly ends draining (no counter needed).
- `FallbackDriver`: `_draining` is never set; cd is written normally; `pre_cmd` arms snapshot directly.

### Race condition regression tests

`test_race_a_stale_pre_cmd_resets_draining_for_current_navigation` and
`test_race_c_snapshot_taken_only_after_last_navigation_pre_cmd` become
obsolete.
They are **deleted** as part of this implementation — the SIGSTOP design makes the races structurally impossible.

---

## Migration notes

### Public API

`Terminal.__init__` gains two optional keyword arguments (`backend`, `driver`).
Existing call sites (`MainScreen`) pass only `command` and are unaffected.

### Module-level functions removed from `terminal.py`

`shell_init_code`, `shell_cmd_cd`, `shell_clear_prompt` are removed from `__all__` and from the module.
Any call sites outside `terminal.py` must be updated (currently none found).

### Imports removed from `terminal.py`

`fcntl`, `struct`, `termios`, `signal` move to `pty_backend.py`.
`shlex` moves to `shell_driver.py`.
`terminal.py` retains only `asyncio`, `contextlib`, `logging`, `os`, `re`, `pty`, and the Textual/pyte/Rich imports.
