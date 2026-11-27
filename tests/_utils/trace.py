"""Restore of the interpreter trace function around a test."""

from __future__ import annotations

import contextlib
import sys
from collections.abc import Generator


@contextlib.contextmanager
def restored_trace_function() -> Generator[None, None, None]:
    """Put the trace function of this thread back to what it was on entry.

    `NovaNavigator` calls `sys.settrace` after any app exception and never undoes it, which slows every later test.
    """
    previous = sys.gettrace()
    try:
        yield
    finally:
        sys.settrace(previous)
