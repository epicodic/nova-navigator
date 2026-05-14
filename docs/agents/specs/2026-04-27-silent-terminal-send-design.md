# Design: Silent Terminal Send

## Problem

When `shell_init_code` is sent to the PTY at startup and after `respawn`, the shell echoes it back as stdout.
This causes the init code to be visible on screen and produces flickering.

## Why termios ECHO suppression does not work

Clearing the PTY's `ECHO` flag via `termios.tcsetattr` before writing does not help for interactive zsh.
When zsh runs in interactive mode it uses its own line editor (`zle`) in raw mode.
`zle` manages echo itself, reading the `ECHO` flag internally and displaying input through its own code path.
The PTY line discipline `ECHO` flag is therefore irrelevant once `zle` is active.

## How Midnight Commander solves this

MC does not try to suppress echo at the PTY level.
Instead, it reads and **discards** all PTY output (echo + command output) until the shell's prompt reappears, using its `feed_subshell(QUIETLY, ...)` function.
MC can do this because it owns the whole screen (ncurses) and has full control over what gets rendered.

## Design: draining state

Apply the same approach inside `Terminal`.

### `_draining: bool` flag

Add a boolean `_draining` to `Terminal`, initially `False`.

### Effect of `_draining` on rendering

In the `recv` loop, the existing `stdout_fed` flag controls whether `_schedule_rebuild()` is called after processing a batch.
While `_draining` is `True`, skip the `_schedule_rebuild()` call — pyte still processes output (keeping screen state current) but the display is not refreshed for the user.

### `send_silent(data: str) -> None`

Public method on `Terminal`, parallel to `send()`.
Sets `_draining = True`, then enqueues the data as `["stdin", data]`.

When the shell finishes processing the silent command, it fires the `precmd` hook, which writes the cwd to the pre-cmd pipe.
The `recv` loop receives `pre_cmd`, clears the screen (`_screen.reset()`), and clears `_draining`, then posts `Terminal.PreCmd` as normal.
This causes the display to be rebuilt from the blank screen — the echo output is gone.

### `_spawn_pty` update

Call `send_silent(shell_init_code(self.fd_pre_cmd_child))` instead of enqueueing `["stdin", ...]` directly.

### `recv` loop update

In the `pre_cmd` branch:
1. If `_draining` is `True`: reset the pyte screen and clear `_draining`.
2. Post `Terminal.PreCmd` as before.
3. Call `_schedule_rebuild()` unconditionally after the batch.

### Leading space

The init code already starts with a space (` _nn_precmd() ...`), which keeps it out of zsh history via `HISTCONTROL=ignorespace`.
This is unchanged.

---

## What does not change

- `send()` and `on_key` are unchanged.
- No callers in `main.py` need updating.
- `shell_init_code` content is unchanged.
- `_dispatch_send_message` is unchanged — `silent_stdin` is no longer a separate message type.

---

## Future Windows port

On Windows with `ConPTY`, the same draining approach applies — read and discard PTY output until the prompt reappears.
No platform-specific code is required; the design is purely at the application layer.

---

## Files affected

| File | Change |
|------|--------|
| `src/nova_navigator/widgets/terminal.py` | Add `_draining` flag, `send_silent()`, update `recv` loop `pre_cmd` branch, update `_spawn_pty` |
