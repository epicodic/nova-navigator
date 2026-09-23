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
| `supports_line_editing` | Property — True if the shell has an emacs-style line editor (kill and yank) used for hidden navigation |

### Concrete Drivers

**ZshDriver** — installs a precmd hook via `precmd_functions+=(_nn_precmd)`.
The hook emits an OSC 7 CWD sequence.

**BashDriver** — installs a precmd hook via `PROMPT_COMMAND`.

**FallbackDriver** — generic POSIX sh driver.
Installs a `PS1`-based hook that emits OSC 7.
Has no line editor, so directory navigation writes a visible `cd` without input preservation.
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
| `_input_since_precmd` | `bool` | True once any user-originated byte was sent since the last precmd |
| `_prompt_snapshot` | `tuple[int, int, str] \| None` | Cursor row, column, and row text captured after every stdout chunk until the first user byte |
| `_at_prompt` | `bool` | True while the shell waits for a command line |
| `owner` | `object \| None` | Token set by the host (the active pane); recorded on command submission |
| `_command_owner` | `object \| None` | Copy of `owner` taken when the user submits a command; attached to the next user-initiated `PathChanged` |
| `_nav_target` | `PurePath \| None` | Newest requested directory not yet confirmed by the shell |
| `_nav_busy` | `bool` | A `cd` was written and its precmd is awaited |
| `_nav_last_sent_target` | `PurePath \| None` | The target most recently written as a `cd`; used to tell a newer target from a repeated one |
| `_nav_same_target_attempts` | `int` | Consecutive `cd` writes for `_nav_last_sent_target` without the shell reporting it as cwd |
| `_nav_stashed` | `bool` | Typed text was killed and is yanked back on completion |
| `_nav_future` | `Future[PurePath] \| None` | Resolved when the transaction chain completes |
| `_nav_watchdog` | `TimerHandle \| None` | Fires when no precmd follows a `cd` |
| `_hook_repaired` | `bool` | True after the precmd hook has been re-installed once this shell session |
| `_cwd` | `PurePath \| None` | Last known working directory reported by the shell via precmd |
| `_rebuild_handle` | `TimerHandle \| None` | Pending `call_later` for the next deferred display rebuild |

### Module-Level Constants

| Constant | Value | Purpose |
|----------|-------|---------|
| `_CTRL_KEYS` | `dict[str, str]` | Maps Textual key names (arrows, F-keys, etc.) to VT escape sequences |
| `_TERMINAL_COLORS` | `dict[str, str]` | Maps 10 named pyte colors plus `"default"` to hex values |
| `_MOUSE_TRACKING_MODES` | `frozenset({"1000", "1002", "1003", "1006"})` | DECSET mode numbers that toggle mouse tracking |
| `_RECV_DRAIN_LIMIT` | `100` | Maximum messages drained per `recv()` iteration |
| `_DISPLAY_FPS` | `60.0` | Maximum display rebuild rate in frames per second |
| `_NAV_WATCHDOG_TIMEOUT` | `2.0` | Seconds to wait for a precmd after a `cd` before repairing the hook |
| `_NAV_MAX_SAME_TARGET_ATTEMPTS` | `3` | Consecutive `cd` attempts for one target before giving up on a mismatched cwd |

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

`has_input()` tells the host whether the shell line holds user input, so Enter from a pane can execute a typed command or, when the line is empty, act on the pane.

It uses two facts that Nova Navigator owns and needs nothing from the shell.

### Fact A: user bytes since precmd

Every path into the shell goes through the widget: `on_key`, `_paste_text`, and `send()`.
Each sets `_input_since_precmd`; every precmd clears it.
Internal writes (kill, `cd`, yank, init code) never set it.
If no user byte was sent, the line is empty, and this answer is certain.

### Fact B: the screen before the first user byte

After precmd, `_prompt_snapshot` is refreshed after every stdout chunk while `_input_since_precmd` is false.
It is therefore frozen at the moment the user starts typing, when the prompt is fully drawn on screen.
No prompt-end marker or timing guess is needed, and multi-chunk or asynchronous prompts are handled by construction.

### Decision

| Bytes sent since precmd | Screen vs snapshot | Result |
|---|---|---|
| no | — | `False` |
| yes | cursor and row text identical | `False` (typed, then deleted everything) |
| yes | anything differs | `True` |

Comparing the row text as well as the cursor handles Home, wide characters, and wrapped input.
After a navigation that restored typed text, the flag is set and the snapshot is left empty, so the answer is `True` for that prompt line.
The only misjudgement is "typed, deleted everything, and the prompt repainted asynchronously", which yields `True`; Enter then executes an empty line, which is harmless.

The mechanism is identical for zsh, bash, local, and SSH terminals.

---

## Directory Navigation Flow

A pane-driven directory change is one serialised transaction per terminal.

`request_cd(path)` stores *path* as `_nav_target` and, when the shell is at a prompt and no transaction is busy, calls `_start_nav()`.
If the shell is running a command, the target waits and is applied on the next precmd.
If the shell already reports *path* and nothing is pending, the request is a no-op.

`_start_nav()` enables draining, kills typed input with the driver's `kill_line_sequence()` if `has_input()` is true (once per chain), writes ` cd <quoted>\n`, and arms the watchdog.

The precmd hook's OSC 7 completes the step.
If a newer target arrived meanwhile, `_handle_pre_cmd` chains directly into another `_start_nav()` while draining stays on, so intermediate prompts are never shown.
Otherwise `_finish_nav()` yanks stashed text back with the driver's `yank_sequence()`, ends draining with the `\r\x1b[K` in-place redraw, and resolves `_nav_future`.
See [Giving up on an unreachable target](#giving-up-on-an-unreachable-target) for what happens when the reported cwd keeps mismatching the *same* target instead.

### Kill/yank sequences are driver-specific

`ShellDriver.kill_line_sequence()` and `ShellDriver.yank_sequence()` return the exact bytes each shell needs, so `Terminal` never hardcodes control characters itself.

Both directions follow the same pattern: move to the true end of the line, then act.
`kill_line_sequence()` moves to the end, types a literal marker space, then kills the whole buffer (marker included).
`yank_sequence()` yanks the killed text back, then backspaces once to remove the marker.

The marker space exists so the kill is never empty.
An empty kill leaves the shell's kill ring/cutbuffer untouched, so a spurious kill on an already-empty line (`has_input()` can return a conservative `True` after a restore, since the freshly restored prompt cannot be snapshotted safely — see below) would otherwise yank back stale, unrelated text from an earlier, unrelated kill.
Moving to the true end first, both before the kill and before the backspace, guarantees the marker is always the *last* character killed and therefore always the *last* character yanked back, so the backspace removes exactly it and nothing else — the restore is indistinguishable from what was there before the navigation.

`BashDriver` uses the base implementation, moving to the end with real Ctrl+E: bash's Ctrl+U (`unix-line-discard`) only kills text before the cursor, so positioning matters, and bash has no plugin ecosystem that intercepts `end-of-line`.

`ZshDriver` cannot use real Ctrl+E for this.
Ctrl+E (the `end-of-line` widget) is one of zsh-autosuggestions' default accept-widgets: sending it while a suggestion is showing silently accepts that suggestion into the real buffer, and the plugin recomputes its suggestion after every keystroke, so this risk exists at every point in the sequence, not just the first.
Instead, `ZshDriver.init_code()` binds a private, unassigned CSI sequence (`_ZSH_RAW_END_OF_LINE_SEQUENCE`) to a small zle widget that calls `zle .end-of-line` — the dot-prefixed form always refers to zsh's original, unwrapped builtin, regardless of what any plugin has layered onto the public `end-of-line` name.
`ZshDriver.kill_line_sequence()` and `yank_sequence()` send that private sequence (and a trailing backspace instead of a trailing Ctrl+E) wherever the base implementation would use real Ctrl+E, reaching the true end without ever risking suggestion acceptance.

`has_input()`'s snapshot cannot be safely recaptured immediately after a restore: the shell's echo of the restored text can arrive fused with the freshly drawn prompt in the very same stdout chunk, so there is no reliable way to isolate "prompt only" from "prompt plus restored text" by observing chunk boundaries.
This is why `_finish_nav()` forces `_input_since_precmd = True` rather than trying to snapshot, and why the never-empty-kill guarantee above is the actual defense against stale-text resurrection, not snapshot accuracy.

### Precmd classification

A precmd that arrives while `_nav_busy` is true belongs to the transaction.
Any other precmd is a user command cycle: a changed cwd posts `PathChanged` with the recorded `owner`, and a stored target is then applied.

The shell's very first precmd (before `_cwd` is ever known) is a bootstrap exception: it reports
wherever the shell happened to launch, not a real navigation, so it does not post `PathChanged`
unless it is itself the result of a user-submitted command (`owner` is not `None`).
Otherwise a slow-starting shell could report its launch directory after a navigation to a
different directory is already in flight, clobbering it.

### Watchdog and hook repair

If no precmd follows a `cd` within `_NAV_WATCHDOG_TIMEOUT`, the precmd hook was most likely removed by an rc file or plugin.
On the first timeout in a session the driver's `init_code()` is re-sent followed by the `cd`; the init line's own precmd reports the old directory, so the normal chaining rule re-sends the `cd` and the transaction completes.
On a second timeout the transaction gives up, yanks stashed text, ends draining, and resolves the future with the last known cwd.

### Giving up on an unreachable target

A precmd can also arrive every time yet never report `_nav_target` as the cwd, because the `cd` itself keeps failing (the directory was deleted, is not accessible, and so on).
This does not trip the watchdog: a precmd *does* arrive, so `_cancel_watchdog()` disarms it before the mismatch is even checked.

`_handle_pre_cmd` tells the two cases apart by comparing `_nav_target` against `_nav_last_sent_target`, the target the most recent `_start_nav()` actually wrote:

- **Target changed** (a newer `request_cd()` replaced it while busy) — `_nav_same_target_attempts` resets to 1 and the chain proceeds as normal.
- **Same target repeated** — the counter increments; once it reaches `_NAV_MAX_SAME_TARGET_ATTEMPTS`, the transaction gives up exactly like a second watchdog timeout: `_finish_nav()` runs with the actually-reported cwd instead of resending the same `cd` again.

Without this bound, a target the shell can never reach would make `_handle_pre_cmd` resend the identical `cd` forever, one resend per precmd.

### Pane ownership

The host sets `Terminal.owner` to the active pane whenever it syncs the terminal.
When user input containing a newline is forwarded, the widget copies `owner` into `_command_owner`.
The next user-initiated `PathChanged` carries that owner, so the pane that submitted the command follows the shell even if the user switched panes before the command finished.

### Derived target on the host side

`MainScreen._sync_terminal_to_active_panel()` reads the active panel's path at call time and calls `request_cd` with it.
It is invoked on Tab, on panel focus, and on every `DirectoryBrowser.PathChanged`.
Because the target is derived rather than passed, a delayed handler can never move the terminal to a panel that is no longer active, and a path change in the inactive panel resolves to a no-op.

### Startup

`_start_backend()` enables draining and writes the init code.
The first precmd ends draining and marks the shell as at a prompt; a target stored before that is applied then.
That first precmd does not post `PathChanged` (see [Precmd classification](#precmd-classification)).

### Awaitable Return Value

`set_terminal_directory` awaits the transaction future and returns the CWD reported by the shell.
If no transaction is required, it returns the last known CWD, or the requested path when none is known yet.

### History exclusion

Navigation `cd` commands are prefixed with a space.
The init code enables `HIST_IGNORE_SPACE` (zsh) or appends `ignorespace` to `HISTCONTROL` (bash).
Both settings are idempotent and have no effect on user-typed commands that do not start with a space.

---

## Key and Mouse Event Flow

### Keyboard

1. Textual delivers a `Key` event to `on_key`.
2. Special keys (arrows, F-keys, etc.) are translated via `_CTRL_KEYS` to their VT escape sequences.
3. Printable characters use `event.character` directly.
4. `ctrl+f1` releases focus back to the application without sending to the shell.
5. `ctrl+shift+c` copies the current text selection to the clipboard instead of being sent to the shell.
6. The widget records user input via `_note_user_input`, which sets `_input_since_precmd` and, for Enter, clears `_at_prompt` and records the command owner.
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
4. Pass `line_editing=True` to the base constructor if the shell has an emacs-style line editor.
5. Update `detect_driver()` in `shell_driver.py` to recognise the shell name.

All drivers share the same draining mechanism.
No special synchronisation is needed.
