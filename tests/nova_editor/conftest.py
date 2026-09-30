"""Fixtures shared by the editor tests."""

from __future__ import annotations

import threading
import time
from collections.abc import Iterator

import pytest

_SCAN_THREADS = ("line-index-scan", "long-line-scan")
_JOIN_SECONDS = 10.0


def _alive() -> list[threading.Thread]:
    return [t for t in threading.enumerate() if t.name in _SCAN_THREADS and t.is_alive()]


@pytest.fixture(autouse=True)
def no_scan_thread_leaks() -> Iterator[None]:
    """Fail a test that leaves an index scan thread running after it finished (after a bounded wait)."""
    yield
    deadline = time.monotonic() + _JOIN_SECONDS
    for thread in _alive():
        thread.join(max(0.0, deadline - time.monotonic()))
    leaked = _alive()
    assert not leaked, f"scan threads still alive: {[t.name for t in leaked]}"
