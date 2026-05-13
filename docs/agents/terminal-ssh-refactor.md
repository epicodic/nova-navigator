# Refactoring Plan: SSH Terminal Support

This document proposes how to add SSH terminal support by extracting a `TerminalBackend` abstraction from the existing `terminal.py`.

---

## Motivation

`Terminal` is currently a monolithic widget that mixes transport (PTY fork, file descriptors, ioctls) with rendering (pyte, Rich text, draining).
Roughly 450 of its 637 lines are transport-agnostic and would have to be duplicated in any from-scratch SSH implementation.
Extracting a backend protocol avoids duplication and keeps all rendering logic in one place.

---

## Current Structure

```
Terminal (Widget)
├── Rendering     _feed_stdout, _rebuild_display, _schedule_rebuild
│                 TerminalDisplay, TerminalPyteScreen, color mapping
│                 draining / silent-send state machine
│                 recv() queue loop
├── Widget glue   on_key, on_click, on_mouse_scroll_*, send(), render()
└── Transport     open_terminal, _spawn_pty, _teardown_pty, _run, respawn
                  pty.fork, os.fdopen, fcntl.ioctl(TIOCSWINSZ), os.kill
```

Only the Transport layer is local-PTY-specific.
Everything else is reusable.

---

## Proposed Structure

```
terminal_backend.py     TerminalBackend protocol
local_backend.py        LocalPTYBackend  (extracts current PTY code)
ssh_backend.py          SSHChannelBackend (new)
terminal.py             Terminal widget  (accepts any TerminalBackend)
```

### `TerminalBackend` protocol

```python
class TerminalBackend(Protocol):
    def start(
        self,
        send_queue: asyncio.Queue[list[object]],
        recv_queue: asyncio.Queue[list[object]],
        nrow: int,
        ncol: int,
    ) -> None: ...

    def stop(self) -> None: ...
    def resize(self, nrow: int, ncol: int) -> None: ...
    def send_stdin(self, data: str) -> None: ...
    def send_click(self, x: int, y: int, button: int) -> None: ...
    def send_scroll(self, direction: str, x: int, y: int) -> None: ...
```

`start()` receives both queues so the backend can register `loop.add_reader` callbacks that push into `recv_queue` and can read from `send_queue` in its `_run` task.
`stop()` cancels tasks, removes readers, and closes the transport.

### `LocalPTYBackend`

This is a direct extraction of the existing code with no behaviour changes.

| Moves from `Terminal` | Notes |
|-----------------------|-------|
| `open_terminal()` | unchanged |
| `_spawn_pty()` | unchanged |
| `_teardown_pty()` | unchanged |
| `_run()` coroutine | unchanged |
| `respawn()` | unchanged |
| Resize via `fcntl.ioctl(TIOCSWINSZ)` | inside `resize()` |
| CWD tracking via `os.pipe()` | unchanged; see below |

### `SSHChannelBackend`

`SSHChannelBackend` accepts a connected `paramiko.SSHClient` and implements the same protocol.

```python
class SSHChannelBackend:
    def __init__(self, ssh_client: paramiko.SSHClient) -> None:
        self._ssh_client = ssh_client
        self._channel: paramiko.Channel | None = None
        self._run_task: asyncio.Task[None] | None = None

    def start(self, send_queue, recv_queue, nrow, ncol) -> None:
        transport = self._ssh_client.get_transport()
        assert transport is not None
        self._channel = transport.open_session()
        self._channel.get_pty(term="xterm-256color", width=ncol, height=nrow)
        self._channel.invoke_shell()
        loop = asyncio.get_running_loop()
        loop.add_reader(self._channel.fileno(), lambda: self._on_output(recv_queue))
        self._run_task = asyncio.create_task(self._run(send_queue))

    def resize(self, nrow: int, ncol: int) -> None:
        if self._channel is not None:
            self._channel.resize_pty(width=ncol, height=nrow)

    def stop(self) -> None:
        if self._run_task is not None:
            self._run_task.cancel()
        if self._channel is not None:
            self._channel.close()
```

`_on_output` reads from the channel and puts `["stdout", text]` into `recv_queue`, matching the same message format `Terminal.recv()` already expects.
On channel EOF it puts `["disconnect", 1]`.

### `Terminal` widget changes

`Terminal.__init__` gains a `backend: TerminalBackend` parameter and drops `command: str`.
`start()` delegates to `self._backend.start(...)`.
`stop()` delegates to `self._backend.stop()`.
`_dispatch_send_message` is removed; the backend handles all writes internally via its `_run` task.
All rendering code (`_feed_stdout`, `_rebuild_display`, `recv()`, draining, etc.) stays in `Terminal` untouched.

For convenience, a factory function can hide the backend:

```python
def local_terminal(**kwargs) -> Terminal:
    return Terminal(backend=LocalPTYBackend(command="/usr/bin/zsh"), **kwargs)

def ssh_terminal(ssh_client: paramiko.SSHClient, **kwargs) -> Terminal:
    return Terminal(backend=SSHChannelBackend(ssh_client), **kwargs)
```

---

## CWD Tracking

The two backends use different mechanisms to signal `["pre_cmd", cwd]` to `recv_queue`.

### Local: pipe fd (unchanged)

`LocalPTYBackend` keeps the existing `os.pipe()` approach.
The write-end fd number is embedded in the zsh init script; the read end is monitored with `loop.add_reader`.
No changes required.

### SSH: OSC 7 escape sequence

Over SSH there is no shared file descriptor, so the local pipe approach cannot work.
The standard alternative is the OSC 7 sequence, used by Kitty, WezTerm, and iTerm2 for the same purpose:

```
\e]7;file://hostname/absolute/path\a
```

`SSHChannelBackend.start()` injects a zsh init hook that emits this sequence after each command:

```python
SSH_INIT_CODE = (
    " _nn_precmd() { printf '\\e]7;file://%s%s\\a' \"$(hostname)\" \"$(pwd)\" } ;"
    " precmd_functions+=(_nn_precmd)\n"
)
```

`Terminal._feed_stdout` is extended to scan for `\e]7;...` sequences using a small regex.
When found, the sequence is stripped from the text before it reaches pyte, and `["pre_cmd", cwd]` is put directly into `recv_queue`.

```python
_re_osc7 = re.compile(r"\x1b\]7;file://[^/]*(/[^\a]*)\a")

def _feed_stdout(self, chars: str) -> None:
    for m in _re_osc7.finditer(chars):
        self.recv_queue.put_nowait(["pre_cmd", m.group(1)])
    chars = _re_osc7.sub("", chars)
    # ... existing DECSET scan and pyte feed ...
```

This keeps CWD tracking entirely within `Terminal` for both backends.
`LocalPTYBackend` still delivers `pre_cmd` via the pipe; `SSHChannelBackend` delivers it via OSC 7 embedded in stdout.

---

## Migration Steps

1. Create `terminal_backend.py` with the `TerminalBackend` protocol.
2. Extract PTY transport into `local_backend.py` as `LocalPTYBackend`.
3. Add OSC 7 scanning to `Terminal._feed_stdout`.
4. Modify `Terminal.__init__` to accept `backend: TerminalBackend`; remove `command`.
5. Update `MainScreen` (and any other callers) to pass `LocalPTYBackend`.
6. Implement `SSHChannelBackend` in `ssh_backend.py`.
7. Run `uv run qa` to confirm zero regressions.

Steps 1–5 are a pure refactor with no behaviour change and can be done independently of step 6.

---

## What Is Not Changing

- `TerminalDisplay`, `TerminalPyteScreen` — unchanged.
- `_rebuild_display`, `_schedule_rebuild`, `_on_rebuild_timer` — unchanged.
- `recv()` loop and all queue message formats — unchanged.
- Draining / silent-send state machine — unchanged.
- All keyboard and mouse event handlers — unchanged.
- `shell_cmd_cd`, `shell_clear_prompt`, `set_terminal_directory` — unchanged.
