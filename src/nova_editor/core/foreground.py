"""Let background scans give way to the user interface.

A scan thread and the UI thread share the GIL. A thread that waits for it gets it only after the running thread released it or
after the 5 ms switch interval, so a UI step that does a few blocking calls (reads, the event loop's select) while a scan is
running is delayed by several intervals. The UI calls `touch()` whenever it paints; a scan asks `pause_seconds()` between two
pieces of work and sleeps that long while the UI is busy, which hands the GIL to the UI without a wait.
"""

from __future__ import annotations

import time

DEFAULT_HOLD_SECONDS = 0.05
"""How long the UI counts as busy after a `touch()`.

Longer than a key repeat interval (about 30 ms) so that holding a key keeps the scans paused, and short enough that the scans
resume about a frame after the user stops.
"""

DEFAULT_PAUSE_SECONDS = 0.001
"""How long a scan sleeps per piece of work while the UI is busy.

One millisecond is enough for the UI thread to take the GIL without the 5 ms switch-interval wait, and costs a scan only a
small fraction of its throughput.
"""


class Foreground:
    """Record the last time the UI was busy; the scans read it through `pause_seconds()` (thread-safe: one float store and load)."""

    def __init__(self, hold: float = DEFAULT_HOLD_SECONDS, pause: float = DEFAULT_PAUSE_SECONDS) -> None:
        """Create the gate.

        Args:
            hold: Seconds the UI counts as busy after a `touch()`.
            pause: Seconds a scan sleeps per piece of work while the UI is busy.
        """
        self._hold = hold
        self._pause = pause
        self._last = float("-inf")

    def touch(self) -> None:
        """Record that the UI is busy now."""
        self._last = time.monotonic()

    def pause_seconds(self) -> float:
        """Return how long a scan should sleep before its next piece of work: 0 when the UI has been idle for `hold` seconds."""
        return self._pause if time.monotonic() - self._last < self._hold else 0.0
