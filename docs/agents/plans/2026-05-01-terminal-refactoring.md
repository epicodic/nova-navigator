# Terminal Sub-Package Refactoring — Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use skills:subagent-driven-development (recommended) or skills:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Extract `widgets/terminal.py` into a `nova_navigator/terminal/` sub-package with `PtyBackend`, `ShellDriver`, and `Terminal` layers, replacing the `_nav_pending` race-condition workaround with MC-style SIGSTOP synchronisation.

**Architecture:** Three layers — `PtyBackend` (OS-level PTY plumbing), `ShellDriver` (shell-specific hooks and quoting), `Terminal` (Textual widget, rendering, draining state machine). The shell sends `kill -STOP $$` in its precmd hook; `Terminal` sends SIGCONT when ready. This eliminates the race condition structurally.

**Tech Stack:** Python 3.12, pytest

**Coding Conventions:** `docs/coding_conventions.md` — read before implementing

**Design spec:** `docs/agents/specs/2026-05-01-terminal-refactoring.md`

---

## File Structure

### New files

| File | Responsibility |
|---|---|
| `src/nova_navigator/terminal/__init__.py` | Package init — re-exports public API |
| `src/nova_navigator/terminal/pty_backend.py` | `PtyBackend` ABC + `LocalPtyBackend` |
| `src/nova_navigator/terminal/shell_driver.py` | `ShellDriver` ABC + `ZshDriver`, `BashDriver`, `FallbackDriver`, `detect_driver()`, `_ansi_c_quote()` |
| `src/nova_navigator/terminal/terminal.py` | `Terminal` widget (trimmed, uses backend + driver) |
| `tests/terminal/__init__.py` | Test package init |
| `tests/terminal/test_shell_driver.py` | Pure unit tests for shell drivers |
| `tests/terminal/test_pty_backend.py` | LocalPtyBackend lifecycle tests |
| `tests/terminal/test_terminal.py` | Terminal widget tests (moved + adapted + new) |
| `docs/terminal.md` | Architecture document |

### Modified files

| File | Change |
|---|---|
| `src/nova_navigator/nova_navigator.py` | Update Terminal import path |
| `src/nova_navigator/widgets/__init__.py` | No change needed (Terminal was never exported here) |

### Deleted files

| File | Reason |
|---|---|
| `src/nova_navigator/widgets/terminal.py` | Moved to `terminal/terminal.py` |
| `tests/widgets/test_terminal.py` | Moved to `tests/terminal/test_terminal.py` |

---

## Task 1: Create `shell_driver.py` with `_ansi_c_quote()` and `ShellDriver` ABC

**Files:**
- Create: `src/nova_navigator/terminal/__init__.py` (empty for now)
- Create: `src/nova_navigator/terminal/shell_driver.py`
- Create: `tests/terminal/__init__.py`
- Create: `tests/terminal/test_shell_driver.py`

This task creates the shell driver module with the ABC, all three concrete drivers, `detect_driver()`, and the shared `_ansi_c_quote()` helper. Tests are written first.

- [ ] **Step 1: Create package scaffolding**

Create the empty `__init__.py` files:

```python
# src/nova_navigator/terminal/__init__.py
# (empty for now — populated in Task 5)
```

```python
# tests/terminal/__init__.py
```

- [ ] **Step 2: Write failing tests for `_ansi_c_quote()`**

Create `tests/terminal/test_shell_driver.py`:

```python
"""Unit tests for shell driver classes and quoting utilities."""

from __future__ import annotations

from pathlib import PurePath

import pytest

from nova_navigator.terminal.shell_driver import (
    BashDriver,
    FallbackDriver,
    ZshDriver,
    _ansi_c_quote,
    detect_driver,
)


# ---------------------------------------------------------------------------
# _ansi_c_quote
# ---------------------------------------------------------------------------


def test_ansi_c_quote_simple_path_preserves_safe_chars() -> None:
    result = _ansi_c_quote("/home/user/projects")
    assert result == "$'/home/user/projects'"


def test_ansi_c_quote_escapes_single_quote() -> None:
    result = _ansi_c_quote("/home/user/O'Brien")
    # Single quote (0x27 = octal 047) must be escaped
    assert "\\047" in result
    assert result.startswith("$'")
    assert result.endswith("'")


def test_ansi_c_quote_escapes_space() -> None:
    result = _ansi_c_quote("/home/user/my dir")
    # Space (0x20 = octal 040) must be escaped
    assert "\\040" in result


def test_ansi_c_quote_escapes_backslash() -> None:
    result = _ansi_c_quote("/home/user/back\\slash")
    # Backslash (0x5C = octal 134) must be escaped
    assert "\\134" in result


def test_ansi_c_quote_empty_string() -> None:
    result = _ansi_c_quote("")
    assert result == "$''"


def test_ansi_c_quote_preserves_safe_characters() -> None:
    safe = "abcABC012/._-"
    result = _ansi_c_quote(safe)
    # All safe chars should appear literally
    assert result == f"$'{safe}'"


def test_ansi_c_quote_escapes_newline() -> None:
    result = _ansi_c_quote("/home/user/line\nbreak")
    # Newline (0x0A = octal 012) must be escaped
    assert "\\012" in result


def test_ansi_c_quote_long_path_gets_line_continuation() -> None:
    # 300 chars of safe content — should trigger at least one continuation
    long_path = "/home/" + "a" * 294
    result = _ansi_c_quote(long_path)
    assert "\\\n" in result
```

- [ ] **Step 3: Run tests to verify they fail**

```
uv run pytest tests/terminal/test_shell_driver.py -v
```

Expected: FAIL — `nova_navigator.terminal.shell_driver` does not exist yet.

- [ ] **Step 4: Implement `_ansi_c_quote()`**

Create `src/nova_navigator/terminal/shell_driver.py`:

```python
"""Shell driver abstraction for terminal hook installation and argument quoting.

This module isolates all shell-language knowledge from the Terminal widget.
Each concrete ShellDriver knows how to:
- Install a precmd hook that writes CWD to a pipe and optionally stops the shell.
- Quote arbitrary strings for safe shell interpolation.
- Parse the precmd pipe output back into a pid and path.

The Terminal widget delegates to a ShellDriver for all shell-specific operations,
allowing transparent support for zsh, bash, and POSIX sh.

Related modules:
- ``pty_backend.py`` — OS-level PTY transport (start/stop process, I/O).
- ``terminal.py`` — Textual widget (rendering, draining, event handling).
"""

from __future__ import annotations

import logging
import re
from abc import ABC, abstractmethod
from pathlib import PurePath

_logger = logging.getLogger(__name__)

_SAFE_CHARS = frozenset(
    "abcdefghijklmnopqrstuvwxyz"
    "ABCDEFGHIJKLMNOPQRSTUVWXYZ"
    "0123456789"
    "/._-"
)

_LINE_CONTINUATION_LIMIT = 250


def _ansi_c_quote(arg: str) -> str:
    """Quote *arg* using ANSI-C ``$'...'`` syntax with octal escapes.

    Every byte outside ``[a-zA-Z0-9/._-]`` is escaped as ``\\ooo`` (3-digit octal).
    Line continuations (``\\\\\\n``) are inserted every 250 bytes to stay within
    the kernel cooked-mode buffer limit on some platforms.

    This quoting scheme is the same one used by Midnight Commander for cd commands
    in bash and zsh.
    """
    parts: list[str] = []
    line_len = 0
    for char in arg:
        if char in _SAFE_CHARS:
            parts.append(char)
            line_len += 1
        else:
            escaped = f"\\{ord(char):03o}"
            parts.append(escaped)
            line_len += len(escaped)
        if line_len >= _LINE_CONTINUATION_LIMIT:
            parts.append("\\\n")
            line_len = 0
    return "$'" + "".join(parts) + "'"
```

- [ ] **Step 5: Run `_ansi_c_quote` tests to verify they pass**

```
uv run pytest tests/terminal/test_shell_driver.py -k "ansi_c_quote" -v
```

Expected: all PASS.

- [ ] **Step 6: Write failing tests for `ZshDriver`**

Append to `tests/terminal/test_shell_driver.py`:

```python
# ---------------------------------------------------------------------------
# ZshDriver
# ---------------------------------------------------------------------------


def test_zsh_driver_init_code_embeds_fd() -> None:
    driver = ZshDriver()
    code = driver.init_code(7)
    assert ">&7" in code


def test_zsh_driver_init_code_contains_kill_stop() -> None:
    driver = ZshDriver()
    code = driver.init_code(7)
    assert "kill -STOP $$" in code


def test_zsh_driver_init_code_uses_precmd_functions() -> None:
    driver = ZshDriver()
    code = driver.init_code(5)
    assert "precmd_functions" in code


def test_zsh_driver_init_code_ends_with_newline() -> None:
    driver = ZshDriver()
    code = driver.init_code(3)
    assert code.endswith("\n")


def test_zsh_driver_init_code_prints_pid_and_pwd() -> None:
    driver = ZshDriver()
    code = driver.init_code(3)
    assert "$$" in code
    assert "pwd" in code


def test_zsh_driver_quote_simple_path() -> None:
    driver = ZshDriver()
    result = driver.quote("/home/user")
    assert result == "$'/home/user'"


def test_zsh_driver_quote_special_chars() -> None:
    driver = ZshDriver()
    result = driver.quote("/home/user/O'Brien")
    assert "\\047" in result


def test_zsh_driver_cd_command() -> None:
    driver = ZshDriver()
    cmd = driver.cd_command("/tmp")
    assert cmd.startswith("cd ")
    assert "$'" in cmd


def test_zsh_driver_supports_stop_resume() -> None:
    driver = ZshDriver()
    assert driver.supports_stop_resume is True


def test_zsh_driver_parse_precmd_payload_normal() -> None:
    driver = ZshDriver()
    pid, cwd = driver.parse_precmd_payload("12345:/home/user\n")
    assert pid == 12345
    assert cwd == PurePath("/home/user")


def test_zsh_driver_parse_precmd_payload_strips_whitespace() -> None:
    driver = ZshDriver()
    pid, cwd = driver.parse_precmd_payload("  99:/var/log  \n")
    assert pid == 99
    assert cwd == PurePath("/var/log")


def test_zsh_driver_parse_precmd_payload_malformed_returns_fallback() -> None:
    driver = ZshDriver()
    pid, cwd = driver.parse_precmd_payload("garbage data\n")
    assert pid is None
    assert cwd == PurePath("/")
```

- [ ] **Step 7: Run ZshDriver tests to verify they fail**

```
uv run pytest tests/terminal/test_shell_driver.py -k "zsh_driver" -v
```

Expected: FAIL — `ZshDriver` class does not exist yet.

- [ ] **Step 8: Implement `ZshDriver`**

Append to `src/nova_navigator/terminal/shell_driver.py`:

```python
class ShellDriver(ABC):
    """Abstract base class for shell-specific terminal integration.

    A ShellDriver knows how to:
    - Generate init code that installs a precmd hook in the shell.
    - Quote arguments safely for that shell's syntax.
    - Build a cd command.
    - Parse precmd pipe output.

    Concrete subclasses exist for zsh, bash, and a POSIX sh fallback.
    The Terminal widget delegates to a ShellDriver for all operations
    that depend on the shell language.
    """

    @abstractmethod
    def init_code(self, precmd_fd: int | None) -> str:
        """Return shell code to inject at startup.

        The code must set up a precmd hook that writes the shell's PID and
        current working directory to file descriptor *precmd_fd*.

        Args:
            precmd_fd: The fd number of the write end of the precmd pipe,
                or None if the backend has no precmd pipe.

        Returns:
            A string of shell code ending with a newline, or an empty string
            if no hook can be installed.
        """

    @abstractmethod
    def quote(self, arg: str) -> str:
        """Return a shell-safe quoted form of *arg*.

        For bash/zsh this uses ANSI-C ``$'...'`` quoting with octal escapes.
        """

    def cd_command(self, path: str) -> str:
        """Return a complete shell command that changes directory to *path*.

        The default implementation prepends ``cd`` to the quoted path.
        ``FallbackDriver`` overrides this with a different quoting strategy.
        """
        return f"cd {self.quote(path)}"

    @property
    @abstractmethod
    def supports_stop_resume(self) -> bool:
        """True if ``init_code()`` includes ``kill -STOP $$``.

        When True, the Terminal widget expects the shell to stop after each
        precmd and will send SIGCONT via the backend to resume it.
        """

    @abstractmethod
    def parse_precmd_payload(self, raw: str) -> tuple[int | None, PurePath]:
        """Parse a raw precmd pipe message.

        Args:
            raw: The raw string read from the precmd pipe.

        Returns:
            A tuple of (shell_pid, cwd).  shell_pid is None when
            stop/resume is not used or the payload is malformed.
        """


class ZshDriver(ShellDriver):
    """Shell driver for zsh.

    Installs a precmd hook via ``precmd_functions`` that writes ``PID:CWD``
    to the precmd pipe and then sends ``kill -STOP $$`` to freeze the shell
    until the Terminal widget sends SIGCONT.
    """

    @property
    def supports_stop_resume(self) -> bool:
        return True

    def init_code(self, precmd_fd: int | None) -> str:
        if precmd_fd is None:
            return ""
        return (
            f" _nn_precmd() {{ printf '%d:%s\\n' $$ $(pwd) >&{precmd_fd};"
            f" kill -STOP $$ }};"
            f" precmd_functions+=(_nn_precmd)\n"
        )

    def quote(self, arg: str) -> str:
        return _ansi_c_quote(arg)

    def parse_precmd_payload(self, raw: str) -> tuple[int | None, PurePath]:
        return _parse_pid_colon_path(raw)
```

And the shared payload parser (add above `ZshDriver`):

```python
_RE_PID_PATH = re.compile(r"^\s*(\d+):(.+)$")


def _parse_pid_colon_path(raw: str) -> tuple[int | None, PurePath]:
    """Parse a ``PID:/path`` precmd message.

    Returns ``(pid, cwd)`` on success, or ``(None, PurePath("/"))`` if the
    payload is malformed.
    """
    cleaned = raw.strip()
    match = _RE_PID_PATH.match(cleaned)
    if match:
        return int(match.group(1)), PurePath(match.group(2))
    _logger.warning("Malformed precmd payload: %r", raw)
    return None, PurePath("/")
```

- [ ] **Step 9: Run ZshDriver tests to verify they pass**

```
uv run pytest tests/terminal/test_shell_driver.py -k "zsh_driver" -v
```

Expected: all PASS.

- [ ] **Step 10: Write failing tests for `BashDriver`**

Append to `tests/terminal/test_shell_driver.py`:

```python
# ---------------------------------------------------------------------------
# BashDriver
# ---------------------------------------------------------------------------


def test_bash_driver_init_code_embeds_fd() -> None:
    driver = BashDriver()
    code = driver.init_code(5)
    assert ">&5" in code


def test_bash_driver_init_code_contains_kill_stop() -> None:
    driver = BashDriver()
    code = driver.init_code(5)
    assert "kill -STOP $$" in code


def test_bash_driver_init_code_uses_prompt_command() -> None:
    driver = BashDriver()
    code = driver.init_code(5)
    assert "PROMPT_COMMAND" in code


def test_bash_driver_init_code_ends_with_newline() -> None:
    driver = BashDriver()
    code = driver.init_code(5)
    assert code.endswith("\n")


def test_bash_driver_supports_stop_resume() -> None:
    driver = BashDriver()
    assert driver.supports_stop_resume is True


def test_bash_driver_quote_uses_ansi_c() -> None:
    driver = BashDriver()
    result = driver.quote("/tmp/test")
    assert result.startswith("$'")


def test_bash_driver_cd_command() -> None:
    driver = BashDriver()
    cmd = driver.cd_command("/var/log")
    assert cmd.startswith("cd ")


def test_bash_driver_parse_precmd_payload() -> None:
    driver = BashDriver()
    pid, cwd = driver.parse_precmd_payload("9999:/opt/app\n")
    assert pid == 9999
    assert cwd == PurePath("/opt/app")
```

- [ ] **Step 11: Implement `BashDriver`**

Append to `src/nova_navigator/terminal/shell_driver.py`:

```python
class BashDriver(ShellDriver):
    """Shell driver for bash.

    Installs a precmd hook via ``PROMPT_COMMAND`` that writes ``PID:CWD``
    to the precmd pipe and then sends ``kill -STOP $$`` to freeze the shell.
    Uses the same ANSI-C quoting and precmd format as ``ZshDriver``.
    """

    @property
    def supports_stop_resume(self) -> bool:
        return True

    def init_code(self, precmd_fd: int | None) -> str:
        if precmd_fd is None:
            return ""
        return (
            f" _nn_precmd() {{ printf '%d:%s\\n' $$ $(pwd) >&{precmd_fd};"
            f" kill -STOP $$; }};"
            " PROMPT_COMMAND=${PROMPT_COMMAND:+${PROMPT_COMMAND}$'\\n'}_nn_precmd\n"
        )

    def quote(self, arg: str) -> str:
        return _ansi_c_quote(arg)

    def parse_precmd_payload(self, raw: str) -> tuple[int | None, PurePath]:
        return _parse_pid_colon_path(raw)
```

- [ ] **Step 12: Run BashDriver tests to verify they pass**

```
uv run pytest tests/terminal/test_shell_driver.py -k "bash_driver" -v
```

Expected: all PASS.

- [ ] **Step 13: Write failing tests for `FallbackDriver`**

Append to `tests/terminal/test_shell_driver.py`:

```python
# ---------------------------------------------------------------------------
# FallbackDriver
# ---------------------------------------------------------------------------


def test_fallback_driver_supports_stop_resume_is_false() -> None:
    driver = FallbackDriver()
    assert driver.supports_stop_resume is False


def test_fallback_driver_init_code_with_fd() -> None:
    driver = FallbackDriver()
    code = driver.init_code(4)
    assert ">&4" in code
    assert "kill" not in code


def test_fallback_driver_init_code_without_fd() -> None:
    driver = FallbackDriver()
    code = driver.init_code(None)
    assert code == ""


def test_fallback_driver_init_code_ends_with_newline() -> None:
    driver = FallbackDriver()
    code = driver.init_code(4)
    assert code.endswith("\n")


def test_fallback_driver_cd_command_is_self_contained() -> None:
    driver = FallbackDriver()
    cmd = driver.cd_command("/tmp/test")
    # Must be a complete statement, not just 'cd <quoted>'
    assert "printf" in cmd
    assert "cd" in cmd


def test_fallback_driver_cd_command_does_not_start_with_cd() -> None:
    driver = FallbackDriver()
    cmd = driver.cd_command("/tmp/test")
    # FallbackDriver returns a multi-statement command, not 'cd ...'
    assert not cmd.startswith("cd ")


def test_fallback_driver_parse_precmd_payload() -> None:
    driver = FallbackDriver()
    pid, cwd = driver.parse_precmd_payload("/home/user\n")
    assert pid is None
    assert cwd == PurePath("/home/user")


def test_fallback_driver_parse_precmd_payload_strips_whitespace() -> None:
    driver = FallbackDriver()
    pid, cwd = driver.parse_precmd_payload("  /var/log  \n")
    assert pid is None
    assert cwd == PurePath("/var/log")
```

- [ ] **Step 14: Implement `FallbackDriver`**

Append to `src/nova_navigator/terminal/shell_driver.py`:

```python
def _posix_octal_escape(arg: str) -> str:
    """Escape *arg* as a sequence of ``\\0ooo`` octal codes for ``printf '%b'``.

    This is the POSIX sh fallback quoting used by Midnight Commander when
    ANSI-C ``$'...'`` is not available.
    """
    parts: list[str] = []
    for char in arg:
        parts.append(f"\\0{ord(char):03o}")
    return "".join(parts)


class FallbackDriver(ShellDriver):
    """Shell driver for generic POSIX sh.

    No SIGSTOP/SIGCONT synchronisation.  The cd command is visible on screen
    (accepted degraded behaviour).  Typed input is lost on navigation.

    Uses the Midnight Commander ``printf '%b_'`` trick for cd commands, since
    POSIX sh does not support ANSI-C ``$'...'`` quoting.
    """

    @property
    def supports_stop_resume(self) -> bool:
        return False

    def init_code(self, precmd_fd: int | None) -> str:
        if precmd_fd is None:
            return ""
        return f" _nn_precmd() {{ pwd >&{precmd_fd}; }}; PS1='$(_nn_precmd)'\"$PS1\"\n"

    def quote(self, arg: str) -> str:
        return _ansi_c_quote(arg)

    def cd_command(self, path: str) -> str:
        escaped = _posix_octal_escape(path)
        return f"_nn_newdir_=`printf '%b_' '{escaped}'`; cd \"${{_nn_newdir_%_}}\""

    def parse_precmd_payload(self, raw: str) -> tuple[int | None, PurePath]:
        cleaned = raw.strip()
        if not cleaned:
            _logger.warning("Empty precmd payload")
            return None, PurePath("/")
        return None, PurePath(cleaned)
```

- [ ] **Step 15: Run FallbackDriver tests to verify they pass**

```
uv run pytest tests/terminal/test_shell_driver.py -k "fallback_driver" -v
```

Expected: all PASS.

- [ ] **Step 16: Write failing tests for `detect_driver()`**

Append to `tests/terminal/test_shell_driver.py`:

```python
# ---------------------------------------------------------------------------
# detect_driver
# ---------------------------------------------------------------------------


def test_detect_driver_zsh() -> None:
    driver = detect_driver("/usr/bin/zsh")
    assert isinstance(driver, ZshDriver)


def test_detect_driver_bash() -> None:
    driver = detect_driver("/bin/bash")
    assert isinstance(driver, BashDriver)


def test_detect_driver_sh() -> None:
    driver = detect_driver("/bin/sh")
    assert isinstance(driver, FallbackDriver)


def test_detect_driver_unknown_shell() -> None:
    driver = detect_driver("/usr/bin/fish")
    assert isinstance(driver, FallbackDriver)


def test_detect_driver_command_with_arguments() -> None:
    driver = detect_driver("/usr/bin/zsh --no-rcs")
    assert isinstance(driver, ZshDriver)
```

- [ ] **Step 17: Implement `detect_driver()`**

Append to `src/nova_navigator/terminal/shell_driver.py`:

```python
def detect_driver(command: str) -> ShellDriver:
    """Return the appropriate ShellDriver for *command*.

    Inspects the basename of the first word in *command* to determine the shell.
    Falls back to ``FallbackDriver`` for unrecognised shells.
    """
    name = PurePath(command.split()[0]).name
    if name == "zsh":
        return ZshDriver()
    if name == "bash":
        return BashDriver()
    return FallbackDriver()
```

- [ ] **Step 18: Run detect_driver tests to verify they pass**

```
uv run pytest tests/terminal/test_shell_driver.py -k "detect_driver" -v
```

Expected: all PASS.

- [ ] **Step 19: Run all shell_driver tests**

```
uv run pytest tests/terminal/test_shell_driver.py -v
```

Expected: all PASS.

- [ ] **Step 20: Coding-guideline follow-up checklist**

- [ ] Conventions file read: `docs/coding_conventions.md`
- [ ] Naming conventions match project rules for all new/edited symbols
- [ ] Language-specific guidelines are followed (full type annotations, `X | None` not `Optional[X]`)
- [ ] Task-level verification commands from the plan executed and passing
- [ ] Any convention violations fixed before moving to next task

---

## Task 2: Create `pty_backend.py` with `PtyBackend` ABC and `LocalPtyBackend`

**Files:**
- Create: `src/nova_navigator/terminal/pty_backend.py`
- Create: `tests/terminal/test_pty_backend.py`

This task extracts the PTY plumbing from `widgets/terminal.py` into the backend abstraction.

- [ ] **Step 1: Write failing tests for `LocalPtyBackend`**

Create `tests/terminal/test_pty_backend.py`:

```python
"""Unit tests for PtyBackend ABC and LocalPtyBackend."""

from __future__ import annotations

import asyncio
import os
import signal

import pytest

from nova_navigator.terminal.pty_backend import LocalPtyBackend, PtyBackend


# ---------------------------------------------------------------------------
# ABC contract
# ---------------------------------------------------------------------------


def test_local_pty_backend_is_a_pty_backend() -> None:
    backend = LocalPtyBackend()
    assert isinstance(backend, PtyBackend)


def test_local_pty_backend_supports_precmd_pipe() -> None:
    backend = LocalPtyBackend()
    assert backend.supports_precmd_pipe is True


# ---------------------------------------------------------------------------
# open / teardown lifecycle
# ---------------------------------------------------------------------------


def test_open_returns_precmd_fd_number() -> None:
    backend = LocalPtyBackend()
    try:
        precmd_fd = backend.open("/bin/sh", rows=24, cols=80)
        assert isinstance(precmd_fd, int)
        assert precmd_fd > 0
    finally:
        backend.teardown()


def test_open_creates_child_process() -> None:
    backend = LocalPtyBackend()
    try:
        backend.open("/bin/sh", rows=24, cols=80)
        # The child process should be alive
        assert backend._pid > 0
        os.kill(backend._pid, 0)  # raises if process doesn't exist
    finally:
        backend.teardown()


def test_teardown_terminates_child_process() -> None:
    backend = LocalPtyBackend()
    backend.open("/bin/sh", rows=24, cols=80)
    pid = backend._pid
    backend.teardown()
    # Process should be gone (or zombie reaped)
    with pytest.raises(ProcessLookupError):
        os.kill(pid, 0)


def test_write_sends_bytes_to_shell() -> None:
    backend = LocalPtyBackend()
    try:
        backend.open("/bin/sh", rows=24, cols=80)
        # Should not raise
        backend.write(b"echo hello\n")
    finally:
        backend.teardown()


def test_resize_does_not_raise() -> None:
    backend = LocalPtyBackend()
    try:
        backend.open("/bin/sh", rows=24, cols=80)
        backend.resize(rows=30, cols=100)
    finally:
        backend.teardown()


def test_resume_on_dead_process_does_not_raise() -> None:
    backend = LocalPtyBackend()
    backend.open("/bin/sh", rows=24, cols=80)
    backend.teardown()
    # resume() after teardown should not raise
    backend.resume()


# ---------------------------------------------------------------------------
# attach_readers / detach_readers
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_attach_readers_posts_stdout_to_recv_queue() -> None:
    backend = LocalPtyBackend()
    try:
        backend.open("/bin/sh", rows=24, cols=80)
        loop = asyncio.get_running_loop()
        recv_queue: asyncio.Queue[list[object]] = asyncio.Queue()
        backend.attach_readers(loop, recv_queue)

        # Write something to trigger stdout
        backend.write(b"echo HELLO_MARKER\n")

        # Wait for output
        found = False
        for _ in range(50):
            await asyncio.sleep(0.05)
            while not recv_queue.empty():
                msg = recv_queue.get_nowait()
                if msg[0] == "stdout" and "HELLO_MARKER" in str(msg[1]):
                    found = True
                    break
            if found:
                break

        backend.detach_readers()
        assert found, "Expected stdout with HELLO_MARKER in recv_queue"
    finally:
        backend.teardown()


@pytest.mark.asyncio
async def test_detach_readers_stops_output_flow() -> None:
    backend = LocalPtyBackend()
    try:
        backend.open("/bin/sh", rows=24, cols=80)
        loop = asyncio.get_running_loop()
        recv_queue: asyncio.Queue[list[object]] = asyncio.Queue()
        backend.attach_readers(loop, recv_queue)
        backend.detach_readers()

        # After detach, writing should not produce queue messages
        # (drain any pending messages first)
        await asyncio.sleep(0.05)
        while not recv_queue.empty():
            recv_queue.get_nowait()

        backend.write(b"echo SHOULD_NOT_APPEAR\n")
        await asyncio.sleep(0.1)
        messages = []
        while not recv_queue.empty():
            messages.append(recv_queue.get_nowait())
        stdout_msgs = [m for m in messages if m[0] == "stdout"]
        assert len(stdout_msgs) == 0
    finally:
        backend.teardown()
```

- [ ] **Step 2: Run tests to verify they fail**

```
uv run pytest tests/terminal/test_pty_backend.py -v
```

Expected: FAIL — `nova_navigator.terminal.pty_backend` does not exist.

- [ ] **Step 3: Implement `PtyBackend` ABC and `LocalPtyBackend`**

Create `src/nova_navigator/terminal/pty_backend.py`:

```python
"""PTY backend abstraction for terminal process management.

This module provides the ``PtyBackend`` ABC and ``LocalPtyBackend`` implementation.
A PtyBackend handles all OS-level concerns of running a shell process:
starting/stopping the process, reading/writing bytes, resizing the terminal,
and managing the precmd pipe for CWD tracking.

The backend does not know anything about shell languages (hooks, quoting) —
that is the responsibility of ``ShellDriver`` in ``shell_driver.py``.

The backend does not know about rendering, draining, or Textual widgets —
that is the responsibility of ``Terminal`` in ``terminal.py``.

Related modules:
- ``shell_driver.py`` — shell-specific hook installation and quoting.
- ``terminal.py`` — Textual widget that consumes backend I/O.
"""

from __future__ import annotations

import asyncio
import contextlib
import fcntl
import logging
import os
import pty
import shlex
import signal
import struct
import termios
from abc import ABC, abstractmethod

_logger = logging.getLogger(__name__)


class PtyBackend(ABC):
    """Abstract base class for terminal process backends.

    A PtyBackend manages the lifecycle of a shell process and provides
    byte-level I/O.  It does not interpret the bytes — the Terminal widget
    feeds them to pyte for rendering.

    Lifecycle: ``open()`` → ``attach_readers()`` → (normal operation)
    → ``detach_readers()`` → ``teardown()``.
    """

    @abstractmethod
    def open(self, command: str, rows: int, cols: int) -> int | None:
        """Start the shell process.

        Args:
            command: The shell command to execute (e.g. ``"/usr/bin/zsh"``).
            rows: Initial terminal height.
            cols: Initial terminal width.

        Returns:
            The precmd pipe child-side fd number for embedding in shell init code,
            or None if this backend does not support a precmd pipe.
        """

    @abstractmethod
    def write(self, data: bytes) -> None:
        """Write raw bytes to the shell's stdin."""

    @abstractmethod
    def resize(self, rows: int, cols: int) -> None:
        """Resize the terminal.

        For local PTYs this sends ``TIOCSWINSZ``.
        For SSH this calls ``channel.resize_pty()``.
        """

    @abstractmethod
    def resume(self) -> None:
        """Send SIGCONT to the managed shell process.

        Called after a precmd that used ``kill -STOP $$`` to freeze the shell.
        No-op for backends that do not support stop/resume.
        Suppresses ``ProcessLookupError`` if the shell has already exited.
        """

    @abstractmethod
    def attach_readers(
        self,
        loop: asyncio.AbstractEventLoop,
        recv_queue: asyncio.Queue[list[object]],
    ) -> None:
        """Register callbacks that pump stdout and precmd data into *recv_queue*.

        For local PTYs this uses ``loop.add_reader()``.
        For SSH backends this would start a reader thread.
        The backend stores the *loop* reference for use by ``detach_readers()``.

        Messages pushed to *recv_queue*:
        - ``["stdout", text]`` — decoded shell output.
        - ``["pre_cmd", text]`` — raw precmd pipe data.
        - ``["disconnect", 1]`` — shell process exited or I/O error.
        """

    @abstractmethod
    def detach_readers(self) -> None:
        """Remove previously registered reader callbacks.

        Uses the loop reference stored during ``attach_readers()``.
        """

    @abstractmethod
    def teardown(self) -> None:
        """Terminate the shell process and close all file objects."""

    @property
    @abstractmethod
    def supports_precmd_pipe(self) -> bool:
        """True if this backend creates a separate out-of-band precmd pipe."""


class LocalPtyBackend(PtyBackend):
    """PTY backend for local shell processes.

    Uses ``pty.fork()`` to create a pseudo-terminal and ``os.pipe()`` for the
    out-of-band precmd communication channel.  The shell writes its PID and CWD
    to the pipe after each command; the parent reads it via ``loop.add_reader()``.
    """

    def __init__(self) -> None:
        self._pid: int = -1
        self._master_fd: int = -1
        self._p_out: object | None = None  # file object wrapping master_fd
        self._p_out_pre_cmd: object | None = None  # file object wrapping precmd read fd
        self._loop: asyncio.AbstractEventLoop | None = None

    @property
    def supports_precmd_pipe(self) -> bool:
        return True

    def open(self, command: str, rows: int, cols: int) -> int | None:
        fd_pre_cmd_parent, fd_pre_cmd_child = os.pipe()

        pid, fd = pty.fork()
        if pid == 0:
            # Child process
            os.close(fd_pre_cmd_parent)
            os.set_inheritable(fd_pre_cmd_child, True)
            argv = shlex.split(command)
            env = os.environ.copy()
            env["TERM"] = "xterm-256color"
            env["LC_ALL"] = "en_US.UTF-8"
            os.execvpe(argv[0], argv, env)  # noqa: S606
            raise RuntimeError("execvpe failed")

        # Parent process
        os.close(fd_pre_cmd_child)
        self._pid = pid
        self._master_fd = fd
        self._precmd_fd_child = fd_pre_cmd_child  # numeric value for init_code
        self._p_out = os.fdopen(fd, "w+b", 0)
        self._p_out_pre_cmd = os.fdopen(fd_pre_cmd_parent, "w+b", 0)

        # Apply initial size
        self.resize(rows, cols)

        return fd_pre_cmd_child

    def write(self, data: bytes) -> None:
        assert self._p_out is not None
        self._p_out.write(data)

    def resize(self, rows: int, cols: int) -> None:
        winsize = struct.pack("HH", rows, cols)
        fcntl.ioctl(self._master_fd, termios.TIOCSWINSZ, winsize)

    def resume(self) -> None:
        with contextlib.suppress(ProcessLookupError, OSError):
            if self._pid > 0:
                os.kill(self._pid, signal.SIGCONT)

    def attach_readers(
        self,
        loop: asyncio.AbstractEventLoop,
        recv_queue: asyncio.Queue[list[object]],
    ) -> None:
        self._loop = loop
        p_out = self._p_out
        p_out_pre_cmd = self._p_out_pre_cmd

        from textual.app import log

        def on_output() -> None:
            try:
                assert p_out is not None
                read = p_out.read(65536).decode()
                recv_queue.put_nowait(["stdout", read])
            except UnicodeDecodeError as error:
                log.warning("decode error:", error)
            except Exception:  # noqa: BLE001
                loop.remove_reader(p_out)
                recv_queue.put_nowait(["disconnect", 1])

        def on_pre_cmd() -> None:
            try:
                assert p_out_pre_cmd is not None
                recv_queue.put_nowait(["pre_cmd", p_out_pre_cmd.read(65536).decode()])
            except UnicodeDecodeError:
                pass
            except Exception:  # noqa: BLE001
                loop.remove_reader(p_out_pre_cmd)

        loop.add_reader(p_out, on_output)
        loop.add_reader(p_out_pre_cmd, on_pre_cmd)

    def detach_readers(self) -> None:
        if self._loop is not None:
            with contextlib.suppress(Exception):
                self._loop.remove_reader(self._p_out)
            with contextlib.suppress(Exception):
                self._loop.remove_reader(self._p_out_pre_cmd)

    def teardown(self) -> None:
        with contextlib.suppress(OSError):
            if self._pid > 0:
                os.kill(self._pid, signal.SIGTERM)
        with contextlib.suppress(OSError):
            if self._pid > 0:
                os.waitpid(self._pid, os.WNOHANG)
        with contextlib.suppress(OSError):
            if self._p_out is not None:
                self._p_out.close()
        with contextlib.suppress(OSError):
            if self._p_out_pre_cmd is not None:
                self._p_out_pre_cmd.close()
        self._pid = -1
```

- [ ] **Step 4: Run backend tests to verify they pass**

```
uv run pytest tests/terminal/test_pty_backend.py -v
```

Expected: all PASS.

- [ ] **Step 5: Coding-guideline follow-up checklist**

- [ ] Conventions file read: `docs/coding_conventions.md`
- [ ] Naming conventions match project rules for all new/edited symbols
- [ ] Language-specific guidelines are followed
- [ ] Task-level verification commands from the plan executed and passing
- [ ] Any convention violations fixed before moving to next task

---

## Task 3: Create trimmed `terminal/terminal.py`

**Files:**
- Create: `src/nova_navigator/terminal/terminal.py`
- Reference: `src/nova_navigator/widgets/terminal.py` (source of the move)

This task creates the new terminal module by adapting the existing `widgets/terminal.py` to use `PtyBackend` and `ShellDriver`. The file is created fresh (not moved) because significant logic changes.

- [ ] **Step 1: Create `terminal/terminal.py`**

Create `src/nova_navigator/terminal/terminal.py` with the following content.
This is the full trimmed widget — all PTY plumbing delegated to `PtyBackend`, all shell logic delegated to `ShellDriver`:

```python
"""PTY-backed terminal emulator widget for Textual.

This module contains the ``Terminal`` widget, which embeds a shell inside a
Textual application.  It delegates OS-level PTY management to a ``PtyBackend``
and shell-specific hook/quoting logic to a ``ShellDriver``.

The widget owns:
- The pyte virtual screen and ANSI parser.
- The Rich text rendering pipeline (``TerminalDisplay``).
- The draining state machine for silent directory navigation.
- Keyboard and mouse event handling.
- The recv_queue processing loop.

It does NOT own:
- Process lifecycle (start/stop/signal) — that's ``PtyBackend``.
- Shell init code, quoting, precmd parsing — that's ``ShellDriver``.

Based on David Brochart's pyte example:
https://github.com/selectel/pyte/blob/master/examples/terminal_emulator.py

Related modules:
- ``pty_backend.py`` — ``PtyBackend`` ABC and ``LocalPtyBackend``.
- ``shell_driver.py`` — ``ShellDriver`` ABC and concrete drivers.
"""

from __future__ import annotations

import asyncio
import logging
import re
from asyncio import Task, TimerHandle
from pathlib import PurePath
from typing import Any, Literal

import pyte
from pyte.screens import Char
from rich.color import ColorParseError
from rich.console import Console, ConsoleOptions, ConsoleRenderable
from rich.console import RenderResult as RichRenderResult
from rich.style import Style
from rich.text import Text
from textual import events
from textual.app import RenderResult, log
from textual.message import Message
from textual.widget import Widget

from nova_navigator.terminal.pty_backend import LocalPtyBackend, PtyBackend
from nova_navigator.terminal.shell_driver import ShellDriver, detect_driver

_logger = logging.getLogger(__name__)

__all__ = [
    "Terminal",
    "TerminalDisplay",
    "TerminalPyteScreen",
]

_KILL_LINE = "\x15"  # Ctrl+U — kill whole line to kill ring
_YANK = "\x19"  # Ctrl+Y — yank from kill ring
_END_OF_LINE = "\x05"  # Ctrl+E — move cursor to end of line


_MOUSE_TRACKING_MODES: frozenset[str] = frozenset({"1000", "1002", "1003", "1006"})
_RECV_DRAIN_LIMIT: int = 100
_DISPLAY_FPS: float = 60.0

_re_ansi_sequence = re.compile(r"(\x1b\[\??[\d;]*[a-zA-Z])")
_DECSET_PREFIX = "\x1b[?"


class TerminalPyteScreen(pyte.Screen):
    """pyte.Screen subclass that drops the unsupported ``private`` keyword from ``set_margins``.

    Workaround for a pyte compatibility issue triggered by certain escape sequences.
    """

    def set_margins(self, *args: Any, **kwargs: Any) -> None:
        kwargs.pop("private", None)
        return super().set_margins(*args, **kwargs)


class TerminalDisplay(ConsoleRenderable):
    """Rich renderable for a single terminal frame."""

    def __init__(self, lines: list[Text], cursor_x: int, cursor_y: int) -> None:
        self.lines = lines
        self.cursor_x = cursor_x
        self.cursor_y = cursor_y

    def __rich_console__(self, console: Console, options: ConsoleOptions) -> RichRenderResult:
        result: list[Text] = []
        for y, line in enumerate(self.lines):
            if y == self.cursor_y:
                rendered_line = line.copy()
                rendered_line.stylize("reverse", self.cursor_x, self.cursor_x + 1)
            else:
                rendered_line = line
            result.append(rendered_line)
        return result


_CTRL_KEYS: dict[str, str] = {
    "up": "\x1bOA",
    "down": "\x1bOB",
    "right": "\x1bOC",
    "left": "\x1bOD",
    "home": "\x1bOH",
    "end": "\x1b[F",
    "delete": "\x1b[3~",
    "pageup": "\x1b[5~",
    "pagedown": "\x1b[6~",
    "shift+tab": "\x1b[Z",
    "f1": "\x1bOP",
    "f2": "\x1bOQ",
    "f3": "\x1bOR",
    "f4": "\x1bOS",
    "f5": "\x1b[15~",
    "f6": "\x1b[17~",
    "f7": "\x1b[18~",
    "f8": "\x1b[19~",
    "f9": "\x1b[20~",
    "f10": "\x1b[21~",
    "f11": "\x1b[23~",
    "f12": "\x1b[24~",
    "f13": "\x1b[25~",
    "f14": "\x1b[26~",
    "f15": "\x1b[28~",
    "f16": "\x1b[29~",
    "f17": "\x1b[31~",
    "f18": "\x1b[32~",
    "f19": "\x1b[33~",
    "f20": "\x1b[34~",
}

_TERMINAL_COLORS: dict[str, str] = {
    "black": "#000000",
    "red": "#AB4642",
    "green": "#A1B56C",
    "yellow": "#FEA62B",
    "blue": "#2871C5",
    "magenta": "#BA8BAF",
    "cyan": "#86C1B9",
    "brown": "#FEA62B",
    "white": "#FFFFFF",
    "brightblack": "#444444",
    "default": "default",
}


def _translate_terminal_color(color: str) -> str:
    """Map a pyte color name or 6-digit hex string to a Rich-compatible color string."""
    if re.fullmatch("[0-9a-f]{6}", color, re.IGNORECASE):
        return f"#{color}"
    if color in _TERMINAL_COLORS:
        return _TERMINAL_COLORS[color]
    return color


def _encode_mouse(msg: list[Any]) -> bytes:
    """Encode a mouse event message as SGR escape bytes for the PTY."""
    if msg[0] == "click":
        x = int(msg[1]) + 1
        y = int(msg[2]) + 1
        button = int(msg[3])
        if button == 1:
            return f"\x1b[<0;{x};{y}M\x1b[<0;{x};{y}m".encode()
        return b""
    elif msg[0] == "scroll":
        x = int(msg[2]) + 1
        y = int(msg[3]) + 1
        if msg[1] == "up":
            return f"\x1b[<64;{x};{y}M".encode()
        if msg[1] == "down":
            return f"\x1b[<65;{x};{y}M".encode()
    return b""


class Terminal(Widget, can_focus=True):
    """PTY-backed terminal emulator widget for Textual.

    Embeds a shell process and renders its output via pyte and Rich.
    Delegates process management to a ``PtyBackend`` and shell-specific
    logic to a ``ShellDriver``.

    The SIGSTOP synchronisation model:
    When using a shell that supports it (zsh, bash), the precmd hook sends
    ``kill -STOP $$`` after writing the CWD to the precmd pipe.  This freezes
    the shell until ``Terminal`` calls ``backend.resume()``.  This makes
    directory navigation deterministic — no race between output suppression
    and shell prompt rendering.
    """

    DEFAULT_CSS = """
    Terminal {
        background: $background;
    }
    """

    class PreCmd(Message):
        """Posted after each command completes in the embedded shell."""

        def __init__(self, terminal_widget: Terminal, cwd: PurePath) -> None:
            self.terminal_widget = terminal_widget
            self.cwd = cwd
            super().__init__()

    class Closed(Message):
        """Posted when the underlying shell process exits and ``keep_alive`` is False."""

        def __init__(self, terminal_widget: Terminal) -> None:
            self.terminal_widget = terminal_widget
            super().__init__()

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
        self.keep_alive = keep_alive
        self._backend = backend or LocalPtyBackend()
        self._driver = driver or detect_driver(command)
        self._started = False
        self._draining = False
        self.ncol = 80
        self.nrow = 24
        self.mouse_tracking = False

        self.send_queue: asyncio.Queue[list[object]] | None = None
        self.recv_queue: asyncio.Queue[list[object]] | None = None
        self.recv_task_t: Task[None] | None = None
        self._run_task: Task[None] | None = None
        self._rebuild_handle: TimerHandle | None = None

        self._display = self.initial_display()
        self._screen = TerminalPyteScreen(self.ncol, self.nrow)
        self._stream = pyte.Stream(self._screen)
        self._prompt_cursor_x: int = 0
        self._pending_yank: bool = False
        self._snapshot_prompt_cursor: bool = False

        super().__init__(name=name, id=id, classes=classes)

    # ------------------------------------------------------------------
    # Lifecycle
    # ------------------------------------------------------------------

    def start(self) -> None:
        if self._started:
            return

        self.ncol = 80
        self.nrow = 24

        self.recv_queue = asyncio.Queue()
        self._start_backend()
        self.recv_task_t = asyncio.create_task(self.recv())
        self._started = True

    def _start_backend(self) -> None:
        """Open the backend, start the send loop, and inject shell init code.

        When the driver supports stop/resume, draining is enabled immediately.
        The shell will freeze after its first precmd (startup); recv() will
        send SIGCONT to resume it and end the startup drain.
        """
        precmd_fd = self._backend.open(self.command, self.nrow, self.ncol)
        self.send_queue = asyncio.Queue()
        self._run_task = asyncio.create_task(self._run())
        # The shell will STOP after its first precmd.  Set draining so the
        # startup output (init code echo) is suppressed.
        if self._driver.supports_stop_resume:
            self._draining = True
        init = self._driver.init_code(precmd_fd)
        if init:
            self._backend.write(init.encode())

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

    def render(self) -> RenderResult:
        return self._display

    # ------------------------------------------------------------------
    # Event handlers
    # ------------------------------------------------------------------

    async def on_key(self, event: events.Key) -> None:
        if not self._started:
            return

        if event.key == "ctrl+f1":
            self.app.set_focus(None)
            return

        event.stop()
        char = _CTRL_KEYS.get(event.key) or event.character
        if char:
            assert self.send_queue is not None
            self.send_queue.put_nowait(["stdin", char])

    def has_input(self) -> bool:
        """Return True if the user has typed something on the current prompt line."""
        log.info("cursor, prompt cursor:", self._screen.cursor.x, self._prompt_cursor_x)
        return self._screen.cursor.x > self._prompt_cursor_x

    async def set_terminal_directory(self, path: PurePath) -> None:
        """Change the shell's working directory to *path*, preserving any typed input.

        For drivers that support stop/resume (zsh, bash):
        - Typed text is saved to the kill ring with Ctrl+U.
        - The cd command runs silently (draining suppresses echo).
        - After precmd, the text is restored with Ctrl+Y.

        For FallbackDriver (POSIX sh):
        - The cd command is written directly (echo visible).
        - Typed input is lost.
        """
        if not self._started:
            return
        if self._driver.supports_stop_resume:
            self._pending_yank = self.has_input()
            if self._pending_yank:
                self._backend.write(_KILL_LINE.encode())
            self._draining = True
        cmd = self._driver.cd_command(str(path)) + "\n"
        self._backend.write(cmd.encode())

    async def send(self, data: str, mode: Literal["normal", "silent"] = "normal") -> None:
        """Send *data* to the shell.

        When *mode* is ``"silent"`` and the driver supports stop/resume,
        the echo of *data* is suppressed until the next precmd fires.
        """
        if not self._started:
            return
        if mode == "silent" and self._driver.supports_stop_resume:
            # Set draining synchronously before writing so that any output
            # arriving between the write and the precmd is suppressed.
            self._draining = True
        self._backend.write(data.encode())

    async def on_resize(self, _event: events.Resize) -> None:
        if not self._started:
            return
        self.ncol = self.size.width
        self.nrow = self.size.height
        assert self.send_queue is not None
        self.send_queue.put_nowait(["set_size", self.nrow, self.ncol])
        self._screen.resize(self.nrow, self.ncol)

    def _mouse_ready(self) -> bool:
        """Return True if the terminal is started and mouse tracking is active."""
        return self._started and self.mouse_tracking

    async def on_click(self, event: events.Click) -> None:
        if not self._mouse_ready():
            return
        assert self.send_queue is not None
        self.send_queue.put_nowait(["click", event.x, event.y, event.button])

    async def on_mouse_scroll_down(self, event: events.MouseScrollDown) -> None:
        if not self._mouse_ready():
            return
        assert self.send_queue is not None
        self.send_queue.put_nowait(["scroll", "down", event.x, event.y])

    async def on_mouse_scroll_up(self, event: events.MouseScrollUp) -> None:
        if not self._mouse_ready():
            return
        assert self.send_queue is not None
        self.send_queue.put_nowait(["scroll", "up", event.x, event.y])

    # ------------------------------------------------------------------
    # recv loop
    # ------------------------------------------------------------------

    async def recv(self) -> None:
        """Process messages from recv_queue: stdout, pre_cmd, setup, disconnect."""
        assert self.recv_queue is not None
        try:
            while True:
                message = await self.recv_queue.get()
                stdout_fed = False
                disconnected = False
                for _ in range(_RECV_DRAIN_LIMIT):
                    cmd = message[0]
                    if cmd == "setup":
                        assert self.send_queue is not None
                        self.send_queue.put_nowait(["set_size", self.nrow, self.ncol])
                    elif cmd == "pre_cmd":
                        raw = str(message[1])
                        _pid, cwd = self._driver.parse_precmd_payload(raw)
                        if self._draining:
                            # Navigation cd has completed.  Write yank bytes
                            # before resuming so they arrive at the shell before
                            # it prints the new prompt.
                            if self._pending_yank:
                                self._pending_yank = False
                                self._backend.write((_YANK + _END_OF_LINE).encode())
                            self._backend.resume()
                            self._draining = False
                        self._snapshot_prompt_cursor = True
                        self.post_message(Terminal.PreCmd(self, cwd))
                    elif cmd == "stdout":
                        if not self._draining:
                            self._feed_stdout(str(message[1]))
                            stdout_fed = True
                    elif cmd == "disconnect":
                        disconnected = True
                        break
                    try:
                        message = self.recv_queue.get_nowait()
                    except asyncio.QueueEmpty:
                        break
                if stdout_fed and not self._draining:
                    self._schedule_rebuild()
                if disconnected:
                    _logger.info("Terminal disconnected")
                    if self.keep_alive:
                        self.respawn()
                    else:
                        self.post_message(Terminal.Closed(self))
                        self.stop()
        except asyncio.CancelledError:
            pass

    # ------------------------------------------------------------------
    # Internal: screen rendering
    # ------------------------------------------------------------------

    def _schedule_rebuild(self) -> None:
        """Schedule a display rebuild if one is not already pending."""
        if self._rebuild_handle is None:
            self._rebuild_handle = asyncio.get_running_loop().call_later(1.0 / _DISPLAY_FPS, self._on_rebuild_timer)

    def _on_rebuild_timer(self) -> None:
        """Timer callback: clear the handle and rebuild the display."""
        self._rebuild_handle = None
        self._rebuild_display()

    def _feed_stdout(self, chars: str) -> None:
        """Scan for DECSET sequences and feed chars to the pyte stream."""
        for sep_match in re.finditer(_re_ansi_sequence, chars):
            sequence = sep_match.group(0)
            if sequence.startswith(_DECSET_PREFIX):
                body = sequence.removeprefix(_DECSET_PREFIX)
                action = body[-1]
                modes = set(body[:-1].split(";"))
                if _MOUSE_TRACKING_MODES & modes:
                    self.mouse_tracking = action == "h"

        try:
            self._stream.feed(chars)
        except TypeError as error:
            log.warning("could not feed:", error)

    def _rebuild_display(self) -> None:
        """Rebuild Rich Text lines from the current pyte screen state and schedule a repaint."""
        lines: list[Text] = []
        for y in range(self._screen.lines):
            line_text = Text()
            line = self._screen.buffer[y]
            style_change_pos = 0
            for x in range(self._screen.columns):
                char: Char = line[x]
                line_text.append(char.data)

                is_last_col = x == self._screen.columns - 1

                if x > 0:
                    last_char: Char = line[x - 1]
                    if not self.char_style_cmp(char, last_char):
                        last_style = self.char_rich_style(last_char)
                        line_text.stylize(last_style, style_change_pos, x)
                        style_change_pos = x
                if is_last_col:
                    cur_style = self.char_rich_style(char)
                    line_text.stylize(cur_style, style_change_pos, x + 1)

            lines.append(line_text)

        self._display = TerminalDisplay(lines, self._screen.cursor.x, self._screen.cursor.y)
        if self._snapshot_prompt_cursor:
            self._snapshot_prompt_cursor = False
            self._prompt_cursor_x = self._screen.cursor.x
            if self._pending_yank:
                self._pending_yank = False
                if self.send_queue is not None:
                    self.send_queue.put_nowait(["stdin", _YANK + _END_OF_LINE])
        self.refresh()

    def _process_stdout(self, chars: str) -> None:
        """Parse ANSI output, update the pyte screen, and refresh the display."""
        self._feed_stdout(chars)
        self._rebuild_display()

    # ------------------------------------------------------------------
    # Style helpers
    # ------------------------------------------------------------------

    def char_rich_style(self, char: Char) -> Style:
        """Return a Rich Style built from the visual attributes of a pyte Char."""
        fg = _translate_terminal_color(char.fg)
        bg = _translate_terminal_color(char.bg)
        try:
            return Style(
                color=fg,
                bgcolor=bg,
                bold=char.bold,
                italic=char.italics,
                underline=char.underscore,
                strike=char.strikethrough,
                reverse=char.reverse,
                blink=char.blink,
            )
        except ColorParseError as error:
            log.warning("color parse error:", error)
            return Style()

    def _char_style_key(self, char: Char) -> tuple[str, str, bool, bool, bool, bool, bool, bool]:
        """Return a tuple of visual style attributes for a pyte Char."""
        return (
            char.fg,
            char.bg,
            char.bold,
            char.italics,
            char.underscore,
            char.strikethrough,
            char.reverse,
            char.blink,
        )

    def char_style_cmp(self, given: Char, other: Char) -> bool:
        """Return True if two pyte Chars have identical visual style."""
        return self._char_style_key(given) == self._char_style_key(other)

    def initial_display(self) -> TerminalDisplay:
        """Return the initial (empty single-line) display state."""
        return TerminalDisplay([Text()], 0, 0)

    # ------------------------------------------------------------------
    # Internal: PTY management via backend
    # ------------------------------------------------------------------

    def respawn(self) -> None:
        """Tear down the current backend and start a fresh shell.

        Keeps ``recv_task_t`` alive.  Can be called from a ``Terminal.Closed``
        handler to restart the terminal on demand.
        """
        if self._run_task is not None:
            self._run_task.cancel()
            self._run_task = None

        self._backend.detach_readers()
        self._backend.teardown()

        self._screen = TerminalPyteScreen(self.ncol, self.nrow)
        self._stream = pyte.Stream(self._screen)

        self._start_backend()

    async def _run(self) -> None:
        """Send loop: reads from send_queue and dispatches to backend."""
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
                    self._backend.resize(int(msg[1]), int(msg[2]))
                elif msg[0] in ("click", "scroll"):
                    encoded = _encode_mouse(msg)
                    if encoded:
                        self._backend.write(encoded)
        except asyncio.CancelledError:
            pass
```

- [ ] **Step 2: Verify the new terminal module is importable**

```
uv run python -c "from nova_navigator.terminal.terminal import Terminal; print('OK')"
```

Expected: `OK` (no import errors).

- [ ] **Step 3: Coding-guideline follow-up checklist**

- [ ] Conventions file read: `docs/coding_conventions.md`
- [ ] Naming conventions match project rules for all new/edited symbols
- [ ] Language-specific guidelines are followed
- [ ] Task-level verification commands from the plan executed and passing
- [ ] Any convention violations fixed before moving to next task

---

## Task 4: Create `terminal/__init__.py` and update import sites

**Files:**
- Modify: `src/nova_navigator/terminal/__init__.py`
- Modify: `src/nova_navigator/nova_navigator.py`

- [ ] **Step 1: Populate `terminal/__init__.py`**

Replace the empty `src/nova_navigator/terminal/__init__.py` with:

```python
"""Terminal sub-package — PTY-backed terminal emulator for Textual.

This package provides the ``Terminal`` widget and its supporting abstractions:

- ``PtyBackend`` / ``LocalPtyBackend`` — OS-level PTY process management.
- ``ShellDriver`` / ``ZshDriver`` / ``BashDriver`` / ``FallbackDriver`` — shell-specific
  hook installation, argument quoting, and precmd parsing.
- ``detect_driver()`` — auto-detect the appropriate driver from a command string.

Architecture overview: see ``docs/terminal.md``.
"""

from nova_navigator.terminal.pty_backend import LocalPtyBackend, PtyBackend
from nova_navigator.terminal.shell_driver import (
    BashDriver,
    FallbackDriver,
    ShellDriver,
    ZshDriver,
    detect_driver,
)
from nova_navigator.terminal.terminal import Terminal, TerminalDisplay, TerminalPyteScreen

__all__ = [
    "BashDriver",
    "FallbackDriver",
    "LocalPtyBackend",
    "PtyBackend",
    "ShellDriver",
    "Terminal",
    "TerminalDisplay",
    "TerminalPyteScreen",
    "ZshDriver",
    "detect_driver",
]
```

- [ ] **Step 2: Update import in `nova_navigator.py`**

In `src/nova_navigator/nova_navigator.py`, change line 37:

```python
# Before:
from nova_navigator.widgets.terminal import Terminal

# After:
from nova_navigator.terminal import Terminal
```

- [ ] **Step 3: Verify imports work**

```
uv run python -c "from nova_navigator.terminal import Terminal, PtyBackend, ShellDriver, detect_driver; print('OK')"
uv run python -c "from nova_navigator.nova_navigator import MainScreen; print('OK')"
```

Expected: both print `OK`.

- [ ] **Step 4: Coding-guideline follow-up checklist**

- [ ] Conventions file read: `docs/coding_conventions.md`
- [ ] Naming conventions match project rules for all new/edited symbols
- [ ] Language-specific guidelines are followed
- [ ] Task-level verification commands from the plan executed and passing
- [ ] Any convention violations fixed before moving to next task

---

## Task 5: Move and adapt terminal tests

**Files:**
- Create: `tests/terminal/test_terminal.py` (adapted from `tests/widgets/test_terminal.py`)
- Delete: `tests/widgets/test_terminal.py` (after verification)
- Delete: `src/nova_navigator/widgets/terminal.py` (after verification)

- [ ] **Step 1: Create `tests/terminal/test_terminal.py`**

Copy `tests/widgets/test_terminal.py` to `tests/terminal/test_terminal.py` and apply these changes:

1. **Update imports** — change:
   ```python
   from nova_navigator.widgets.terminal import (
       Terminal,
       TerminalDisplay,
       TerminalPyteScreen,
       _translate_terminal_color,
       shell_clear_prompt,
       shell_cmd_cd,
       shell_init_code,
   )
   ```
   to:
   ```python
   from nova_navigator.terminal.terminal import (
       Terminal,
       TerminalDisplay,
       TerminalPyteScreen,
       _translate_terminal_color,
   )
   ```

2. **Delete tests for removed functions** — remove all tests in these sections:
   - `# shell_init_code` (4 tests: `test_shell_init_code_*`)
   - `# shell_clear_prompt` (2 tests: `test_shell_clear_prompt_*`)
   - `# shell_cmd_cd` (4 tests: `test_shell_cmd_cd_*`)
   - `# shell_cmd_cd — path injection safety` (1 test: `test_shell_cmd_cd_handles_path_with_single_quote`)

   These are replaced by equivalent tests in `test_shell_driver.py`.

3. **Remove `_nav_pending` references from draining tests** — in:
   - `test_draining_suppresses_display_rebuild_until_pre_cmd`: remove `terminal._nav_pending = 1`
   - `test_normal_send_after_pre_cmd_resets_drain_appears_on_screen`: remove `terminal._nav_pending = 1`

4. **Adapt race condition tests** — update `test_race_a_*` and `test_race_c_*`:
   - Remove `_nav_pending` counter checks.
   - Replace `nav_start` queue messages with the SIGSTOP model expectations.
   - The tests should verify that `_draining` remains True when a stale pre_cmd arrives while draining, because with SIGSTOP the shell is frozen and cannot send a second pre_cmd until resumed.

5. **Update `test_set_terminal_directory_sends_kill_line_and_cd_silently`** — the cd command format changes (ANSI-C quoting instead of `shlex.quote`). Update the assertion to check that the data contains the path in some form and starts with KILL_LINE.

6. **Update `test_draining_flag_set_by_send_silent`** — `send(mode="silent")` now writes to backend directly. The test needs `terminal._backend` to be a `FakePtyBackend` (or the terminal needs to be started). Use a simple approach: set `_started = True` and provide a backend that accepts `write()`.

- [ ] **Step 2: Create a `FakePtyBackend` test helper**

Add at the top of `tests/terminal/test_terminal.py`:

```python
from nova_navigator.terminal.pty_backend import PtyBackend


class FakePtyBackend(PtyBackend):
    """Test double for PtyBackend that records calls without forking a process."""

    def __init__(self) -> None:
        self.writes: list[bytes] = []
        self.resume_count: int = 0
        self.opened: bool = False
        self.torn_down: bool = False
        self._attached: bool = False

    @property
    def supports_precmd_pipe(self) -> bool:
        return True

    def open(self, command: str, rows: int, cols: int) -> int | None:
        self.opened = True
        return 99  # fake fd

    def write(self, data: bytes) -> None:
        self.writes.append(data)

    def resize(self, rows: int, cols: int) -> None:
        pass

    def resume(self) -> None:
        self.resume_count += 1

    def attach_readers(
        self,
        loop: asyncio.AbstractEventLoop,
        recv_queue: asyncio.Queue[list[object]],
    ) -> None:
        self._attached = True

    def detach_readers(self) -> None:
        self._attached = False

    def teardown(self) -> None:
        self.torn_down = True
```

- [ ] **Step 3: Run the new terminal tests**

```
uv run pytest tests/terminal/test_terminal.py -v
```

Expected: all PASS.

- [ ] **Step 4: Run ALL tests to verify nothing is broken**

```
uv run pytest -v
```

Expected: all PASS (the old test file at `tests/widgets/test_terminal.py` still passes because `widgets/terminal.py` still exists).

- [ ] **Step 5: Delete old files**

Delete `src/nova_navigator/widgets/terminal.py` and `tests/widgets/test_terminal.py`.

```
rm src/nova_navigator/widgets/terminal.py tests/widgets/test_terminal.py
```

- [ ] **Step 6: Run ALL tests again to confirm clean state**

```
uv run pytest -v
```

Expected: all PASS.

- [ ] **Step 7: Coding-guideline follow-up checklist**

- [ ] Conventions file read: `docs/coding_conventions.md`
- [ ] Naming conventions match project rules for all new/edited symbols
- [ ] Language-specific guidelines are followed
- [ ] Task-level verification commands from the plan executed and passing
- [ ] Any convention violations fixed before moving to next task

---

## Task 6: Write architecture documentation

**Files:**
- Create: `docs/terminal.md`
- Modify: `docs/terminal-set-directory-race.md`
- Modify: `AGENTS.md`

- [ ] **Step 1: Create `docs/terminal.md`**

Create the architecture document with the following structure.
Refer to the design spec `docs/agents/specs/2026-05-01-terminal-refactoring.md` sections on timing diagrams, error handling, and API contracts.
Write one sentence per line (mandatory per AGENTS.md).

The document must cover these sections:

1. **Overview** — what the terminal sub-package does, the three-layer split.
2. **Package layout** — file listing with one-line descriptions.
3. **PtyBackend layer** — ABC contract, LocalPtyBackend implementation, lifecycle.
4. **ShellDriver layer** — ABC contract, per-driver differences, quoting, init hooks.
5. **SIGSTOP synchronisation model** — how `kill -STOP $$` works, why it eliminates the race, timing diagrams.
6. **Directory navigation flow** — step-by-step `set_terminal_directory()` walkthrough, KILL_LINE/YANK dance.
7. **Degraded mode (FallbackDriver)** — what's different when SIGSTOP is unavailable.
8. **recv_queue message protocol** — table of all message types.
9. **Extending for new backends** — how to add SSH or other backends.
10. **Extending for new shells** — how to add a new ShellDriver subclass.

- [ ] **Step 2: Update `docs/terminal-set-directory-race.md`**

Add a resolution note at the top of the file, after the title:

```markdown
> **Resolved:** The race condition described in this document has been eliminated
> by the SIGSTOP synchronisation model introduced in the terminal sub-package refactoring.
> The `_nav_pending` counter and `nav_start` barrier are no longer used.
> See [docs/terminal.md](terminal.md) for the current architecture.
> This document is retained for historical reference.
```

- [ ] **Step 3: Update `AGENTS.md` architecture section**

In the `AGENTS.md` file, update the architecture section to reflect the new terminal sub-package.
Replace references to `widgets/terminal.py` with the new package structure.
Add the terminal sub-package to the key layers:

```markdown
**Terminal:** `nova_navigator/terminal/` — embedded terminal emulator:
- `terminal.py` — `Terminal` widget (Textual, pyte rendering, draining state machine)
- `pty_backend.py` — `PtyBackend` ABC; `LocalPtyBackend` (PTY fork, fd management, SIGCONT)
- `shell_driver.py` — `ShellDriver` ABC; `ZshDriver`, `BashDriver`, `FallbackDriver` (shell hooks, quoting)
```

Update the UI widgets section to remove the terminal reference:

```markdown
**UI widgets:** `nova_navigator/widgets/`
- `directory_browser.py` — main dual-pane file browser widget
- `side_bar.py`, `footer.py`, `overlay_widget.py`
```

- [ ] **Step 4: Coding-guideline follow-up checklist**

- [ ] Conventions file read: `docs/coding_conventions.md`
- [ ] Documentation follows one-sentence-per-line rule
- [ ] Task-level verification: all referenced files exist and links resolve
- [ ] Any convention violations fixed before moving to next task

---

## Task 7: Final QA

**Files:** None (verification only)

- [ ] **Step 1: Run full QA suite**

```
uv run qa
```

Expected: zero failures (lint, type check, tests all pass).

- [ ] **Step 2: Verify old terminal.py is gone**

```
test ! -f src/nova_navigator/widgets/terminal.py && echo "GONE" || echo "STILL EXISTS"
test ! -f tests/widgets/test_terminal.py && echo "GONE" || echo "STILL EXISTS"
```

Expected: both print `GONE`.

- [ ] **Step 3: Verify new package is importable**

```
uv run python -c "
from nova_navigator.terminal import (
    Terminal, TerminalDisplay, TerminalPyteScreen,
    PtyBackend, LocalPtyBackend,
    ShellDriver, ZshDriver, BashDriver, FallbackDriver,
    detect_driver,
)
print('All imports OK')
"
```

Expected: `All imports OK`.

- [ ] **Step 4: Verify documentation files exist**

```
test -f docs/terminal.md && echo "OK" || echo "MISSING"
```

Expected: `OK`.

- [ ] **Step 5: Coding-guideline follow-up checklist**

- [ ] `uv run qa` output shows zero failures
- [ ] All new files follow naming conventions
- [ ] All deleted files are confirmed gone
- [ ] Documentation is complete and links resolve
