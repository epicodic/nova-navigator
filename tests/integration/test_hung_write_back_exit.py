"""The process ends after "Quit and keep files" of the write-back dialog, although a write-back never returns.

A pilot test cannot show this: a thread that is stuck in a write keeps the interpreter alive after the UI is gone.
So the application runs in a child process on a pseudo terminal (see ``hung_write_back_app.py``).
"""

from __future__ import annotations

import contextlib
import fcntl
import os
import select
import signal
import struct
import subprocess
import sys
import termios
import time
from dataclasses import dataclass
from pathlib import Path

import pytest

from nova_navigator.vfs.process_root import find_orphans

_APP = Path(__file__).with_name("hung_write_back_app.py")
_HANG_LIMIT = 60.0  # not a time budget: only a process that does not end at all gets here
_LEAVE_ALTERNATE_SCREEN = b"\x1b[?1049l"
_MESSAGE = b"Nova Navigator quit while a write-back was still running."
_MARKER = "NN_TEST_PROCESS"


@dataclass
class _Run:
    exit_status: int
    output: bytes
    work: Path
    copy_root: Path


def _marked_processes(marker: str) -> list[int]:
    """Processes started with this test's marker in their environment: the application and its shells."""
    needle = f"{_MARKER}={marker}\0".encode()
    found = []
    for environ in Path("/proc").glob("[0-9]*/environ"):
        with contextlib.suppress(OSError):
            if needle in environ.read_bytes():
                found.append(int(environ.parent.name))
    return found


def _read_until_exit(process: subprocess.Popen[bytes], terminal: int) -> tuple[bytes, bool]:
    """Collect the terminal output until *process* ends; the flag is False when it did not end within the limit."""
    output = bytearray()
    deadline = time.monotonic() + _HANG_LIMIT
    while time.monotonic() < deadline:
        ended = process.poll() is not None
        if select.select([terminal], [], [], 0.1)[0]:
            try:
                data = os.read(terminal, 65536)
            except OSError:
                data = b""
            output += data
            if data:
                continue
        if ended:
            return bytes(output), True
    return bytes(output), False


def _run_app(case: str, root: Path) -> _Run:
    env = {"PATH": os.environ.get("PATH", "/usr/bin:/bin"), "TERM": "xterm-256color", "LANG": "C.UTF-8", "SHELL": "/bin/bash", _MARKER: str(root)}
    directories = {"HOME": "home", "TMPDIR": "tmp", "XDG_CONFIG_HOME": "config", "XDG_DATA_HOME": "data", "XDG_CACHE_HOME": "cache", "XDG_STATE_HOME": "state"}
    for variable, name in directories.items():
        (root / name).mkdir()
        env[variable] = str(root / name)
    work = root / "work"
    work.mkdir()

    terminal, child_side = os.openpty()
    try:
        fcntl.ioctl(child_side, termios.TIOCSWINSZ, struct.pack("HHHH", 40, 120, 0, 0))
        try:
            process = subprocess.Popen([sys.executable, str(_APP), case, str(work)], stdin=child_side, stdout=child_side, stderr=child_side, env=env, cwd=work, start_new_session=True)
        finally:
            os.close(child_side)
        try:
            output, ended = _read_until_exit(process, terminal)
            failure = work / "failure"
            assert not failure.exists(), failure.read_text()
            assert ended, f"the process did not end; terminal output ends with {output[-1500:]!r}"
            deadline = time.monotonic() + _HANG_LIMIT
            while _marked_processes(str(root)) and time.monotonic() < deadline:
                time.sleep(0.05)
            assert _marked_processes(str(root)) == [], "the application left processes behind"
        finally:
            process.kill()
            process.wait()
            for pid in _marked_processes(str(root)):
                with contextlib.suppress(ProcessLookupError):
                    os.kill(pid, signal.SIGKILL)
    finally:
        os.close(terminal)
    return _Run(process.returncode, output, work, root / "tmp" / "nova-navigator")


def _assert_quit_and_kept_the_copy(run: _Run) -> None:
    assert run.exit_status == 0, run.output[-1500:]
    assert (run.work / "write-entered").exists()
    message_at = run.output.find(_MESSAGE)
    assert message_at >= 0, run.output[-1500:]
    left_at = run.output.find(_LEAVE_ALTERNATE_SCREEN)
    assert 0 <= left_at < message_at, "the message must be printed on the normal screen"
    assert _LEAVE_ALTERNATE_SCREEN not in run.output[message_at:], "nothing may switch screens over the message"

    [kept] = find_orphans(root=run.copy_root)  # the next start reports it
    [copy] = [path for path in kept.rglob("a.txt") if path.is_file()]
    assert copy.read_text() == "EDITED\n"
    message = run.output[message_at:].decode()
    assert f"Local copies were kept in {kept}\r\n" in message
    assert "Not written back: a.txt\r\n" in message


@pytest.mark.integration
def test_quit_and_keep_files_ends_the_process_despite_a_hung_write_back(tmp_path: Path) -> None:
    run = _run_app("hung_write", tmp_path)

    _assert_quit_and_kept_the_copy(run)


@pytest.mark.integration
def test_the_process_ends_although_a_widget_teardown_hangs_too(tmp_path: Path) -> None:
    run = _run_app("hung_teardown", tmp_path)

    assert (run.work / "teardown-entered").exists()
    _assert_quit_and_kept_the_copy(run)


@pytest.mark.integration
def test_the_terminal_is_given_back_when_the_app_hangs_before_doing_so(tmp_path: Path) -> None:
    run = _run_app("hung_exit", tmp_path)

    assert (run.work / "exit-entered").exists()
    _assert_quit_and_kept_the_copy(run)


@pytest.mark.integration
def test_a_plain_quit_returns_from_run_as_before(tmp_path: Path) -> None:
    run = _run_app("nothing_pending", tmp_path)

    assert run.exit_status == 0, run.output[-1500:]
    assert (run.work / "run-returned").exists()
    assert _MESSAGE not in run.output
