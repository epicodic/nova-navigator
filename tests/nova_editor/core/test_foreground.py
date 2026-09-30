"""Tests for the gate that lets background scans give way to the UI thread."""

from __future__ import annotations

import time

from nova_editor.core.foreground import Foreground
from nova_editor.core.line_index import LineIndex
from nova_editor.core.long_line_index import LongLineIndex

from .helpers import MemorySource


def test_a_scan_is_not_paused_before_the_first_touch() -> None:
    assert Foreground(hold=10, pause=0.5).pause_seconds() == 0.0


def test_a_touch_pauses_the_scans_until_the_hold_time_is_over() -> None:
    gate = Foreground(hold=0.05, pause=0.25)
    gate.touch()
    assert gate.pause_seconds() == 0.25
    time.sleep(0.08)
    assert gate.pause_seconds() == 0.0


class CountingForeground(Foreground):
    """Counts the questions of the scan, always answers `pause`."""

    def __init__(self, pause: float) -> None:
        super().__init__(hold=1, pause=pause)
        self.asked = 0

    def pause_seconds(self) -> float:
        self.asked += 1
        return 0.0


def test_the_long_line_scan_asks_the_gate_once_per_checkpoint_piece() -> None:
    data = b"abc " * 40
    gate = CountingForeground(0.0)
    index = LongLineIndex(MemorySource(data), 0, len(data), checkpoint_chars=16, scan_block=1 << 20, foreground=gate)
    assert index.join(10)
    assert gate.asked == len(data) // 16


def test_the_long_line_scan_sleeps_while_the_ui_is_busy() -> None:
    data = b"abc " * 40
    gate = Foreground(hold=60, pause=0.02)
    gate.touch()
    started = time.perf_counter()
    index = LongLineIndex(MemorySource(data), 0, len(data), checkpoint_chars=16, scan_block=1 << 20, foreground=gate)
    assert index.join(10)
    assert time.perf_counter() - started >= 0.02 * (len(data) // 16) * 0.9


def test_the_line_scan_sleeps_between_blocks_while_the_ui_is_busy() -> None:
    data = b"line\n" * 40
    gate = Foreground(hold=60, pause=0.02)
    gate.touch()
    started = time.perf_counter()
    index = LineIndex(MemorySource(data), scan_block=20, foreground=gate)
    index.start()
    assert index.join(10)
    assert time.perf_counter() - started >= 0.02 * (len(data) // 20) * 0.9
