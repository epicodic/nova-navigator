# Code Review: `widgets/terminal.py`

This review covers correctness, safety, maintainability, and simplification opportunities for the 503-line terminal emulator widget.

---

## Summary

The file implements a PTY-backed terminal emulator widget for Textual using `pyte`.
The core design is sound, but there is a mix of acknowledged (`# FIXME`/`# TODO`) and unacknowledged technical debt.
Several issues affect correctness and resource safety.
The internal messaging protocol is untyped and fragile.
There are no tests for this widget.

---

## Critical Issues

### 1. File descriptors are never closed (`stop()` leaks resources)

`stop()` cancels the asyncio tasks and kills the child process, but never:
- calls `p_out.close()` or `p_out_pre_cmd.close()`
- removes the event loop readers added by `loop.add_reader()`

This causes `ResourceWarning: unclosed file` (acknowledged in the `FIXME` comment).
The `on_output` exception handler calls `loop.remove_reader(self.p_out)` for the main fd, and `on_pre_cmd` calls `loop.remove_reader(self.p_out_pre_cmd)` on error, but neither is called from `stop()`.

**Fix:** Close both file objects and remove both readers in `stop()`, wrapped in try/except.

---

### 2. `os.waitpid` blocks the event loop

`stop()` calls `os.waitpid(self.pid, 0)` synchronously.
This blocks the Textual event loop until the child exits.
If the child process is stuck or writing to a full pipe buffer, this can deadlock.

**Fix:** Use `asyncio.get_event_loop().add_child_watcher()` or the non-blocking `os.waitpid(pid, WNOHANG)` pattern.
At minimum, send `SIGKILL` after a timeout if `SIGTERM` is not enough.

---

### 3. Type annotations declare non-nullable types initialized to `None`

```python
self.send_queue: asyncio.Queue[list[object]] = None
self.recv_queue: asyncio.Queue[list[object]] = None
self.recv_task_t: Task[None] = None
```

These are typed as non-optional but assigned `None`, causing type errors.
The `# mypy: ignore-errors` at the top of the file suppresses all type-checking, hiding this and any other type errors.

**Fix:** Use `asyncio.Queue[list[object]] | None = None` and `Task[None] | None = None`, then assert or guard before use.
Remove `# mypy: ignore-errors` and fix remaining type errors so `ty check` covers the file.

---

### 4. `open_terminal()` returns a closed file descriptor

```python
os.close(fd_pre_cmd_child)   # parent closes the write end
return fd, fd_pre_cmd_parent, fd_pre_cmd_child   # fd_pre_cmd_child is now closed!
```

The method closes `fd_pre_cmd_child` and then returns it.
The caller stores it as `self.fd_pre_cmd_child` and passes it to `shell_init_code()` to embed the fd number into the shell init script.
This works by coincidence: the numeric fd value is the same in parent and child (before the parent closes it), and the shell script runs in the child where the fd is still open.
The intent is opaque and the returned value is misleading (it looks like a valid, open fd).

**Fix:** Return only `(fd, fd_pre_cmd_parent)` and pass `fd_pre_cmd_child` separately before closing it, or add a clear comment documenting the fd-number-sharing trick.

---

### 5. Off-by-one in style span when a style run changes

In `recv()`, when a character's style differs from the previous character:

```python
if not self.char_style_cmp(char, last_char) or x == self._screen.columns - 1:
    last_style = self.char_rich_style(last_char)
    line_text.stylize(last_style, style_change_pos, x + 1)   # spans up to and including x
    style_change_pos = x
```

When `not char_style_cmp(char, last_char)` is true (style changed at position `x`), the style for the previous run should cover `[style_change_pos, x)`.
But `x + 1` as the end means it also applies the OLD style to position `x`, which has a DIFFERENT style.
Rich's `stylize(style, start, end)` uses exclusive `end`, so `stylize(s, a, x + 1)` covers positions `a` through `x`.

This only produces visible errors when the cursor reverse-style override is not applied at position `x`.

**Fix:** Use `x` (not `x + 1`) as the end when the style changed:
```python
end = x + 1 if x == self._screen.columns - 1 else x
line_text.stylize(last_style, style_change_pos, end)
```

---

### 6. `shell_cmd_cd` is vulnerable to path injection via single quotes

```python
def shell_cmd_cd(path: PurePath) -> str:
    return f"cd '{path}' >& /dev/null && printf '\\e[A'"
```

If a directory name contains a single quote (e.g., `O'Brien`), the generated shell command will be syntactically broken or exploitable.

**Fix:** Use `shlex.quote`:
```python
import shlex
def shell_cmd_cd(path: PurePath) -> str:
    return f"cd {shlex.quote(str(path))} >& /dev/null && printf '\\e[A'"
```

---

## Medium Issues

### 7. Cursor stylized twice in every render

In `recv()`, the cursor position is stylized while building `line_text`:
```python
if self._screen.cursor.x == x and self._screen.cursor.y == y:
    line_text.stylize("reverse", x, x + 1)
```

Then in `TerminalDisplay.__rich_console__`, it is stylized again:
```python
if y == self.cursor_y:
    line.stylize("reverse", self.cursor_x, self.cursor_x + 1)
```

The cursor's reverse style is applied twice on every render.
It happens to be idempotent for "reverse" but the duplication is confusing and the `TerminalDisplay` cursor fields (`cursor_x`, `cursor_y`) exist specifically for this purpose, making the inline stylization in `recv()` redundant.

**Fix:** Remove the cursor stylization from `recv()` and keep only the one in `TerminalDisplay.__rich_console__`.

---

### 8. `recv_queue_precmd` is created but never used

In `start()`:
```python
self.recv_queue_precmd = asyncio.Queue()
```

Pre-cmd messages are routed through `recv_queue`, not `recv_queue_precmd`.
This queue is allocated, stored, and never read from or written to.

**Fix:** Remove `self.recv_queue_precmd`.

---

### 9. `quiet` variable in `_run()` is dead code

```python
quiet = False
```

This variable is assigned but never read.

**Fix:** Remove it.

---

### 10. Mouse tracking only handles mode 1000, not 1002/1003/1006

The DECSET scanning detects `1000h`/`1000l` (normal mouse tracking) but ignores extended modes:
- `1002` — button-event tracking
- `1003` — any-event tracking
- `1006` — SGR extended mode (required for terminals wider than 223 columns)

Applications that use SGR mouse mode (e.g., `vim`, `htop`) will not have their mouse tracking state correctly tracked.

**Fix:** Extend the `if "1000h" in parameters` checks to cover `1002h`, `1003h`, and `1006h`.

---

### 11. Child process environment is stripped of `PATH`

In `open_terminal()`:
```python
env = {
    "TERM": "xterm",
    "LC_ALL": "en_US.UTF-8",
    "HOME": str(Path.home()),
}
os.execvpe(argv[0], argv, env)
```

`os.execvpe` completely replaces the environment.
The child shell (`zsh`) starts with no `PATH`, `USER`, `LOGNAME`, `XDG_*`, or any other variable.
ZSH will source `/etc/zsh/zshenv` which may restore `PATH`, but this is distro-dependent and fragile.

**Fix:** Start from `os.environ.copy()` and override only the keys that need to change.

---

### 12. `_translate_terminal_color` uses `re.match` instead of `re.fullmatch`

```python
if re.match("[0-9a-f]{6}", color, re.IGNORECASE):
    return f"#{color}"
```

`re.match` anchors at the start but not the end, so `"0000001234"` (8 hex digits) would match and return `"#0000001234"`, which is an invalid color.
pyte color names longer than 6 hex digits should not occur in practice, but this is still incorrect.

**Fix:** Use `re.fullmatch`.

---

### 13. Inconsistency: `TerminalPyteScreen` docstring mentions `TERM=linux`, code uses `TERM=xterm`

The class docstring says "to be used with TERM=linux", but `open_terminal()` sets `TERM=xterm`.
The `set_margins` override is a workaround for a pyte bug triggered by the TERM value, but the actual TERM sent to the child is `xterm`.

**Fix:** Update the docstring, and verify whether `set_margins` override is still needed with `TERM=xterm`.

---

### 14. Internal queue protocol uses positional `list[object]`

All messages in `send_queue` and `recv_queue` are bare lists with positional elements:
```python
["stdin", char]
["set_size", nrow, ncol]
["click", event.x, event.y, event.button]
["pre_cmd", cwd_string]
["disconnect", 1]
```

A wrong index or missing element raises `IndexError` with no indication of message type.
There is no static check that senders and receivers agree on the format.

**Fix:** Use `@dataclass` or `NamedTuple` for each message type, or at minimum a `TypedDict`.

---

### 15. `on_click` declares `events.MouseEvent` instead of `events.Click`

```python
async def on_click(self, event: events.MouseEvent) -> None:
```

The Textual handler `on_click` receives `events.Click`, not `events.MouseEvent`.
`events.MouseEvent` does not have a `button` attribute; `events.Click` does.

**Fix:** Change the type annotation to `events.Click`.

---

### 16. `send_queue.put` awaited unnecessarily in hot paths

In `on_key()`, `on_resize()`, `on_click()`, and the scroll handlers, `await self.send_queue.put(...)` is used.
Since `send_queue` is created with no `maxsize`, `put()` never actually blocks and the await is a no-op extra coroutine step.

**Fix:** Use `self.send_queue.put_nowait(...)` for all sends from event handlers.

---

### 17. No tests

There are no tests for `Terminal`, `TerminalDisplay`, `TerminalPyteScreen`, or the helper functions.
The CWD-tracking mechanism, screen rendering, ANSI escape handling, and mouse tracking are all untested.

---

## Low Issues and Simplification Opportunities

### 18. `char_style_cmp` can be simplified to a tuple comparison

Current implementation compares 8 fields individually with `and`.
A tuple comparison is equivalent and shorter:

```python
def char_style_cmp(self, given: Char, other: Char) -> bool:
    return (
        given.fg, given.bg, given.bold, given.italics,
        given.underscore, given.strikethrough, given.reverse, given.blink
    ) == (
        other.fg, other.bg, other.bold, other.italics,
        other.underscore, other.strikethrough, other.reverse, other.blink
    )
```

---

### 19. `shell_init_code` uses `%` formatting suppressed by `noqa: UP031`

```python
NN_PRECMD = "_nn_precmd() { pwd>&%d }" % fd  # noqa: UP031
```

The `noqa` suppresses the ruff warning to use an f-string.
The reason given is that `{` and `}` in the shell snippet make f-strings awkward, but it is straightforward with doubled braces:

```python
NN_PRECMD = f"_nn_precmd() {{ pwd>&{fd} }}"
```

---

### 20. Mouse event handlers share duplicated guard logic

`on_click`, `on_mouse_scroll_down`, and `on_mouse_scroll_up` all repeat:

```python
if not self._started:
    return
if self.mouse_tracking is False:
    return
```

This can be extracted into a small helper or property.

---

### 21. `shell_clear_prompt()` uses 200 backspaces

```python
def shell_clear_prompt() -> str:
    return "\b" * 200
```

If the prompt is longer than 200 characters, this silently fails to clear it.
A more reliable approach is to send `\r\x1b[K` (carriage return + erase to end of line) or to use readline's `accept-line` to submit a blank line.

---

### 22. Commented-out code and leftover class scaffold comments

The file retains a number of commented-out lines and internal structural comments from a refactoring:

- `# TerminalEmulator.__init__`, `# TerminalEmulator.start`, `# TerminalEmulator.stop`, `# class TerminalEmulator:` — leftover class boundary markers
- `# log("recv stdout:", chars)`, `# log.warning("Terminal.recv cancelled")`, `# log.warning("TerminalEmulator._run cancelled")`
- `# _nn_precmd() { pwd>&%d; kill -STOP $$; }` — old shell hook variant in a comment
- `## Terminal.start()` — internal section marker

These should be removed to reduce noise.

---

### 23. `fd_pre_cmd_child` is a public attribute exposing implementation detail

`fd_pre_cmd_child` is accessed directly from `main.py` to construct the shell init code.
This tightly couples `MainScreen` to the internal fd plumbing of `Terminal`.

**Improvement:** Expose a method like `shell_init_code(self) -> str` on `Terminal` itself so callers do not need to know about the fd.

---

### 24. `TERM=xterm` is hardcoded

The terminal type could reasonably be `xterm-256color` (better color support) or made configurable.
`xterm` limits the color palette to the standard 16 colors.

---

### 25. Missing `__all__` export declaration

The module exports `Terminal`, `shell_clear_prompt`, `shell_cmd_cd`, `shell_init_code` (used in `main.py`), but there is no `__all__`.
Declaring `__all__` makes the public API explicit and prevents accidental re-exports.
