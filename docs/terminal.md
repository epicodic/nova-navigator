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

## Terminal Widget Layer

The `Terminal` class is a focusable Textual `Widget` that owns rendering, draining, and event handling.
It delegates downward to `PtyBackend` (process I/O) and `ShellDriver` (shell syntax).

### Supporting Classes

**`TerminalPyteScreen`** — subclass of `pyte.Screen` that drops the unsupported `private` keyword from `set_margins`.
This is a compatibility shim for a pyte bug triggered by certain escape sequences.

**`TerminalDisplay`** — a `rich.ConsoleRenderable` holding one `rich.text.Text` per terminal row plus the cursor position.
Its `__rich_console__` method copies the cursor row and applies a `"reverse"` style span at the cursor character before yielding.
The stored lines in `_display` are never mutated.

### Key Attributes

| Attribute | Type | Purpose |
|-----------|------|---------|
| `command` | `str` | The shell command to run (e.g. `"/usr/bin/zsh"`) |
| `ncol`, `nrow` | `int` | Current terminal dimensions |
| `mouse_tracking` | `bool` | Whether the child app has enabled mouse reporting |
| `keep_alive` | `bool` | Whether to respawn the shell on disconnect |
| `_backend` | `PtyBackend` | The backend instance (default: `LocalPtyBackend`) |
| `_driver` | `ShellDriver` | The driver instance (auto-detected from `command`) |
| `_screen` | `TerminalPyteScreen` | pyte virtual screen (VT100 state machine) |
| `_stream` | `pyte.Stream` | pyte ANSI parser; feeds bytes into `_screen` |
| `_display` | `TerminalDisplay` | The last rendered frame, returned by `render()` |
| `send_queue` | `asyncio.Queue \| None` | Commands from the widget to the PTY writer task |
| `recv_queue` | `asyncio.Queue \| None` | Events from the backend readers, consumed by `recv()` |
| `_draining` | `bool` | When True, stdout is fed to pyte but display rebuilds are suppressed |
| `_nav_pending` | `int` | Number of in-flight navigations awaiting pre_cmd acknowledgement |
| `_nav_future` | `Future[PurePath] \| None` | Resolved when `_nav_pending` reaches zero |
| `_prompt_cursor_x` | `int` | Cursor X position captured after the most recent prompt |
| `_pending_yank` | `bool` | Whether to restore killed text after draining ends |
| `_snapshot_prompt_cursor` | `bool` | Set True to capture `_prompt_cursor_x` on the next rebuild |
| `_rebuild_handle` | `TimerHandle \| None` | Pending `call_later` for the next deferred display rebuild |

### Module-Level Constants

| Constant | Value | Purpose |
|----------|-------|---------|
| `_CTRL_KEYS` | `dict[str, str]` | Maps Textual key names (arrows, F-keys, etc.) to VT escape sequences |
| `_TERMINAL_COLORS` | `dict[str, str]` | Maps 10 named pyte colors plus `"default"` to hex values |
| `_MOUSE_TRACKING_MODES` | `frozenset({"1000", "1002", "1003", "1006"})` | DECSET mode numbers that toggle mouse tracking |
| `_RECV_DRAIN_LIMIT` | `100` | Maximum messages drained per `recv()` iteration |
| `_DISPLAY_FPS` | `60.0` | Maximum display rebuild rate in frames per second |

### Lifecycle

```
Terminal.__init__()   → allocates pyte screen/stream; queues are None
Terminal.start()      → opens backend, creates async tasks
Terminal.stop()       → cancels tasks, tears down backend, resets display
Terminal.respawn()    → tears down and restarts backend (keeps recv loop alive)
```

`start()` and `stop()` are called explicitly by the host (`MainScreen`).
The widget renders an empty line until `start()` is called.

---

## Screen Rendering Pipeline

Rendering is split into two phases with different frequencies.

### Phase 1 — per stdout chunk: `_feed_stdout(chars)`

Called once per `stdout` message received from the PTY, at full read rate:

1. **ANSI scan:** The raw text is searched with `_re_ansi_sequence` for DECSET sequences.
Any sequence whose mode numbers intersect `_MOUSE_TRACKING_MODES` updates `mouse_tracking` — `h` enables, `l` disables.

2. **pyte feed:** `self._stream.feed(chars)` parses the ANSI escape sequences and updates the pyte `TerminalPyteScreen` buffer.

After `_feed_stdout` returns, `recv()` calls `_schedule_rebuild()`, which posts a `call_later(1 / _DISPLAY_FPS)` timer if one is not already pending.
Many consecutive reads cause only one rebuild per frame.

### Phase 2 — rate-limited: `_rebuild_display()`

Called by the event loop timer at most `_DISPLAY_FPS` (60) times per second:

3. **Rich Text conversion:** For each row in the pyte buffer, a `rich.text.Text` object is built.
Characters are appended one by one.
A run-length encoding approach tracks where the current style run started.
When the style of character `x` differs from character `x-1`, `Text.stylize` is called on the completed run.

4. **`TerminalDisplay` creation:** The list of `Text` lines and cursor position are wrapped in a new `TerminalDisplay` and stored as `self._display`.

5. **Prompt cursor snapshot:** If `_snapshot_prompt_cursor` is True, `_prompt_cursor_x` is captured from the current cursor X position and the flag is cleared.

6. **`refresh()`:** The widget is marked dirty.
Textual calls `render()` which returns `self._display`.
`TerminalDisplay.__rich_console__` yields the lines with cursor highlighting applied.

---

## Silent Send and Draining

Some data must be sent to the shell without its echo appearing in the terminal display.
The primary use cases are shell init code at startup and programmatic directory navigation.

### Why termios ECHO Suppression Does Not Work

Clearing the PTY `ECHO` flag via `termios.tcsetattr` is ineffective for interactive shells.
When zsh or bash runs in interactive mode, they use their own line editor in raw mode, managing echo internally and ignoring the PTY line discipline's `ECHO` flag.

### Inspiration: Midnight Commander

MC's `feed_subshell(QUIETLY, ...)` reads and discards all PTY output until the shell prompt reappears.
The same approach is applied here at the application layer.

### The Draining State

`Terminal._draining: bool` controls whether PTY output is forwarded to the display.

```
Normal mode (_draining=False)
  stdout → pyte feed → _schedule_rebuild → display updated

Draining mode (_draining=True)
  stdout → discarded (pyte not fed, no rebuild)
  ...more stdout...
  pre_cmd fires → _draining=False → display rebuilt from current screen state
```

The `pre_cmd` pipe is the reliable "shell is back at prompt" signal — exactly as MC uses its `subshell_pipe`.

### `send(data, mode="silent")`

Public method on `Terminal`.
When `mode` is `"silent"` and the driver supports stop/resume, sets `_draining = True` before writing `data` to the backend.
The data reaches the shell as regular input; the echo is swallowed by the draining logic.
Once the shell's precmd hook fires, draining ends and the display is live again.

### Startup Bootstrap

`_start_backend()` sets `_draining = True` before writing the init code when the driver supports stop/resume.
The shell will STOP after its first precmd (startup).
`recv()` will send SIGCONT to resume it and end the startup drain.

---

## Key and Mouse Event Flow

### Keyboard

1. Textual delivers a `Key` event to `on_key`.
2. Special keys (arrows, F-keys, etc.) are translated via `_CTRL_KEYS` to their VT escape sequences.
3. Printable characters use `event.character` directly.
4. `ctrl+f1` releases focus back to the application without sending to the shell.
5. The result is placed on `send_queue` as `["stdin", text]`.
6. `_run()` writes the encoded bytes to the PTY via `backend.write()`.

### Mouse (when `mouse_tracking` is enabled)

1. `on_click` / `on_mouse_scroll_up` / `on_mouse_scroll_down` place `["click", ...]` or `["scroll", ...]` on `send_queue`.
2. `_run()` encodes these as SGR mouse escape sequences via `_encode_mouse()` and writes them to the PTY.

Mouse tracking is active when the running application sends any of the DECSET enable sequences `?1000h`, `?1002h`, `?1003h`, or `?1006h`.
It is disabled by the corresponding `l` variants.

---

## Color Mapping

pyte represents character colors as either:
- A named color: `"black"`, `"red"`, `"green"`, `"yellow"`, `"blue"`, `"magenta"`, `"cyan"`, `"brown"`, `"white"`, `"brightblack"`, `"default"`.
- A 6-digit hex string, uppercase or lowercase (e.g. `"ff6600"` for 256-color/truecolor).

`_translate_terminal_color` maps these to Rich-compatible color strings:
- Hex strings → `"#rrggbb"`.
- Named colors → the hex values in `_TERMINAL_COLORS`.
- Unknown strings → passed through as-is (may raise `ColorParseError` in `char_rich_style`, which logs a warning and falls back to `Style()`).

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

## Message Protocol (Internal Queues)

Both queues carry `list[object]` messages with a string command as the first element.

### send_queue (widget → PTY writer)

| Message | Format | Purpose |
|---------|--------|---------|
| `stdin` | `["stdin", str]` | Text to write to the PTY |
| `set_size` | `["set_size", rows, cols]` | Resize the PTY window |
| `click` | `["click", x, y, button]` | Mouse click event |
| `scroll` | `["scroll", "up"/"down", x, y]` | Mouse scroll event |

### recv_queue (PTY readers → display updater)

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
