"""The suite restores the trace function a test leaves behind (the app installs one after any exception)."""

from __future__ import annotations

import sys
from types import FrameType

from tests._utils.trace import restored_trace_function


def _leaked(_frame: FrameType, _event: str, _arg: object) -> None:
    return None


def test_a_trace_function_installed_inside_the_block_is_removed_after_it() -> None:
    before = sys.gettrace()
    with restored_trace_function():
        sys.settrace(_leaked)
    assert sys.gettrace() is before
