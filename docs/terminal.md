# Terminal Sub-Package

Architecture documentation for `nova_navigator/terminal/`.

---

## Overview

The terminal sub-package embeds an interactive shell inside the Textual application.
It is split into three layers, each with a single responsibility:

- **PtyBackend** — OS-level process and PTY management (start, stop, read, write, resize).
- **ShellDriver** — shell-language knowledge (init hooks, argument quoting).
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
├── ssh_pty_backend.py   # SshPtyBackend (paramiko-based)
├── shell_driver.py      # ShellDriver ABC + ZshDriver, BashDriver, FallbackDriver, detect_driver()
├── terminal.py          # Terminal widget, TerminalDisplay, TerminalPyteScreen
└── terminal_pool.py     # TerminalPool — manages one terminal per filesystem
```

---

## PtyBackend Layer

`PtyBackend` is an abstract base class defining the contract for terminal process backends.
It manages the lifecycle of a shell process and provides byte-level I/O.

### ABC Contract

| Method | Purpose |
|--------|---------|
| `open(command, rows, cols)` | Start the shell process |
| `write(data)` | Write raw bytes to the shell's stdin |
| `resize(rows, cols)` | Resize the terminal via `TIOCSWINSZ` |
| `resume()` | Send `SIGCONT` to the shell process (no-op for SSH) |
| `attach_readers(loop, recv_queue)` | Register callbacks that push messages into recv_queue |
| `detach_readers()` | Remove reader callbacks |
| `teardown()` | Terminate the process and close all file descriptors |
| `supports_precmd` | Property — True if this backend delivers precmd CWD notifications (always True) |

### In-Band CWD Tracking via OSC 7

CWD tracking uses in-band OSC 7 escape sequences rather than an out-of-band pipe.
The shell's precmd hook (installed by `ShellDriver.init_code()`) emits:

```
\033]7;panel=;file:///current/path\007
```

The `panel=` prefix distinguishes Nova Navigator's own hook from third-party hooks (e.g. oh-my-zsh) that emit plain `file://` OSC 7 sequences.
The inherited `PtyBackend._process_chunk()` method scans stdout for OSC sequences, strips them, and posts `["pre_cmd", path, from_nn]` messages.

### LocalPtyBackend

`LocalPtyBackend` is the concrete implementation for local shell processes.
It uses `pty.fork()` to create a pseudo-terminal.

In `open()`, the child process prepares a clean environment (sets `TERM`, removes inherited `VIRTUAL_ENV` variables) and calls `os.execvpe()`.
The parent process stores the master fd and applies the initial terminal size.

A reader callback registered via `attach_readers()` uses `loop.add_reader()` on the master fd.
It pushes `["stdout", ...]` messages (with OSC sequences extracted) into `recv_queue`.
On read errors, a `["disconnect", 1]` message is pushed.

### SshPtyBackend

`SshPtyBackend` runs a shell over a paramiko SSH channel.
A daemon thread reads from `channel.recv()` and forwards data through `_process_chunk()`.
The `resume()` method is a no-op since SIGSTOP is not used over SSH.

### Lifecycle

The full lifecycle is: `open()` → `attach_readers()` → normal operation → `detach_readers()` → `teardown()`.
`teardown()` sends `SIGTERM` (local) or closes the channel (SSH), then closes file objects.

---

## ShellDriver Layer

`ShellDriver` is an abstract base class that isolates all shell-language knowledge from the Terminal widget.

### ABC Contract

| Method | Purpose |
|--------|---------|
| `init_code()` | Return shell code to inject at startup (installs precmd hook) |
| `quote(arg)` | Return a shell-safe quoted form of an argument |
| `cd_command(path)` | Return a complete `cd` command string |
| `supports_prompt_ready` | Property — True if init code installs an OSC 133;B prompt-end hook |

### Concrete Drivers

**ZshDriver** — installs a precmd hook via `precmd_functions+=(_nn_precmd)`.
The hook emits an OSC 7 CWD sequence.
Also installs a `zle-line-init` widget that emits OSC 133;B for prompt detection.

**BashDriver** — installs a precmd hook via `PROMPT_COMMAND`.
Also embeds OSC 133;B in PS1 for prompt detection.

**FallbackDriver** — generic POSIX sh driver.
Installs a `PS1`-based hook that emits OSC 7.
Does not support prompt detection (no OSC 133;B).
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
| `_draining` | `bool` | When True, stdout is discarded (not fed to pyte) |
| `_nav_pending` | `int` | Number of in-flight navigations awaiting pre_cmd acknowledgement |
| `_nav_future` | `Future[PurePath] \| None` | Resolved when `_nav_pending` reaches zero |
| `_prompt_cursor_x` | `int` | Cursor X position captured after the most recent prompt |
| `_prompt_cursor_y` | `int` | Cursor Y position captured after the most recent prompt |
| `_prompt_ready_received` | `bool` | True after the prompt position has been snapshotted |
| `_keys_forwarded_since_precmd` | `bool` | True if any key was sent to the shell since the last precmd |
| `_snapshot_prompt_after_precmd` | `bool` | Set True to capture prompt position on the next stdout chunk |
| `_pending_yank` | `bool` | Whether to restore killed text after draining ends |
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

5. **`refresh()`:** The widget is marked dirty.
Textual calls `render()` which returns `self._display`.
`TerminalDisplay.__rich_console__` yields the lines with cursor highlighting applied.

---

## Precmd-Gated Draining

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
  pre_cmd fires → _draining=False → next stdout feeds pyte and triggers rebuild
```

The precmd hook's OSC 7 sequence is the reliable "shell is back at prompt" signal — exactly as MC uses its `subshell_pipe`.
No SIGSTOP/SIGCONT synchronisation is required.
The shell runs continuously; draining simply discards output until precmd fires.

### How It Works Without SIGSTOP

The timing is inherently correct because precmd fires *before* the shell prints its prompt:

1. `cd` command sent → shell echoes the command (suppressed by draining)
2. Shell executes the `cd`
3. Precmd hook runs → emits OSC 7 → `_handle_pre_cmd` clears draining
4. Shell prints PS1 prompt → first stdout after precmd → displayed normally

The OSC 7 sequence is parsed from the stdout byte stream by `PtyBackend._process_chunk()`.
It is posted as a `["pre_cmd", ...]` message *before* any remaining text in the same chunk.
This ensures draining ends at exactly the right moment.

### `send(data, mode="silent")`

Public method on `Terminal`.
When `mode` is `"silent"` and the backend supports precmd, sets `_draining = True` before writing `data` to the backend.
The data reaches the shell as regular input; the echo is swallowed by the draining logic.
Once the shell's precmd hook fires, draining ends and the display is live again.

### Startup Bootstrap

`_start_backend()` sets `_draining = True` before writing the init code.
The shell processes the init code (echo suppressed) and fires its first precmd hook.
When the first OSC 7 arrives, draining ends and the prompt is displayed.

---

## Input Detection (`has_input()`)

The `has_input()` method determines whether the user has typed something on the current prompt line.
It is used by the host application to decide whether pressing Enter should execute a command in the terminal or trigger a panel action (e.g. open a file).

### Two-Tier Strategy

1. **Primary — cursor comparison** (when prompt position is known):
   The cursor position at the end of the prompt is snapshotted.
   If the current cursor is past that position, the user has typed something.
   This correctly handles "typed then deleted" (cursor returns to prompt position → `False`).

2. **Fallback — keystroke tracking** (when prompt position is unknown):
   A flag (`_keys_forwarded_since_precmd`) is set whenever a key event is forwarded to the shell and cleared on each precmd.
   This works for any shell but cannot detect the "typed then deleted everything" case (conservatively returns `True`).

### Prompt Position Snapshotting

The prompt position is captured from two sources, whichever fires first:

- **OSC 133;B** — emitted by `zle-line-init` (zsh) or embedded in PS1 (bash).
  This is the most precise signal: it fires at the exact moment the shell enters line-editing mode, with the cursor at the end of the prompt.

- **First stdout after precmd** — when OSC 133;B is not available (e.g. overridden by oh-my-zsh plugins), `_snapshot_prompt_after_precmd` is set True by `_handle_pre_cmd`.
  The next stdout chunk (which is the prompt text) triggers a cursor snapshot.
  This is slightly less precise (may capture mid-prompt if the prompt arrives in multiple chunks) but handles the "typed then deleted" case.

### Shell Compatibility

| Shell | Primary detection | Fallback |
|-------|-------------------|----------|
| zsh (with NN hooks intact) | Cursor comparison (OSC 133;B) | Keystroke flag |
| zsh (hooks overridden by plugins) | Cursor comparison (post-precmd snapshot) | Keystroke flag |
| bash | Cursor comparison (PS1 OSC 133;B) | Keystroke flag |
| POSIX sh (FallbackDriver) | Not available | Keystroke flag only |
| SSH (any shell) | Same as corresponding shell above | Keystroke flag |

---

## Directory Navigation Flow

A walkthrough of `request_cd(path)`:

1. **Short-circuit.** If `_nav_pending == 0` and `_cwd == path`, return immediately.
2. **Check for typed input.** `has_input()` determines whether the user has typed something.
3. **Increment counter.** `_nav_pending += 1`.
4. **Enable draining.** Set `_draining = True` so `recv()` suppresses subsequent stdout.
5. **Save typed text.** If the user has typed something, send `Ctrl+U` (kill line) to save it to the shell's kill ring.
   Draining is already active so the kill echo is suppressed.
6. **Send cd command.** Write ` cd <quoted_path>\n` to the backend (leading space for history exclusion).
7. **Shell executes cd.** The shell changes directory and runs the precmd hook.
8. **Precmd hook fires.** The hook emits OSC 7 with the new CWD.
9. **recv() processes pre_cmd.** Decrements `_nav_pending`.
   If it reaches zero: writes `Ctrl+Y` + `Ctrl+E` (yank + end-of-line) if text was killed, clears `_draining`, resolves the navigation future, and enables prompt snapshotting.
10. **Shell prints prompt.** First stdout after precmd is displayed normally.
    Prompt position is snapshotted.

### Rapid Panel Switching

When the user switches panels faster than the shell can process cd commands, multiple navigations may be in flight.
The `_nav_pending` counter tracks how many navigations have not yet been acknowledged by a `pre_cmd`.
Draining stays on until the counter reaches zero, preventing intermediate cd echoes from leaking.

Only the final `PathChanged` is posted (when `_nav_pending` reaches 0 and the cwd has changed).
Intermediate cds are silently consumed.

### Active-Panel Routing

User-initiated directory changes (the user types `cd /somewhere` in the terminal) post `PathChanged` with `user_initiated=True`.
The host application routes these to the **currently active panel** (Midnight Commander model).
No shell-side panel identification variables are used, eliminating race conditions between rapid panel switches.

### Awaitable Return Value

`set_terminal_directory` returns the actual CWD reported by the shell (a `PurePath`).
If no navigation is needed, it returns the cached `_cwd` immediately.

### History Exclusion

Navigation cd commands must not pollute the shell's command history.
The cd command is prefixed with a leading space.
The init code also configures the shell to honour this convention:

- **zsh:** `setopt HIST_IGNORE_SPACE` — commands starting with a space are excluded from history.
- **bash:** `HISTCONTROL="${HISTCONTROL:+${HISTCONTROL}:}ignorespace"` — appended without overwriting user settings.

Both settings are idempotent and have no effect on user-typed commands that do not start with a space.

---

## Key and Mouse Event Flow

### Keyboard

1. Textual delivers a `Key` event to `on_key`.
2. Special keys (arrows, F-keys, etc.) are translated via `_CTRL_KEYS` to their VT escape sequences.
3. Printable characters use `event.character` directly.
4. `ctrl+f1` releases focus back to the application without sending to the shell.
5. `ctrl+shift+c` copies the current text selection to the clipboard instead of being sent to the shell.
6. The `_keys_forwarded_since_precmd` flag is set.
7. The result is placed on `send_queue` as `["stdin", text]`.
8. `_run()` writes the encoded bytes to the PTY via `backend.write()`.

### Mouse (when `mouse_tracking` is enabled)

1. `on_click` / `on_mouse_scroll_up` / `on_mouse_scroll_down` place `["click", ...]` or `["scroll", ...]` on `send_queue`.
2. `_run()` encodes these as SGR mouse escape sequences via `_encode_mouse()` and writes them to the PTY.

Mouse tracking is active when the running application sends any of the DECSET enable sequences `?1000h`, `?1002h`, `?1003h`, or `?1006h`.
It is disabled by the corresponding `l` variants.

A middle-click always pastes the clipboard, regardless of `mouse_tracking`, the same as `ctrl+shift+v` in a regular terminal.
`on_click` checks for the middle button before the `mouse_tracking` gate and forwards `self.app.clipboard` through `_paste_text()`, the same helper `on_paste` uses.

---

## Text Selection and Copy

`Terminal` renders through a custom `TerminalDisplay` (a `ConsoleRenderable`), not `Text`/`Content`, so Textual's automatic selection support needed manual wiring at two separate levels:

1. **Starting/extending a selection on mouse drag.**
   Textual's `Screen._forward_event` decides whether a `MouseDown`/`MouseMove` extends a selection by calling `get_widget_and_offset_at()`, which requires the widget's rendered `Strip` segments to carry an `"offset"` style-meta entry (normally added automatically for `Content`/`Text`-based widgets by `rich_style_with_offset`).
   A widget whose `render()` returns a raw `ConsoleRenderable` never gets this metadata through the generic rendering path, so a click-drag would start `_selecting` but never actually populate `Screen.selections` — nothing appeared selected.
   `Terminal` fixes this by overriding `render_line(y)` directly (bypassing `render()`/the generic `Visual` pipeline for on-screen painting, mirroring Textual's own `Log` widget): it renders row `y` from `TerminalDisplay.render_row()`, converts it to a `Strip`, and calls `Strip.apply_offsets(0, y)` to tag every segment with the offset metadata the compositor needs.
2. **Extracting and highlighting the selected text.**
   `Terminal.get_selection()` builds the copyable text from the current screen buffer, right-stripping each row's trailing pyte padding spaces.
   `TerminalDisplay.render_row()` paints the `screen--selection` component style over the selected span of a row (alongside the existing cursor reverse-video highlight); `render_line()` reads the current selection from `self.text_selection` on every call, so highlighting stays in sync as the drag progresses.
   `Terminal.render()` (used only for direct calls, e.g. in tests) still returns the same `TerminalDisplay` but is no longer part of the on-screen paint path once `render_line` is overridden.

Selection is only available while `mouse_tracking` is off (see `allow_select`).
When a mouse-aware full-screen program is running in the shell (vim, htop, a nested `mc`, etc.), click-drag must reach that program instead of starting a text selection.

Double-clicking selects the word under the pointer instead of Textual's default double-click behaviour (select the whole widget).
`Terminal._on_click()` overrides the `event.chain == 2` case to call `_select_word_at()`, which finds the `\w+` match under the click's `x` position on the clicked row and sets that span as the selection.
Textual's message dispatch calls `_on_click` for every class in the MRO that defines it, not just the most-derived one, so without `event.prevent_default()` the base `Widget._on_click` would run right afterwards for the same event and overwrite the word selection with its own select-all behaviour.
`_on_click` calls `event.prevent_default()` whenever it handles a double- or triple-click itself, to suppress that base handler.

Copying happens two ways:

- **Automatically** — releasing the mouse button after a drag (`_on_mouse_up`) copies the selection to the clipboard via `_copy_selection()`.
- **Explicitly** — pressing `ctrl+shift+c` re-copies the current selection.
  `ctrl+c` is not used for this because it is always forwarded to the shell as SIGINT.

There is no scrollback buffer (pyte only tracks the visible grid), so selection and copy only ever cover what is currently on screen.

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
| `stdout` | `["stdout", str]` | `_process_chunk` | Shell output (OSC sequences already stripped) |
| `pre_cmd` | `["pre_cmd", path, from_nn]` | `_process_chunk` | CWD from OSC 7; `from_nn` is True for NN hooks |
| `prompt_ready` | `["prompt_ready"]` | `_process_chunk` | OSC 133;B detected (prompt end) |
| `disconnect` | `["disconnect", int]` | reader callback | Shell process exited or read error |

The `recv()` loop drains up to `_RECV_DRAIN_LIMIT` (100) messages per wakeup to batch processing.
When `_draining` is True, `stdout` messages are silently discarded.

---

## TerminalPool

`TerminalPool` manages one `Terminal` widget per filesystem connection.
All terminals are mounted in the Textual DOM simultaneously.
Only the active terminal is visible (`display=True`); others are hidden but continue running.

The pool supports:
- A local terminal (always present).
- Remote terminals created on demand via registered factories (e.g. SSH).
- `switch_to(fs)` — show the terminal for a given filesystem, hiding the current one.

---

## Extending for New Backends

To add a new backend (e.g. a container-based PTY):

1. Subclass `PtyBackend`.
2. Implement all abstract methods: `open()`, `write()`, `resize()`, `resume()`, `attach_readers()`, `detach_readers()`, `teardown()`.
3. Pass the backend instance to `Terminal(command, backend=my_backend)`.

CWD tracking works automatically via in-band OSC 7 sequences parsed by `_process_chunk()`.
The `resume()` method can be a no-op for backends that don't support SIGCONT.

---

## Extending for New Shells

To add a new shell driver:

1. Subclass `ShellDriver`.
2. Implement `init_code()` — install a precmd hook that emits OSC 7 with the `panel=;file:///path` format.
3. Implement `quote(arg)` — return a safely quoted string for that shell's syntax.
4. Set `prompt_ready=True` if the hook also installs an OSC 133;B prompt-end marker.
5. Update `detect_driver()` in `shell_driver.py` to recognise the shell name.

All drivers share the same draining mechanism.
No special synchronisation is needed.
