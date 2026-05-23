"""NerdFont detection via terminal cursor-position probing and fontconfig.

Calls :func:`detect_nerd_font` once at startup, before Textual takes over
the terminal, to decide whether to use NerdFont or Unicode icon glyphs.

Two-stage detection:

1. **Cursor probe** — writes a NerdFont PUA glyph (U+E001) to stdout and
   compares cursor positions before and after via ANSI CPR (ESC[6n).
   Returns True immediately when the cursor advances 2 columns.
   This works in terminals that explicitly mark PUA chars as double-width
   (e.g. kitty, WezTerm).

2. **Fontconfig fallback** — many terminals (VTE-based, xterm) render
   NerdFont glyphs visually double-wide but keep the cursor at column+1
   because ``wcwidth()`` classifies PUA characters as single-width.
   When the cursor probe returns single-wide, ``fc-list :charset=e001``
   is used to check whether any installed font covers the NerdFont PUA
   range.  If yes, NerdFont mode is enabled.
"""

from __future__ import annotations

import os
import re
import select
import shutil
import subprocess
import sys
import termios
import time
import tty
from collections.abc import Callable

# U+E001 is in the NerdFonts custom PUA range (U+E000-U+F8FF).
# Standard Unicode fonts leave it blank / render it single-width.
_PROBE_GLYPH = "\ue001"

# ANSI Cursor Position Report request — terminal replies with ESC[row;colR.
_CPR_REQUEST = "\x1b[6n"

# Move cursor to column 1 of the current row.
_MOVE_COL1 = "\x1b[1G"

# DEC save / restore cursor (more widely supported than ANSI SCP/RCP).
_SAVE_CURSOR = "\x1b7"
_RESTORE_CURSOR = "\x1b8"

# Column advance expected from a double-width glyph.
_DOUBLE_WIDTH = 2


def detect_nerd_font(timeout: float = 0.1) -> bool:
    """Return True if the terminal's active font renders NerdFont glyphs.

    Falls back to False when stdin/stdout are not connected to a TTY.

    Detection is two-stage:

    * Cursor probe — accurate on terminals that report PUA chars as
      double-width (kitty, WezTerm).
    * Fontconfig fallback — used when the cursor probe returns single-wide,
      which happens on VTE-based terminals (GNOME Terminal, XFCE Terminal,
      xterm) even when a NerdFont is configured.  ``fc-list :charset=e001``
      is queried; if any installed font covers the NerdFont PUA range,
      NerdFont mode is enabled.
    """
    if not sys.stdout.isatty() or not sys.stdin.isatty():
        return False

    fd = sys.stdin.fileno()
    old = termios.tcgetattr(fd)
    try:
        tty.setraw(fd, termios.TCSANOW)
        if _probe_nerd_font(
            write=sys.stdout.write,
            flush=sys.stdout.flush,
            read_cpr=_make_fd_cpr_reader(fd, timeout),
        ):
            return True
    except OSError:
        return False
    finally:
        termios.tcsetattr(fd, termios.TCSADRAIN, old)

    # Cursor probe returned single-wide — terminal's wcwidth() doesn't mark
    # PUA as double-width.  Ask fontconfig whether a NerdFont is installed.
    return _fc_list_has_nerd_font_glyph()


def _fc_list_has_nerd_font_glyph() -> bool:
    """Return True if fontconfig reports any font covering U+E001 (NerdFont PUA)."""
    if shutil.which("fc-list") is None:
        return False
    try:
        result = subprocess.run(
            ["fc-list", ":charset=e001"],
            capture_output=True,
            text=True,
            timeout=2.0,
            check=False,
        )
        return bool(result.stdout.strip())
    except (subprocess.TimeoutExpired, OSError):
        return False


def _probe_nerd_font(
    *,
    write: Callable[[str], object],
    flush: Callable[[], None],
    read_cpr: Callable[[], tuple[int, int] | None],
) -> bool:
    """Perform the cursor probe and return True if the glyph advances 2 columns.

    Separated from :func:`detect_nerd_font` so tests can inject mock I/O.
    """
    # Move to a known column and record the starting position.
    write(_SAVE_CURSOR + _MOVE_COL1 + _CPR_REQUEST)
    flush()
    initial = read_cpr()

    # Print the probe glyph then immediately request the new cursor position.
    write(_PROBE_GLYPH + _CPR_REQUEST)
    flush()
    after = read_cpr()

    # Restore cursor regardless of what was detected.
    write(_RESTORE_CURSOR)
    flush()

    if initial is None or after is None:
        return False
    return (after[1] - initial[1]) == _DOUBLE_WIDTH


def _make_fd_cpr_reader(fd: int, timeout: float) -> Callable[[], tuple[int, int] | None]:
    """Return a closure that reads one CPR response (ESC[row;colR) from *fd*."""

    def _read() -> tuple[int, int] | None:
        buf = ""
        deadline = time.monotonic() + timeout
        while True:
            remaining = deadline - time.monotonic()
            if remaining <= 0:
                return None
            ready, _, _ = select.select([fd], [], [], remaining)
            if not ready:
                return None
            chunk = os.read(fd, 32).decode("ascii", errors="ignore")
            buf += chunk
            m = re.search(r"\x1b\[(\d+);(\d+)R", buf)
            if m:
                return int(m.group(1)), int(m.group(2))

    return _read
