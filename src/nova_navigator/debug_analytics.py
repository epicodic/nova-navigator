from __future__ import annotations

import datetime
import logging
import os
import pdb  # noqa: T100
import subprocess
import sys
import types
from collections.abc import Callable
from pathlib import Path
from typing import Any

from rich.console import Console
from rich.rule import Rule
from rich.text import Text

_logger = logging.getLogger(__name__)

# ---------------------------------------------------------------------------
# Toggle
# Hand-editable constants. Also overridden by environment variables.
# ---------------------------------------------------------------------------
DEBUG_ANALYTICS: bool = os.environ.get("NN_DEBUG_ANALYTICS", "0") != "0"

# When True, drop into a live pdb session on crash (locals fully inspectable).
# Only meaningful alongside DEBUG_ANALYTICS = True.
LIVE_PDB: bool = os.environ.get("NN_LIVE_PDB", "0") != "0"

# Module-level storage so _crash_handler closure and write_crash share log_dir.
_log_dir: Path | None = None


# ---------------------------------------------------------------------------
# Moved from debug.py — trace handler
# ---------------------------------------------------------------------------

_debugpy_armed = False


def trace_handler(_frame: types.FrameType, event: str, _arg: Any) -> Callable[[types.FrameType, str, Any], Any] | None:
    global _debugpy_armed  # noqa: PLW0603
    if event == "exception" and not _debugpy_armed:
        _debugpy_armed = True
    return trace_handler


# ---------------------------------------------------------------------------
# Crash writing
# ---------------------------------------------------------------------------


_SOURCE_CONTEXT = 5  # lines before and after the crash line


def _render_frame_source(console: Console, frame: types.FrameType, lineno: int) -> None:
    try:
        src_lines, start = __import__("inspect").getsourcelines(frame)
        for j, src_line in enumerate(src_lines):
            line_no = start + j
            if abs(line_no - lineno) > _SOURCE_CONTEXT:
                continue
            prefix = ">>>" if line_no == lineno else "   "
            style = "bold white on dark_red" if line_no == lineno else "dim"
            console.print(f"    [dim]{line_no:4d}[/] [{style}]{prefix} {src_line.rstrip()}[/]")
    except Exception:  # noqa: BLE001, S110
        pass


def _render_frame_locals(console: Console, frame: types.FrameType) -> None:
    local_items = list(frame.f_locals.items())
    if not local_items:
        return
    console.print()
    for k, v in local_items:
        try:
            r = repr(v)
        except Exception:  # noqa: BLE001
            r = "<repr failed>"
        key_text = Text()
        key_text.append(f"    {k}", style="bold magenta")
        key_text.append(" = ", style="dim")
        key_text.append(r, style="white")
        console.print(key_text)


def _render_crash(
    console: Console,
    exc_type: type[BaseException],
    exc_value: BaseException,
    exc_tb: types.TracebackType | None,
) -> None:
    """Render a GDB-style crash report to the given rich Console."""
    ts = datetime.datetime.now().strftime("%Y-%m-%d %H:%M:%S")
    exc_label = f"{exc_type.__qualname__}: {exc_value}"

    console.print(Rule(f"[bold red]CRASH[/] [dim]{ts}[/]", style="red"))
    console.print()

    exc_text = Text()
    exc_text.append("Exception: ", style="bold red")
    exc_text.append(exc_label, style="red")
    console.print(exc_text)
    console.print()

    # Collect frames innermost-first (GDB #0 = crash frame)
    frames: list[types.TracebackType] = []
    tb = exc_tb
    while tb is not None:
        frames.append(tb)
        tb = tb.tb_next
    frames.reverse()

    console.print(Rule("[bold yellow]Stack frames[/] [dim](innermost first, GDB style)[/]", style="yellow"))
    console.print()

    for i, frame_tb in enumerate(frames):
        frame = frame_tb.tb_frame
        filename = frame.f_code.co_filename
        lineno = frame_tb.tb_lineno
        funcname = frame.f_code.co_name

        header = Text()
        header.append(f"#{i}", style="bold cyan")
        header.append("  ")
        header.append(funcname, style="bold white")
        header.append(" () at ", style="dim")
        header.append(filename, style="green")
        header.append(":", style="dim")
        header.append(str(lineno), style="cyan")
        console.print(header)

        _render_frame_source(console, frame, lineno)
        _render_frame_locals(console, frame)
        console.print()

    console.print(Rule("[bold red]End of crash report[/]", style="red"))


def _write_crash_txt(
    log_dir: Path,
    exc_type: type[BaseException],
    exc_value: BaseException,
    exc_tb: types.TracebackType | None,
) -> None:
    try:
        with (log_dir / "crash.txt").open("w", encoding="utf-8") as f:
            console = Console(file=f, force_terminal=True, width=120, highlight=False)
            _render_crash(console, exc_type, exc_value, exc_tb)
    except Exception:
        _logger.exception("Failed to write crash.txt")


def write_crash(exc: BaseException) -> None:
    """Write crash.txt for a live exception.

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
    if LIVE_PDB:
        _live_pdb(exc_type, exc, exc_tb)


def _restore_terminal() -> None:
    """Best-effort restore of terminal state after Textual's TUI mode."""
    try:
        subprocess.run(["stty", "sane"], check=False)
        # Emit reset sequence: exit alternate screen, show cursor, reset attributes
        sys.stdout.write("\033[?1049l\033[?25h\033[0m")
        sys.stdout.flush()
    except Exception:  # noqa: BLE001, S110
        pass


def _live_pdb(
    exc_type: type[BaseException],
    exc_value: BaseException,
    exc_tb: types.TracebackType | None,
) -> None:
    """Restore the terminal and drop into a live pdb post-mortem session."""
    _restore_terminal()
    console = Console(stderr=True, highlight=False)
    _render_crash(console, exc_type, exc_value, exc_tb)
    print("\n--- Live pdb session (locals are fully inspectable) ---")
    print("Commands: u/d = navigate frames, p <var> = inspect, l = source, q = quit\n")
    pdb.post_mortem(exc_tb)


def _crash_handler(
    exc_type: type[BaseException],
    exc_value: BaseException,
    exc_tb: types.TracebackType | None,
) -> None:
    if _log_dir is None:
        return
    _write_crash_txt(_log_dir, exc_type, exc_value, exc_tb)
    if LIVE_PDB:
        _live_pdb(exc_type, exc_value, exc_tb)


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
    handler.setFormatter(logging.Formatter("%(asctime)s %(levelname)s %(name)s: %(message)s"))
    logging.getLogger().addHandler(handler)
    logging.getLogger().setLevel(logging.DEBUG)

    # sys.excepthook for exceptions that escape the process
    sys.excepthook = _crash_handler

    _logger.info("Debug analytics enabled. Log dir: %s", _log_dir)
