# Debug Analytics Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use skills:subagent-driven-development (recommended) or skills:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Add an opt-in debug analytics mode that enables file-based logging and writes a rich coredump (plain-text + dill-serialised frame snapshots) on unhandled exceptions for post-mortem debugging.

**Architecture:** A new `debug_analytics.py` module holds the toggle flag, log setup, and crash-writing logic. `main.py` calls `debug_analytics.install()` on startup and routes Textual's `_handle_exception` to `debug_analytics.write_crash()`. A `tools/inspect_crash.py` script loads a `.dump` file under the VS Code debugger.

**Tech Stack:** Python 3.12, pytest, dill, Textual

**Coding Conventions:** `docs/coding_conventions.md` — read before implementing

**Spec:** `docs/agents/specs/2026-04-26-debug-analytics-design.md`

---

## File Map

| Action | Path | Responsibility |
|--------|------|----------------|
| Create | `src/nova_navigator/debug_analytics.py` | Toggle flag, logging setup, crash writing |
| Modify | `src/nova_navigator/main.py` lines 22, 586–593 | Swap `debug` → `debug_analytics`, add `write_crash` call |
| Delete | `src/nova_navigator/debug.py` | Absorbed into `debug_analytics.py` |
| Create | `src/tools/inspect_crash.py` | Post-mortem inspection helper |
| Modify | `pyproject.toml` lines 7–15, 18–19 | Add `dill` dep, add `inspect-crash` script entry |

---

## Task 1: Add `dill` dependency and `inspect-crash` script entry

**Files:**
- Modify: `pyproject.toml`

- [ ] **Step 1: Add `dill` to project dependencies**

In `pyproject.toml`, add `dill` to the `[project]` `dependencies` list:

```toml
dependencies = [
    "pyte>=0.8.2",
    "textual>=6.5.0",
    "tomlkit>=0.13.3",
    "watchdog>=6.0.0",
    "paramiko>=4.0.0",
    "pygments>=2.19.2",
    "azure-storage-blob>=12.27.1",
    "azure-identity>=1.25.1",
    "rich>=14.2.0",
    "debugpy>=1.8.20",
    "dill>=0.3.9",
]
```

- [ ] **Step 2: Add `inspect-crash` script entry**

In `pyproject.toml`, update `[project.scripts]`:

```toml
[project.scripts]
nn = "nova_navigator.main:main"
qa = "tools.qa:main"
inspect-crash = "tools.inspect_crash:main"
```

- [ ] **Step 3: Sync dependencies**

```sh
uv sync
```

Expected: resolves and installs `dill` with no errors.

- [ ] **Step 4: Coding-guideline follow-up checklist**

- [ ] Conventions file read: `docs/coding_conventions.md`
- [ ] No naming convention violations introduced
- [ ] `uv sync` exited with code 0

---

## Task 2: Create `src/nova_navigator/debug_analytics.py`

**Files:**
- Create: `src/nova_navigator/debug_analytics.py`

This module absorbs everything from `debug.py` and adds analytics logic.

- [ ] **Step 1: Create the file**

```python
# src/nova_navigator/debug_analytics.py
from __future__ import annotations

import logging
import os
import sys
import traceback
import types
from collections.abc import Callable
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

import dill

_logger = logging.getLogger(__name__)

# ---------------------------------------------------------------------------
# Toggle
# Hand-editable constant. Also overridden by the NN_DEBUG_ANALYTICS env var.
# ---------------------------------------------------------------------------
DEBUG_ANALYTICS: bool = os.environ.get("NN_DEBUG_ANALYTICS", "0") != "0"

# Module-level storage so _crash_handler closure and write_crash share log_dir.
_log_dir: Path | None = None


# ---------------------------------------------------------------------------
# Moved from debug.py — trace handler
# ---------------------------------------------------------------------------

_debugpy_armed = False


def trace_handler(
    _frame: types.FrameType, event: str, _arg: Any
) -> Callable[[types.FrameType, str, Any], Any] | None:
    global _debugpy_armed  # noqa: PLW0603
    if event == "exception" and not _debugpy_armed:
        _debugpy_armed = True
    return trace_handler


# ---------------------------------------------------------------------------
# Crash writing
# ---------------------------------------------------------------------------


@dataclass
class FrameSnapshot:
    filename: str
    function: str
    lineno: int
    locals_: dict[str, Any] = field(default_factory=dict)


def _build_snapshots(exc_tb: types.TracebackType | None) -> list[FrameSnapshot]:
    snapshots: list[FrameSnapshot] = []
    tb = exc_tb
    while tb is not None:
        frame = tb.tb_frame
        snapshot = FrameSnapshot(
            filename=frame.f_code.co_filename,
            function=frame.f_code.co_name,
            lineno=tb.tb_lineno,
        )
        for k, v in frame.f_locals.items():
            try:
                dill.dumps(v)
                snapshot.locals_[k] = v
            except Exception:  # noqa: BLE001
                snapshot.locals_[k] = f"<unserializable: {repr(v)[:200]}>"
        snapshots.append(snapshot)
        tb = tb.tb_next
    return snapshots


def _write_crash_txt(
    log_dir: Path,
    exc_type: type[BaseException],
    exc_value: BaseException,
    exc_tb: types.TracebackType | None,
) -> None:
    try:
        lines: list[str] = []
        lines += traceback.format_exception(exc_type, exc_value, exc_tb)
        lines.append("\n")
        tb = exc_tb
        while tb is not None:
            frame = tb.tb_frame
            lines.append(
                f"--- Frame: {frame.f_code.co_filename}:{tb.tb_lineno}"
                f" in {frame.f_code.co_name} ---\n"
            )
            for k, v in frame.f_locals.items():
                try:
                    r = repr(v)
                except Exception:  # noqa: BLE001
                    r = "<repr failed>"
                lines.append(f"    {k} = {r}\n")
            tb = tb.tb_next
        (log_dir / "crash.txt").write_text("".join(lines), encoding="utf-8")
    except Exception:  # noqa: BLE001
        _logger.exception("Failed to write crash.txt")


def _write_crash_dump(
    log_dir: Path,
    exc_type: type[BaseException],
    exc_value: BaseException,
    exc_tb: types.TracebackType | None,
) -> None:
    try:
        snapshots = _build_snapshots(exc_tb)
        payload: dict[str, Any] = {
            "exception_type": exc_type.__qualname__,
            "exception_value": repr(exc_value),
            "frames": snapshots,
        }
        with (log_dir / "crash.dump").open("wb") as f:
            dill.dump(payload, f)
    except Exception:  # noqa: BLE001
        _logger.exception("Failed to write crash.dump")


def write_crash(exc: BaseException) -> None:
    """Write crash.txt and crash.dump for a live exception.

    Safe to call even when DEBUG_ANALYTICS is False (no-op).
    """
    if not DEBUG_ANALYTICS:
        return
    if _log_dir is None:
        _logger.warning("write_crash called before install(); crash files not written")
        return
    exc_type = type(exc)
    exc_tb = exc.__traceback__
    _write_crash_txt(_log_dir, exc_type, exc, exc_tb)
    _write_crash_dump(_log_dir, exc_type, exc, exc_tb)


def _crash_handler(
    exc_type: type[BaseException],
    exc_value: BaseException,
    exc_tb: types.TracebackType | None,
) -> None:
    if _log_dir is None:
        return
    _write_crash_txt(_log_dir, exc_type, exc_value, exc_tb)
    _write_crash_dump(_log_dir, exc_type, exc_value, exc_tb)


# ---------------------------------------------------------------------------
# Install
# ---------------------------------------------------------------------------


def install() -> None:
    """Install debug analytics. No-op when DEBUG_ANALYTICS is False."""
    if not DEBUG_ANALYTICS:
        return

    global _log_dir  # noqa: PLW0603
    _log_dir = Path.home() / ".cache" / "nova-navigator" / "debug-analytics" / str(os.getpid())
    _log_dir.mkdir(parents=True, exist_ok=True)

    # File logging alongside TextualHandler
    handler = logging.FileHandler(_log_dir / "app.log", encoding="utf-8")
    handler.setLevel(logging.DEBUG)
    handler.setFormatter(
        logging.Formatter("%(asctime)s %(levelname)s %(name)s: %(message)s")
    )
    logging.getLogger().addHandler(handler)
    logging.getLogger().setLevel(logging.DEBUG)

    # sys.excepthook for exceptions that escape the process
    sys.excepthook = _crash_handler

    _logger.info("Debug analytics enabled. Log dir: %s", _log_dir)
```

- [ ] **Step 2: Verify no import errors**

```sh
uv run python -c "from nova_navigator import debug_analytics; print('OK')"
```

Expected: `OK`

- [ ] **Step 3: Coding-guideline follow-up checklist**

- [ ] Conventions file read: `docs/coding_conventions.md`
- [ ] All functions and methods have full type annotations
- [ ] `X | None` used (not `Optional[X]`)
- [ ] Builtin collection types used (`list`, `dict`)
- [ ] `snake_case` for functions/variables, `UpperCamelCase` for types
- [ ] `_` prefix for private names

---

## Task 3: Update `main.py` to use `debug_analytics`

**Files:**
- Modify: `src/nova_navigator/main.py` lines 22, 584–593

- [ ] **Step 1: Update the import on line 22**

Replace:
```python
from nova_navigator import archive, debug
```
With:
```python
from nova_navigator import archive, debug_analytics
```

- [ ] **Step 2: Update `_handle_exception` (lines 584–589)**

Replace the entire method body:
```python
    def _handle_exception(self, error: Exception) -> None:
        # debug.exception_handler(type(error), error, error.__traceback__)
        sys.settrace(debug.trace_handler)
        raise error
        # TODO show a user-friendly error dialog here
        #       ideally with an option to open the debugger if debugpy is installed
```
With:
```python
    def _handle_exception(self, error: Exception) -> None:
        sys.settrace(debug_analytics.trace_handler)
        debug_analytics.write_crash(error)
        raise error
        # TODO show a user-friendly error dialog here
        #       ideally with an option to open the debugger if debugpy is installed
```

- [ ] **Step 3: Update `on_mount` (line 593)**

Replace:
```python
        debug.install_debug_handler()
```
With:
```python
        debug_analytics.install()
```

- [ ] **Step 4: Verify the app imports cleanly**

```sh
uv run python -c "import nova_navigator.main; print('OK')"
```

Expected: `OK`

- [ ] **Step 5: Coding-guideline follow-up checklist**

- [ ] No references to `debug` remain in `main.py`
- [ ] Full type annotations preserved on modified methods

---

## Task 4: Delete `debug.py`

**Files:**
- Delete: `src/nova_navigator/debug.py`

- [ ] **Step 1: Confirm no remaining imports of `debug`**

```sh
grep -r "nova_navigator.debug\b\|from nova_navigator import.*\bdebug\b" src/ tests/
```

Expected: no matches.

- [ ] **Step 2: Delete the file**

```sh
rm src/nova_navigator/debug.py
```

- [ ] **Step 3: Run QA to confirm nothing broken**

```sh
uv run qa
```

Expected: zero failures.

---

## Task 5: Create `tools/inspect_crash.py`

**Files:**
- Create: `tools/inspect_crash.py`

- [ ] **Step 1: Create the file**

```python
# tools/inspect_crash.py
"""Post-mortem crash inspector for Nova Navigator debug analytics dumps.

Usage:
    uv run inspect-crash ~/.cache/nova-navigator/debug-analytics/<pid>/crash.dump

Run under the VS Code debugger (F5 with a suitable launch.json) to inspect
restored locals in the Variables panel frame-by-frame.
"""
from __future__ import annotations

import sys
from pathlib import Path
from typing import Any

import dill


def main() -> None:
    if len(sys.argv) < 2:  # noqa: PLR2004
        print("Usage: inspect-crash <path/to/crash.dump>", file=sys.stderr)
        sys.exit(1)

    dump_path = Path(sys.argv[1])
    if not dump_path.exists():
        print(f"File not found: {dump_path}", file=sys.stderr)
        sys.exit(1)

    with dump_path.open("rb") as f:
        payload: dict[str, Any] = dill.load(f)  # noqa: S301

    exc_type = payload.get("exception_type", "<unknown>")
    exc_value = payload.get("exception_value", "<unknown>")
    frames: list[Any] = payload.get("frames", [])

    print(f"\nCrash report: {dump_path}")
    print(f"Exception: {exc_type}: {exc_value}")
    print(f"Frames: {len(frames)}\n")

    for i, frame in enumerate(frames):
        print(f"[{i}] {frame.filename}:{frame.lineno} in {frame.function}()")
        print(f"     Locals: {list(frame.locals_.keys())}")

    print("\nEntering frame-by-frame inspection (set breakpoints in VS Code)...\n")

    for i, frame in enumerate(frames):
        print(f"\n--- Frame {i}: {frame.filename}:{frame.lineno} in {frame.function}() ---")
        # Inject restored locals into current scope so VS Code Variables panel shows them.
        locals().update(frame.locals_)  # type: ignore[arg-type]
        breakpoint()  # noqa: T100


if __name__ == "__main__":
    main()
```

- [ ] **Step 2: Verify the script is importable**

```sh
uv run python -c "from tools.inspect_crash import main; print('OK')"
```

Expected: `OK`

- [ ] **Step 3: Coding-guideline follow-up checklist**

- [ ] Full type annotations on `main()`
- [ ] `snake_case` naming

---

## Task 6: Run full QA

- [ ] **Step 1: Run all QA checks**

```sh
uv run qa
```

Expected: zero lint, type-check, and test failures.

- [ ] **Step 2: Manual smoke test (analytics disabled — default)**

```sh
uv run nn
```

Expected: app starts normally; no files created under `~/.cache/nova-navigator/debug-analytics/`.

- [ ] **Step 3: Manual smoke test (analytics enabled)**

```sh
NN_DEBUG_ANALYTICS=1 uv run nn
```

Expected:
- Directory `~/.cache/nova-navigator/debug-analytics/<pid>/` created.
- `app.log` present and receiving log lines.
- App behaves normally otherwise.

- [ ] **Step 4: Manual coredump test**

Temporarily add a crash to `on_mount` (revert after test):
```python
raise RuntimeError("test crash")
```

Run:
```sh
NN_DEBUG_ANALYTICS=1 uv run nn
```

Expected:
- `crash.txt` present with traceback and locals.
- `crash.dump` present.

```sh
uv run inspect-crash ~/.cache/nova-navigator/debug-analytics/<pid>/crash.dump
```

Expected: prints summary, then hits `breakpoint()` at each frame.
