# Architecture: `widgets/terminal.py`

This document describes the design, components, data flow, and internal mechanisms of the `Terminal` widget — a PTY-backed terminal emulator embedded in the Nova Navigator TUI.

---

## Purpose

`Terminal` integrates a fully functional shell (zsh) into a Textual `Widget`.
It runs the shell in a pseudo-terminal, feeds its output through `pyte` (a VT100/VT220 emulator), renders each frame as Rich `Text`, and forwards keyboard and mouse events back to the shell.
It also tracks the shell's current working directory by hooking zsh's `precmd` mechanism.

---

## Components

### `TerminalPyteScreen`

Subclass of `pyte.Screen` that overrides `set_margins` to drop the `private` keyword argument.
This is a compatibility shim for a pyte bug that surfaces when specific applications are run with certain `TERM` values.

### `TerminalDisplay`

A `rich.ConsoleRenderable` that holds one `rich.text.Text` per terminal row plus the cursor position.
Its `__rich_console__` method yields the lines, applying a `"reverse"` style span at the cursor position before yielding that row.
This object is the result of `render()` and is consumed by Textual's rendering pipeline on every refresh.

### `Terminal` (widget)

The main class.
It is a focusable `textual.Widget` and owns:

| Attribute | Type | Purpose |
|-----------|------|---------|
| `command` | `str` | The shell command to run (e.g., `"/usr/bin/zsh"`) |
| `ncol`, `nrow` | `int` | Current terminal dimensions |
| `mouse_tracking` | `bool` | Whether the child app has enabled mouse reporting |
| `_screen` | `TerminalPyteScreen` | pyte virtual screen (the VT100 state machine) |
| `_stream` | `pyte.Stream` | pyte ANSI parser, feeds bytes into `_screen` |
| `_display` | `TerminalDisplay` | The last rendered frame, returned by `render()` |
| `send_queue` | `asyncio.Queue[list[object]] \| None` | Commands from the widget to the PTY writer task |
| `recv_queue` | `asyncio.Queue[list[object]] \| None` | Raw events from the PTY reader, consumed by `recv()` |
| `_run_task` | `Task[None] \| None` | PTY I/O pump — writes stdin, processes size changes |
| `recv_task_t` | `Task[None] \| None` | Processes `recv_queue` entries and updates the display |
| `_loop` | `asyncio.AbstractEventLoop \| None` | Event loop reference stored by `_run()` so `stop()` can call `remove_reader` synchronously |

### Helper functions (module level)

| Function | Purpose |
|----------|---------|
| `shell_init_code(fd)` | Generates zsh code that hooks `precmd` to pipe `pwd` to fd |
| `shell_clear_prompt()` | Returns 200 backspace characters to erase the current prompt |
| `shell_cmd_cd(path)` | Returns a shell command to `cd` to `path` silently (uses `shlex.quote`) |
| `_translate_terminal_color(color)` | Maps pyte color names and 6-digit hex strings to Rich color strings |

### Module-level constants

| Constant | Purpose |
|----------|---------|
| `_CTRL_KEYS` | Maps Textual key names to their VT escape sequences |
| `_TERMINAL_COLORS` | Maps 9 named pyte colors plus `"default"` to hex values |
| `_MOUSE_TRACKING_MODES` | The four DECSET mode numbers that toggle mouse tracking: `{"1000", "1002", "1003", "1006"}` |

---

## Lifecycle

```
Terminal.__init__()   → allocates pyte screen/stream; queues are None
Terminal.start()      → forks PTY, opens file objects, creates async tasks
Terminal.stop()       → cancels tasks, kills child, resets display
```

`start()` and `stop()` are called explicitly by the host (`MainScreen`).
The widget does nothing (renders an empty line) until `start()` is called.

---

## PTY Setup (`open_terminal`)

```
Parent process                     Child process (after pty.fork)
──────────────────────────────     ──────────────────────────────
fd_pre_cmd_parent ◄─── pipe ───   fd_pre_cmd_child  (write end)
                                   os.execvpe(zsh)
                                   ↓
                                   zsh starts with fd_pre_cmd_child
                                   open and inheritable
```

1. `os.pipe()` creates `(fd_pre_cmd_parent, fd_pre_cmd_child)`.
2. `pty.fork()` creates a child and a PTY master fd (`fd`).
3. In the **child**: `fd_pre_cmd_parent` is closed; `fd_pre_cmd_child` is made inheritable; `execvpe` replaces the child with zsh.
4. In the **parent**: `fd_pre_cmd_child` is closed (the numeric fd value is saved as `self.fd_pre_cmd_child` to be embedded in the shell init script that runs inside the child).
5. The PTY master fd is wrapped with `os.fdopen` as `self._p_out`.
6. The pre-cmd pipe read end is wrapped as `self._p_out_pre_cmd`.

---

## Async I/O Model

The widget uses two asyncio tasks and the event loop's `add_reader` facility.

```
asyncio event loop
│
├── loop.add_reader(_p_out,         on_output)   ← fires when PTY master has data
├── loop.add_reader(_p_out_pre_cmd, on_pre_cmd)  ← fires when shell writes pwd
│
├── Task: _run()      ← registers readers, reads send_queue, writes stdin / resize to PTY
└── Task: recv()      ← reads recv_queue, updates pyte screen, calls refresh()
```

### `on_output` (event loop callback)

Runs on the event loop thread when the PTY master fd is readable.
Reads up to 65 536 bytes, decodes UTF-8, and puts `["stdout", text]` into `recv_queue`.
On any non-decode exception (typically an `OSError` meaning the child exited), removes the reader and puts `["disconnect", 1]`.

### `on_pre_cmd` (event loop callback)

Runs when the pre-cmd pipe has data (i.e., after each zsh command completes).
Reads the written `pwd` output and puts `["pre_cmd", cwd_string]` into `recv_queue`.

### `_run()` coroutine (task)

On startup it calls `loop.add_reader` for both `_p_out` and `_p_out_pre_cmd`, then enqueues `["setup", {}]` to trigger the initial resize.
Afterwards it awaits messages from `send_queue` and dispatches:

| Message | Action |
|---------|--------|
| `["stdin", text]` | Writes encoded bytes to `_p_out` (the PTY master) |
| `["set_size", rows, cols]` | Sends `TIOCSWINSZ` ioctl to resize the PTY |
| `["click", x, y, btn]` | Writes an SGR mouse press/release escape sequence |
| `["scroll", dir, x, y]` | Writes an SGR mouse scroll escape sequence |

### `recv()` coroutine (task)

Awaits messages from `recv_queue` and dispatches:

| Message | Action |
|---------|--------|
| `["setup", {}]` | Sends initial `set_size` to PTY |
| `["pre_cmd", cwd]` | Posts `Terminal.PreCmd` Textual message |
| `["stdout", text]` | Scans for DECSET mouse sequences; feeds text to pyte; re-renders display |
| `["disconnect", _]` | Calls `stop()` |

---

## Screen Rendering Pipeline

Every time `recv()` processes a `stdout` message:

1. **ANSI scan:** The raw text is searched with `_re_ansi_sequence` for DECSET sequences.
Any sequence whose mode numbers intersect `_MOUSE_TRACKING_MODES` (`{"1000", "1002", "1003", "1006"}`) updates `mouse_tracking` — `h` enables it, `l` disables it.

2. **pyte feed:** `self._stream.feed(chars)` parses the ANSI escape sequences and updates the pyte `TerminalPyteScreen` buffer (cursor position, character cells, colors, attributes).

3. **Rich Text conversion:** For each row in the pyte buffer, a `rich.text.Text` object is built.
   Characters are appended one by one.
   A run-length encoding approach is used: a `style_change_pos` pointer tracks where the current style run started.
   When the style of character `x` differs from character `x-1`, `Text.stylize` is called to apply the previous style to the completed run `[style_change_pos, x)`.
   The last run is always closed at `x == columns - 1`.
   No cursor highlighting is applied here — the stored lines are plain character data only.

4. **`TerminalDisplay` creation:** The list of `Text` lines and cursor position are wrapped in a new `TerminalDisplay` and stored as `self._display`.

5. **`refresh()`:** The widget is marked dirty; Textual calls `render()` which returns `self._display`; the Textual rendering pipeline calls `TerminalDisplay.__rich_console__`, which copies the cursor row, applies `"reverse"` to the cursor character, and returns a `list[Text]`.
The stored lines in `_display` are never mutated.

---

## CWD Tracking Mechanism

```
zsh shell (child)                         Nova Navigator (parent)
─────────────────                         ───────────────────────
precmd hook fires after each command
  │
  ▼
_nn_precmd() { pwd>&N }                  on_pre_cmd() fires (add_reader)
  │                                           │
  ▼                                           ▼
writes cwd to fd N (pre-cmd pipe)       recv_queue ← ["pre_cmd", cwd_string]
                                               │
                                               ▼
                                         recv() → Terminal.PreCmd message
                                               │
                                               ▼
                                         MainScreen._on_terminal_pre_cmd()
                                         updates active panel path
```

1. On `on_mount`, `MainScreen` calls `terminal.get_shell_init_code()`, which delegates to `shell_init_code(self.fd_pre_cmd_child)` to generate:
   ```zsh
   _nn_precmd() { pwd>&N }; precmd_functions+=(_nn_precmd)
   ```
   where `N` is the numeric file descriptor number of the write end of the pre-cmd pipe (open in the child process).
2. This code is sent to the shell via `Terminal.send()`.
3. After each zsh command, the hook fires, writes the current directory to fd `N`.
4. The event loop reader `on_pre_cmd` fires and routes the cwd through `recv_queue` to `recv()`.
5. `recv()` posts `Terminal.PreCmd(terminal_widget, cwd)`.
6. `MainScreen._on_terminal_pre_cmd` calls `active_panel().set_path(VPath(event.cwd, LocalFilesystem))` to synchronize the file browser panel.

---

## Directory Change (Panel → Terminal)

When the user navigates in a panel and the terminal should follow:

```python
await self._terminal.send(shell_clear_prompt() + " " + shell_cmd_cd(path.path) + "\n")
```

- `shell_clear_prompt()` sends 200 backspace characters to erase any partial command already typed.
- `shell_cmd_cd(path)` produces `cd '/some/path' >& /dev/null && printf '\e[A'`.
- `printf '\e[A'` moves the cursor up one line in the terminal output so the `cd` command is not visually present in the terminal's scroll history.

---

## Key and Mouse Event Flow

### Keyboard

1. Textual delivers a `Key` event to `on_key`.
2. Special keys (arrows, F-keys, etc.) are translated via `_CTRL_KEYS` to their VT escape sequences.
3. Printable characters use `event.character` directly.
4. The result is put on `send_queue` as `["stdin", text]`.
5. `_run()` writes the encoded bytes to the PTY master.

`MainScreen._handle_key` also forwards some keys from panel focus to the terminal (e.g., printable characters, backspace, arrow keys) without the terminal having focus.

### Mouse (when `mouse_tracking` is enabled)

1. `on_click` / `on_mouse_scroll_*` place `["click", ...]` or `["scroll", ...]` on `send_queue`.
2. `_run()` translates these to X10/SGR mouse escape sequences and writes them to the PTY.

Mouse tracking is active when the running application sends any of the DECSET enable sequences `?1000h`, `?1002h`, `?1003h`, or `?1006h`.
It is disabled by the corresponding `l` variants.
The `_process_stdout` method scans all stdout for these sequences and updates `self.mouse_tracking`.

---

## Message Protocol (Internal Queues)

Both queues carry `list[object]` messages with a string command as the first element.
This is an informal protocol with no static type checking.

**`send_queue` messages (widget → PTY writer):**

| `msg[0]` | Remaining elements | Meaning |
|----------|--------------------|---------|
| `"stdin"` | `[str]` | Text to write to the PTY |
| `"set_size"` | `[rows, cols]` | Resize the PTY window |
| `"click"` | `[x, y, button]` | Mouse click event |
| `"scroll"` | `["up"/"down", x, y]` | Mouse scroll event |

**`recv_queue` messages (PTY readers → display updater):**

| `msg[0]` | Remaining elements | Meaning |
|----------|--------------------|---------|
| `"setup"` | `[{}]` | PTY is ready; send initial size |
| `"stdout"` | `[str]` | Output from the terminal process |
| `"pre_cmd"` | `[str]` | Shell's current working directory |
| `"disconnect"` | `[int]` | Child process has exited |

---

## Color Mapping

pyte represents character colors as either:
- A named color: `"black"`, `"red"`, `"green"`, `"yellow"`, `"blue"`, `"magenta"`, `"cyan"`, `"brown"`, `"white"`, `"default"`
- A 6-digit hex string, uppercase or lowercase (e.g., `"ff6600"` for 256-color/truecolor)

`_translate_terminal_color` maps these to Rich-compatible color strings:
- Hex strings (matched case-insensitively with `re.fullmatch`) → `"#rrggbb"`
- Named colors → the hex values in `_TERMINAL_COLORS` (9 named colors plus `"default"`)
- Unknown strings → passed through as-is (may raise `ColorParseError` in `char_rich_style`, which logs a warning and falls back to `Style()`)

Note: bright color variants (`"bright_red"`, etc.) are not present in `_TERMINAL_COLORS` and will fall through to the pass-through path.
