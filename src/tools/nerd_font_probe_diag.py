"""Diagnostic script for NerdFont cursor-probe detection.

Run directly:  uv run python src/tools/nerd_font_probe_diag.py
"""

from __future__ import annotations

import os
import re
import select
import sys
import termios
import time
import tty

_PROBE_GLYPH = "\ue001"
_CPR_REQUEST = "\x1b[6n"
_MOVE_COL1 = "\x1b[1G"
_SAVE_CURSOR = "\x1b7"
_RESTORE_CURSOR = "\x1b8"


_DOUBLE_WIDTH = 2


def _read_cpr_raw(fd: int, timeout: float) -> tuple[int, int] | None:
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


def main() -> None:
    print(f"sys.stdout.isatty() = {sys.stdout.isatty()}")
    print(f"sys.stdin.isatty()  = {sys.stdin.isatty()}")

    if not sys.stdout.isatty() or not sys.stdin.isatty():
        print("Not a TTY — probe skipped.")
        return

    fd = sys.stdin.fileno()
    old = termios.tcgetattr(fd)
    try:
        tty.setraw(fd, termios.TCSANOW)

        # Initial position
        sys.stdout.write(_SAVE_CURSOR + _MOVE_COL1 + _CPR_REQUEST)
        sys.stdout.flush()
        initial = _read_cpr_raw(fd, 0.5)

        # Print probe glyph and re-query
        sys.stdout.write(_PROBE_GLYPH + _CPR_REQUEST)
        sys.stdout.flush()
        after = _read_cpr_raw(fd, 0.5)

        # Restore
        sys.stdout.write(_RESTORE_CURSOR)
        sys.stdout.flush()

    finally:
        termios.tcsetattr(fd, termios.TCSADRAIN, old)

    print(f"\r\ninitial CPR : {initial}")
    print(f"after CPR   : {after}")
    if initial and after:
        diff = after[1] - initial[1]
        if diff == _DOUBLE_WIDTH:
            label = "double-width -> NerdFont detected"
        else:
            label = "single-width -> NerdFont NOT detected"
        print(f"col advance : {diff}  ({label})")
    else:
        print("CPR read timed out (terminal didn't respond)")


if __name__ == "__main__":
    main()
