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
_MOUSE_MODES = (1000, 1003, 1006)
_MARKER = "NN_TEST_PROCESS"


@dataclass
class _Run:
    exit_status: int
    output: bytes
    work: Path
    copy_root: Path
    terminal_before: list[object]
    terminal_after: list[object]


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


def _run_app(case: str, root: Path, bashrc: str = "") -> _Run:
    env = {"PATH": os.environ.get("PATH", "/usr/bin:/bin"), "TERM": "xterm-256color", "LANG": "C.UTF-8", "SHELL": "/bin/bash", _MARKER: str(root)}
    directories = {"HOME": "home", "TMPDIR": "tmp", "XDG_CONFIG_HOME": "config", "XDG_DATA_HOME": "data", "XDG_CACHE_HOME": "cache", "XDG_STATE_HOME": "state"}
    for variable, name in directories.items():
        (root / name).mkdir()
        env[variable] = str(root / name)
    if bashrc:
        (root / "home" / ".bashrc").write_text(bashrc)
    work = root / "work"
    work.mkdir()

    terminal, child_side = os.openpty()
    try:
        fcntl.ioctl(child_side, termios.TIOCSWINSZ, struct.pack("HHHH", 40, 120, 0, 0))
        terminal_before = termios.tcgetattr(child_side)
        process = subprocess.Popen([sys.executable, str(_APP), case, str(work)], stdin=child_side, stdout=child_side, stderr=child_side, env=env, cwd=work, start_new_session=True)
        try:
            output, ended = _read_until_exit(process, terminal)
            failure = work / "failure"
            assert not failure.exists(), failure.read_text()
            assert ended, f"the process did not end; terminal output ends with {output[-1500:]!r}"
            deadline = time.monotonic() + _HANG_LIMIT
            while _marked_processes(str(root)) and time.monotonic() < deadline:
                time.sleep(0.05)
            assert _marked_processes(str(root)) == [], "the application left processes behind"
            terminal_after = termios.tcgetattr(child_side)
        finally:
            process.kill()
            process.wait()
            for pid in _marked_processes(str(root)):
                with contextlib.suppress(ProcessLookupError):
                    os.kill(pid, signal.SIGKILL)
    finally:
        os.close(child_side)
        os.close(terminal)
    return _Run(process.returncode, output, work, root / "tmp" / "nova-navigator", terminal_before, terminal_after)


def _assert_copy_kept(run: _Run) -> Path:
    [kept] = find_orphans(root=run.copy_root)  # the next start reports it
    [copy] = [path for path in kept.rglob("a.txt") if path.is_file()]
    assert copy.read_text() == "EDITED\n"
    return kept


def _assert_terminal_restored(run: _Run, before: int) -> None:
    """Mouse reporting and the alternate screen are off before position *before*, and the line settings are back."""
    for mode in _MOUSE_MODES:
        on_at = run.output.rfind(f"\x1b[?{mode}h".encode(), 0, before)
        off_at = run.output.rfind(f"\x1b[?{mode}l".encode(), 0, before)
        assert 0 <= on_at < off_at, f"mouse mode {mode} was left on"
    assert run.output.rfind(_LEAVE_ALTERNATE_SCREEN, 0, before) >= 0
    assert run.terminal_after == run.terminal_before


def _assert_quit_and_kept_the_copy(run: _Run, exit_status: int = 0) -> None:
    assert run.exit_status == exit_status, run.output[-1500:]
    assert (run.work / "write-entered").exists()
    message_at = run.output.find(_MESSAGE)
    assert message_at >= 0, run.output[-1500:]
    _assert_terminal_restored(run, message_at)  # the message is printed on the normal screen
    assert _LEAVE_ALTERNATE_SCREEN not in run.output[message_at:], "nothing may switch screens over the message"

    kept = _assert_copy_kept(run)
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
def test_the_process_ends_although_nothing_reads_its_output(tmp_path: Path) -> None:
    run = _run_app("blocked_output", tmp_path)

    assert (run.work / "exit-entered").exists()
    assert run.exit_status == 0, run.output[-1500:]
    assert run.terminal_after == run.terminal_before
    _assert_copy_kept(run)


@pytest.mark.integration
@pytest.mark.parametrize(("case", "marker"), [("hung_unsynced", "unsynced-entered"), ("hung_session_save", "save-entered")])
def test_the_process_ends_although_the_forced_quit_itself_hangs(tmp_path: Path, case: str, marker: str) -> None:
    run = _run_app(case, tmp_path)

    assert (run.work / marker).exists()
    _assert_quit_and_kept_the_copy(run)


@pytest.mark.integration
def test_an_error_during_teardown_is_printed_and_fails_the_exit_status(tmp_path: Path) -> None:
    run = _run_app("teardown_error", tmp_path)

    _assert_quit_and_kept_the_copy(run, exit_status=1)
    assert 0 <= run.output.find(_MESSAGE) < run.output.find(b"RuntimeError: boom-in-teardown")
    assert b"Traceback (most recent call last)" in run.output


@pytest.mark.integration
def test_a_shell_that_ignores_the_hangup_is_ended(tmp_path: Path) -> None:
    bashrc = 'trap \'\' HUP\nmkfifo "$HOME/never"\ntouch "$HOME/shell-ignores-hangup"\nread <>"$HOME/never"\n'

    run = _run_app("stubborn_shell", tmp_path, bashrc)  # fails if the shell outlives the application

    assert (tmp_path / "home" / "shell-ignores-hangup").exists()
    _assert_quit_and_kept_the_copy(run)


@pytest.mark.integration
def test_a_plain_quit_returns_from_run_as_before(tmp_path: Path) -> None:
    run = _run_app("nothing_pending", tmp_path)

    assert run.exit_status == 0, run.output[-1500:]
    assert (run.work / "run-returned").exists()
    assert _MESSAGE not in run.output
