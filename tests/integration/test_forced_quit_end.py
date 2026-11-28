"""``NovaNavigator._end_process``, the last step of a forced quit, with ``os._exit`` replaced.

The effects that need a real process are in ``test_hung_write_back_exit.py``.
"""

from __future__ import annotations

import io
import os
import select
import sys
import threading
import time
from typing import cast

import pytest
from textual.driver import Driver

from nova_navigator import debug_analytics
from nova_navigator.nova_navigator import NovaNavigator

_REPORT = "the report"
_GUARD = 10.0  # a hang limit, not a time budget


@pytest.fixture
def exits(monkeypatch: pytest.MonkeyPatch) -> list[int]:
    codes: list[int] = []
    monkeypatch.setattr("os._exit", codes.append)
    return codes


@pytest.fixture
def printed(monkeypatch: pytest.MonkeyPatch) -> io.StringIO:
    stream = io.StringIO()
    monkeypatch.setattr(sys, "__stderr__", stream)
    return stream


def _quitting_app() -> NovaNavigator:
    app = NovaNavigator()
    app._forced_quit_report = _REPORT
    app._teardown_started = True
    return app


def test_a_second_thread_cannot_end_the_process_while_the_report_is_being_printed(monkeypatch: pytest.MonkeyPatch, exits: list[int], printed: io.StringIO) -> None:
    app = _quitting_app()
    busy = threading.Event()
    release = threading.Event()

    def _slow_cleanup() -> None:
        busy.set()
        release.wait(_GUARD)

    monkeypatch.setattr(debug_analytics, "cleanup", _slow_cleanup)
    first = threading.Thread(target=app._end_process, daemon=True)
    second = threading.Thread(target=app._end_process, daemon=True)
    # Both threads must be over before monkeypatch gives back the real os._exit, also when an assertion fails:
    # a thread still in _end_process would then end pytest itself, with the status of a success.
    try:
        first.start()
        assert busy.wait(_GUARD)

        second.start()
        second.join(0.3)

        assert second.is_alive()
        assert exits == []
        release.set()
        first.join(_GUARD)
        assert exits == [0]
        assert printed.getvalue().count(_REPORT) == 1
    finally:
        release.set()
        _join(first)
        if app._end_lock.locked():
            app._end_lock.release()  # in a real process the exit above ends the waiting thread
        _join(second)
        assert not first.is_alive()
        assert not second.is_alive()


def _join(thread: threading.Thread) -> None:
    if thread.ident is not None:  # started
        thread.join(_GUARD)


class _SilentDriver:
    """A driver of textual that writes nothing, so that only what NovaNavigator itself does reaches the terminal."""

    def write(self, data: str) -> None:
        pass

    def stop_application_mode(self) -> None:
        pass

    def close(self) -> None:
        pass


def test_input_pending_when_the_terminal_is_given_back_is_dropped(monkeypatch: pytest.MonkeyPatch) -> None:
    controlling, terminal = os.openpty()
    try:
        with open(terminal, closefd=False) as stdin:
            monkeypatch.setattr(sys, "__stdin__", stdin)
            app = NovaNavigator()
            app._driver = cast("Driver", _SilentDriver())
            os.write(controlling, b"\x1b[<35;1;1M\n")  # a mouse report that nobody has read
            assert select.select([terminal], [], [], _GUARD)[0]

            app._release_terminal()

            assert not select.select([terminal], [], [], 0)[0]
    finally:
        os.close(terminal)
        os.close(controlling)


class _ShellReaper:
    name = "end-shell-1"

    def __init__(self) -> None:
        self.timeouts: list[float] = []

    def join(self, timeout: float) -> None:
        self.timeouts.append(timeout)


@pytest.mark.usefixtures("printed")
@pytest.mark.parametrize("seconds_left", [-1.0, 4.0], ids=["deadline passed", "time left"])
def test_waiting_for_shells_ends_at_the_deadline_of_the_forced_quit(monkeypatch: pytest.MonkeyPatch, exits: list[int], seconds_left: float) -> None:
    app = _quitting_app()
    app._forced_quit_deadline = time.monotonic() + seconds_left
    reapers = [_ShellReaper(), _ShellReaper()]
    monkeypatch.setattr(threading, "enumerate", lambda: reapers)

    app._end_process()

    assert exits == [0]
    for reaper in reapers:
        [timeout] = reaper.timeouts
        assert 0.0 <= timeout <= max(seconds_left, 0.0)
