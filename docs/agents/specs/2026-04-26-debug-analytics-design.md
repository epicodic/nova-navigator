# Debug Analytics — Design Spec

Date: 2026-04-26

## Overview

Add an opt-in debug analytics mode to Nova Navigator.
When enabled, the mode configures file-based logging and installs an unhandled-exception handler that writes a coredump for post-mortem debugging.
A helper tool `tools/inspect_crash.py` makes the dump inspectable under the VS Code debugger.

## Goals

- Zero impact when the feature is disabled (default).
- File-based logging to `~/.cache/nova-navigator/debug-analytics/<pid>/app.log`.
- Existing `TextualHandler` logging continues in parallel; terminal logging is not suppressed.
- On any unhandled exception, write two files to the same directory:
  - `crash.txt` — always succeeds; human-readable traceback + per-frame local variables.
  - `crash.dump` — `dill`-serialised frame snapshots for interactive post-mortem; best-effort, skipping unserializable locals.
- Two exception paths are covered: `sys.excepthook` (exceptions escaping the process) and Textual's `_handle_exception` override (exceptions caught inside the Textual event loop — the more common path in a Textual app).
- A `tools/inspect_crash.py` script registered as the `inspect-crash` entry point in `pyproject.toml`.

## Out of Scope

- `debugpy` live-attach (already exists elsewhere in the codebase).
- Any UI to toggle analytics mode at runtime.

---

## Module Structure

### Files changed / removed

| File | Action |
|---|---|
| `src/nova_navigator/debug.py` | Deleted — content absorbed into `debug_analytics.py` |
| `src/nova_navigator/debug_analytics.py` | Created — all logic lives here |
| `src/nova_navigator/main.py` | Update import and call site |
| `tools/inspect_crash.py` | Created — post-mortem inspection helper |
| `pyproject.toml` | Add `dill` dependency; add `inspect-crash` script entry point |

### `src/nova_navigator/debug_analytics.py`

Public surface:

```python
DEBUG_ANALYTICS: bool  # toggle (see below)

def install() -> None: ...
def write_crash(exc: Exception) -> None: ...
```

`install()` is called from `NovaNavigator.on_mount()` (replacing the current `debug.install_debug_handler()` call).
It is a no-op when `DEBUG_ANALYTICS` is `False`.
`trace_handler` from the existing `debug.py` is also moved into this module unchanged.

---

## Toggle

```python
# src/nova_navigator/debug_analytics.py
import os

# Hand-editable constant. Also overridden by environment variable.
DEBUG_ANALYTICS: bool = os.environ.get("NN_DEBUG_ANALYTICS", "0") != "0"
```

To enable:
- Set `DEBUG_ANALYTICS = True` directly in the file, **or**
- Run with `NN_DEBUG_ANALYTICS=1 uv run nn`.

`main.py` calls `debug_analytics.install()` unconditionally; the flag check is inside `install()`.

---

## Logging (when enabled)

`install()` adds a `logging.FileHandler` to the **root logger** alongside the existing `TextualHandler`:

1. Compute `log_dir = Path.home() / ".cache/nova-navigator/debug-analytics" / str(os.getpid())`.
2. Create `log_dir` with `log_dir.mkdir(parents=True, exist_ok=True)`.
3. Create `FileHandler(log_dir / "app.log", encoding="utf-8")` at level `DEBUG`.
4. Formatter: `%(asctime)s %(levelname)s %(name)s: %(message)s`.
5. Add the handler to `logging.getLogger()` (root logger).

The `TextualHandler` added by `main.py`'s existing `logging.basicConfig` call is left untouched.
Both handlers are active simultaneously.

---

## Coredump on Unhandled Exception (when enabled)

`install()` computes `log_dir`, then defines `_crash_handler` as a closure capturing `log_dir`.
It installs `_crash_handler` in two places:

1. `sys.excepthook = _crash_handler` — catches exceptions that fully escape the process.
2. `NovaNavigator._handle_exception` is overridden in `main.py` to also call `debug_analytics.write_crash(error)` (see `main.py` Changes section below) — catches exceptions routed through Textual's internal event loop handler, which is the more common crash path in a Textual app.

Both paths call the same underlying crash-writing logic.
`write_crash(exc: Exception)` is a public function in `debug_analytics.py` that accepts a live exception and writes the two crash files using `exc.__traceback__`.

### `_crash_handler(exc_type, exc_value, exc_tb)` / `write_crash(exc)`

`_crash_handler` normalises its arguments and delegates to `write_crash`.
`write_crash` writes files to `log_dir` (the same directory as `app.log`).

#### Step 1 — `crash.txt` (always)

Content:
1. Full formatted traceback (`traceback.format_exception(exc_type, exc_value, exc_tb)`).
2. For each frame in the traceback chain (innermost last), a section:
   ```
   --- Frame: <filename>:<lineno> in <function> ---
   <varname> = <repr(value)>
   ...
   ```

Written with `open(log_dir / "crash.txt", "w", encoding="utf-8")`.
This step cannot raise — all errors are caught and written to `app.log`.

#### Step 2 — `crash.dump` (best-effort)

Data structure serialised:

```python
@dataclass
class FrameSnapshot:
    filename: str
    function: str
    lineno: int
    locals_: dict[str, Any]  # unserializable values replaced with repr string
```

Building snapshots:
- Walk `traceback.extract_tb(exc_tb)` to get frame summaries.
- Walk the actual `tb` chain in parallel to access `tb.tb_frame.f_locals`.
- For each local `(k, v)`: attempt `dill.dumps(v)` inside `try/except Exception`; on failure store `f"<unserializable: {repr(v)[:200]}>"` as the value.

The top-level serialised object is a dict:
```python
payload = {
    "exception_type": exc_type.__qualname__,
    "exception_value": repr(exc_value),
    "frames": snapshots,
}
with open(log_dir / "crash.dump", "wb") as f:
    dill.dump(payload, f)
```

If `dill.dump` itself raises, log the error to `app.log` and continue (`crash.txt` is already written).

---

## `main.py` Changes

Replace:
```python
from nova_navigator import archive, debug
```
With:
```python
from nova_navigator import archive, debug_analytics
```

Replace in `_handle_exception`:
```python
sys.settrace(debug.trace_handler)
```
With:
```python
sys.settrace(debug_analytics.trace_handler)
if debug_analytics.DEBUG_ANALYTICS:
    debug_analytics.write_crash(error)
```

Replace in `on_mount`:
```python
debug.install_debug_handler()
```
With:
```python
debug_analytics.install()
```

---

## `tools/inspect_crash.py`

A standalone script for post-mortem inspection under the VS Code debugger.

Usage:
```sh
uv run inspect-crash ~/.cache/nova-navigator/debug-analytics/<pid>/crash.dump
```

Behaviour:
1. Load `crash.dump` with `dill.load()`.
2. Print a summary: exception type/value, number of frames.
3. Iterate frames from outermost to innermost.
4. For each frame, print filename/function/lineno, then call `breakpoint()`.
   At the breakpoint, `locals()` contains all restored variables from that frame.
   VS Code Variables panel shows them; Watch and Debug Console work normally.

The script accepts the dump path as a CLI argument (`sys.argv[1]`).

---

## `pyproject.toml` Changes

Add `dill` to `[project.dependencies]`.

Add to `[project.scripts]`:
```toml
inspect-crash = "tools.inspect_crash:main"
```

---

## Testing

No automated tests for the exception handler (it requires triggering an unhandled exception and inspecting filesystem output — not worth the complexity).
Manual test procedure:
1. Set `NN_DEBUG_ANALYTICS=1 uv run nn`.
2. Trigger an unhandled exception.
3. Verify `~/.cache/nova-navigator/debug-analytics/<pid>/app.log`, `crash.txt`, and `crash.dump` exist.
4. Run `uv run inspect-crash <path>/crash.dump` under the VS Code debugger and verify locals are visible.
