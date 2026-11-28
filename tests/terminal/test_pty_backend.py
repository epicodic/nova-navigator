"""Unit tests for PtyBackend ABC and LocalPtyBackend."""

from __future__ import annotations

import asyncio
import contextlib
import os
import select
import signal
import subprocess
import sys
import threading
import time
from collections.abc import Iterator
from pathlib import Path

import pytest

from nova_navigator.terminal.pty_backend import LocalPtyBackend, PtyBackend


def test_local_pty_backend_is_a_pty_backend() -> None:
    backend = LocalPtyBackend()
    assert isinstance(backend, PtyBackend)


def test_local_pty_backend_supports_precmd_is_true() -> None:
    backend = LocalPtyBackend()
    assert backend.supports_precmd is True


def test_open_returns_none() -> None:
    backend = LocalPtyBackend()
    try:
        result = backend.open("/bin/sh", rows=24, cols=80)
        assert result is None
    finally:
        backend.teardown()


def test_open_creates_child_process() -> None:
    backend = LocalPtyBackend()
    try:
        backend.open("/bin/sh", rows=24, cols=80)
        assert backend._pid > 0
        os.kill(backend._pid, 0)  # raises if process doesn't exist
    finally:
        backend.teardown()


def test_teardown_terminates_child_process() -> None:
    backend = LocalPtyBackend()
    backend.open("/bin/sh", rows=24, cols=80)
    pid = backend._pid
    backend.teardown()
    # The child may still be a zombie after WNOHANG. Poll waitpid to reap it.
    for _ in range(20):
        try:
            rpid, _ = os.waitpid(pid, os.WNOHANG)
            if rpid != 0:
                return  # Reaped — process is gone
        except ChildProcessError:
            return  # Already reaped by teardown
        time.sleep(0.05)
    pytest.fail("Child process did not exit after teardown")


def test_write_sends_bytes_to_shell() -> None:
    backend = LocalPtyBackend()
    try:
        backend.open("/bin/sh", rows=24, cols=80)
        backend.write(b"echo hello\n")  # should not raise
    finally:
        backend.teardown()


def test_resize_does_not_raise() -> None:
    backend = LocalPtyBackend()
    try:
        backend.open("/bin/sh", rows=24, cols=80)
        backend.resize(rows=30, cols=100)
    finally:
        backend.teardown()


# ---------------------------------------------------------------------------
# OSC 7 scanner — _process_chunk and _on_osc
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_process_chunk_strips_osc7_and_posts_pre_cmd() -> None:
    backend = LocalPtyBackend()
    loop = asyncio.get_running_loop()
    queue: asyncio.Queue[list[object]] = asyncio.Queue()

    path = "/home/user/projects"
    osc7 = f"\033]7;file://{path}\007"
    backend._process_chunk(f"before{osc7}after".encode(), loop, queue)
    await asyncio.sleep(0)  # let call_soon_threadsafe callbacks run

    messages = []
    while not queue.empty():
        messages.append(queue.get_nowait())

    pre_cmds = [m for m in messages if m[0] == "pre_cmd"]
    stdouts = [m for m in messages if m[0] == "stdout"]
    assert len(pre_cmds) == 1
    assert pre_cmds[0][1] == path
    stdout_text = "".join(str(m[1]) for m in stdouts)
    assert "before" in stdout_text
    assert "after" in stdout_text


@pytest.mark.asyncio
async def test_process_chunk_handles_split_osc_sequence_across_two_chunks() -> None:
    backend = LocalPtyBackend()
    loop = asyncio.get_running_loop()
    queue: asyncio.Queue[list[object]] = asyncio.Queue()

    path = "/home/user/split"
    full_osc7 = f"\033]7;file://{path}\007"
    # Split the sequence somewhere in the middle
    split = len(full_osc7) // 2
    first_chunk = ("before" + full_osc7[:split]).encode()
    second_chunk = (full_osc7[split:] + "after").encode()

    backend._process_chunk(first_chunk, loop, queue)
    backend._process_chunk(second_chunk, loop, queue)
    await asyncio.sleep(0)  # let call_soon_threadsafe callbacks run

    messages = []
    while not queue.empty():
        messages.append(queue.get_nowait())

    pre_cmds = [m for m in messages if m[0] == "pre_cmd"]
    assert len(pre_cmds) == 1
    assert pre_cmds[0][1] == path


@pytest.mark.asyncio
async def test_process_chunk_passes_non_osc_text_as_stdout() -> None:
    backend = LocalPtyBackend()
    loop = asyncio.get_running_loop()
    queue: asyncio.Queue[list[object]] = asyncio.Queue()

    backend._process_chunk(b"hello world", loop, queue)
    await asyncio.sleep(0)  # let call_soon_threadsafe callbacks run

    messages = []
    while not queue.empty():
        messages.append(queue.get_nowait())

    assert any(m[0] == "stdout" and "hello world" in str(m[1]) for m in messages)
    assert not any(m[0] == "pre_cmd" for m in messages)


@pytest.mark.asyncio
async def test_process_chunk_ignores_unknown_osc_codes() -> None:
    backend = LocalPtyBackend()
    loop = asyncio.get_running_loop()
    queue: asyncio.Queue[list[object]] = asyncio.Queue()

    # OSC 2 is the window title sequence — should be stripped silently
    backend._process_chunk(b"\033]2;My Window Title\007normal text", loop, queue)
    await asyncio.sleep(0)  # let call_soon_threadsafe callbacks run

    messages = []
    while not queue.empty():
        messages.append(queue.get_nowait())

    assert not any(m[0] == "pre_cmd" for m in messages)
    stdouts = [m for m in messages if m[0] == "stdout"]
    assert any("normal text" in str(m[1]) for m in stdouts)


@pytest.mark.asyncio
async def test_osc_133_is_stripped_and_posts_no_message() -> None:
    """OSC 133 sequences are removed from stdout and do not produce a queue message."""
    backend = LocalPtyBackend()
    loop = asyncio.get_running_loop()
    queue: asyncio.Queue[list[object]] = asyncio.Queue()

    backend._process_chunk(b"before\x1b]133;B\x07after", loop, queue)
    await asyncio.sleep(0)

    messages: list[list[object]] = []
    while not queue.empty():
        messages.append(queue.get_nowait())
    assert messages == [["stdout", "before"], ["stdout", "after"]]


def test_resume_on_dead_process_does_not_raise() -> None:
    backend = LocalPtyBackend()
    backend.open("/bin/sh", rows=24, cols=80)
    backend.teardown()
    backend.resume()  # must not raise


@pytest.mark.asyncio
async def test_attach_readers_posts_stdout_to_recv_queue() -> None:
    backend = LocalPtyBackend()
    try:
        backend.open("/bin/sh", rows=24, cols=80)
        loop = asyncio.get_running_loop()
        recv_queue: asyncio.Queue[list[object]] = asyncio.Queue()
        backend.attach_readers(loop, recv_queue)

        backend.write(b"echo HELLO_MARKER\n")

        found = False
        for _ in range(50):
            await asyncio.sleep(0.05)
            while not recv_queue.empty():
                msg = recv_queue.get_nowait()
                if msg[0] == "stdout" and "HELLO_MARKER" in str(msg[1]):
                    found = True
                    break
            if found:
                break

        backend.detach_readers()
        assert found, "Expected stdout with HELLO_MARKER in recv_queue"
    finally:
        backend.teardown()


@pytest.mark.asyncio
async def test_detach_readers_stops_output_flow() -> None:
    backend = LocalPtyBackend()
    try:
        backend.open("/bin/sh", rows=24, cols=80)
        loop = asyncio.get_running_loop()
        recv_queue: asyncio.Queue[list[object]] = asyncio.Queue()
        backend.attach_readers(loop, recv_queue)
        backend.detach_readers()

        await asyncio.sleep(0.05)
        while not recv_queue.empty():
            recv_queue.get_nowait()

        backend.write(b"echo SHOULD_NOT_APPEAR\n")
        await asyncio.sleep(0.1)
        messages = []
        while not recv_queue.empty():
            messages.append(recv_queue.get_nowait())
        stdout_msgs = [m for m in messages if m[0] == "stdout"]
        assert len(stdout_msgs) == 0
    finally:
        backend.teardown()


@pytest.mark.asyncio
async def test_process_chunk_osc7_with_panel_prefix_posts_from_nn_true() -> None:
    """OSC 7 with panel=;file:///path posts ["pre_cmd", "/path", True] (from_nn=True)."""
    backend = LocalPtyBackend.__new__(LocalPtyBackend)
    backend._osc_buf = ""
    loop = asyncio.get_running_loop()
    queue: asyncio.Queue[list[object]] = asyncio.Queue()

    path = "/home/user/projects"
    osc7 = f"\033]7;panel=;file://{path}\007"
    backend._process_chunk(osc7.encode(), loop, queue)
    await asyncio.sleep(0)  # let call_soon_threadsafe callbacks run

    messages: list[list[object]] = []
    while not queue.empty():
        messages.append(queue.get_nowait())

    pre_cmds = [m for m in messages if m[0] == "pre_cmd"]
    assert len(pre_cmds) == 1
    assert pre_cmds[0][1] == path
    assert pre_cmds[0][2] is True


@pytest.mark.asyncio
async def test_process_chunk_osc7_with_empty_panel_posts_from_nn_true() -> None:
    """OSC 7 with panel=;file:///path is recognised as NN origin (from_nn=True)."""
    backend = LocalPtyBackend.__new__(LocalPtyBackend)
    backend._osc_buf = ""
    loop = asyncio.get_running_loop()
    queue: asyncio.Queue[list[object]] = asyncio.Queue()

    path = "/home/user"
    osc7 = f"\033]7;panel=;file://{path}\007"
    backend._process_chunk(osc7.encode(), loop, queue)
    await asyncio.sleep(0)  # let call_soon_threadsafe callbacks run

    messages: list[list[object]] = []
    while not queue.empty():
        messages.append(queue.get_nowait())

    pre_cmds = [m for m in messages if m[0] == "pre_cmd"]
    assert len(pre_cmds) == 1
    assert pre_cmds[0][1] == path
    assert pre_cmds[0][2] is True


@pytest.mark.asyncio
async def test_process_chunk_osc7_without_panel_prefix_posts_from_nn_false() -> None:
    """OSC 7 without panel= prefix posts ["pre_cmd", path, False] — from_nn=False marks
    it as a third-party chpwd hook event so _handle_pre_cmd can ignore it."""
    backend = LocalPtyBackend.__new__(LocalPtyBackend)
    backend._osc_buf = ""
    loop = asyncio.get_running_loop()
    queue: asyncio.Queue[list[object]] = asyncio.Queue()

    path = "/home/user"
    osc7 = f"\033]7;file://{path}\007"
    backend._process_chunk(osc7.encode(), loop, queue)
    await asyncio.sleep(0)  # let call_soon_threadsafe callbacks run

    messages: list[list[object]] = []
    while not queue.empty():
        messages.append(queue.get_nowait())

    pre_cmds = [m for m in messages if m[0] == "pre_cmd"]
    assert len(pre_cmds) == 1
    assert pre_cmds[0][1] == path
    assert pre_cmds[0][2] is False


@pytest.mark.asyncio
async def test_process_chunk_osc7_with_legacy_panel_value_still_posts_from_nn_true() -> None:
    """OSC 7 with panel=left;file:// (legacy format) still posts from_nn=True."""
    backend = LocalPtyBackend.__new__(LocalPtyBackend)
    backend._osc_buf = ""
    loop = asyncio.get_running_loop()
    queue: asyncio.Queue[list[object]] = asyncio.Queue()

    path = "/srv"
    osc7 = f"\033]7;panel=left;file://{path}\007"
    backend._process_chunk(osc7.encode(), loop, queue)
    await asyncio.sleep(0)  # let call_soon_threadsafe callbacks run

    messages: list[list[object]] = []
    while not queue.empty():
        messages.append(queue.get_nowait())

    pre_cmds = [m for m in messages if m[0] == "pre_cmd"]
    assert len(pre_cmds) == 1
    assert pre_cmds[0][1] == path
    assert pre_cmds[0][2] is True


@pytest.mark.asyncio
async def test_attach_readers_posts_osc7_as_pre_cmd_via_stdout() -> None:
    """Verify that an OSC 7 sequence emitted by the shell arrives as pre_cmd."""
    backend = LocalPtyBackend()
    try:
        backend.open("/bin/sh", rows=24, cols=80)
        loop = asyncio.get_running_loop()
        recv_queue: asyncio.Queue[list[object]] = asyncio.Queue()
        backend.attach_readers(loop, recv_queue)

        # Write an OSC 7 sequence followed by a newline to flush the line buffer
        backend.write(b"printf '\\033]7;file:///test/dir\\007'\n")

        found_pre_cmd = False
        for _ in range(50):
            await asyncio.sleep(0.05)
            while not recv_queue.empty():
                msg = recv_queue.get_nowait()
                if msg[0] == "pre_cmd" and "/test/dir" in str(msg[1]):
                    found_pre_cmd = True
        assert found_pre_cmd, "pre_cmd message with /test/dir not received"
    finally:
        backend.detach_readers()
        backend.teardown()


def _isolate_shell_env(home: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    """Keep a shell started by a test away from the real home directory and its ``ENV`` file."""
    monkeypatch.setenv("HOME", str(home))
    monkeypatch.delenv("ENV", raising=False)


def test_failed_exec_exits_the_child(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    _isolate_shell_env(tmp_path, monkeypatch)
    backend = LocalPtyBackend()
    backend.open("/nonexistent/shell", rows=24, cols=80)
    # Wait for the exit without reaping it, so teardown() still sees the pid as its own child.
    exit_info = os.waitid(os.P_PID, backend._pid, os.WEXITED | os.WNOWAIT)
    assert exit_info is not None
    assert exit_info.si_code == os.CLD_EXITED
    assert exit_info.si_status == 127
    backend.teardown()


def test_teardown_twice_is_safe(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    _isolate_shell_env(tmp_path, monkeypatch)
    backend = LocalPtyBackend()
    backend.open("/bin/sh", rows=24, cols=80)
    pid = backend._pid
    os.kill(pid, signal.SIGKILL)
    # Wait for the exit without reaping it, so the first teardown() is the one that reaps the child.
    os.waitid(os.P_PID, pid, os.WEXITED | os.WNOWAIT)
    backend.teardown()
    assert backend._pid == -1

    signals_sent: list[tuple[int, int]] = []
    monkeypatch.setattr(os, "kill", lambda target, sig: signals_sent.append((target, sig)))
    backend.teardown()
    backend.resume()
    assert signals_sent == []


def _run_until_ready(backend: LocalPtyBackend, script: str = "") -> None:
    """Run *script* in bash and read its output until bash shows its next prompt."""
    # The quotes keep both markers out of the echoed command line.
    backend.write(f"PS1='shell-pro''mpt$ '\n{script}echo shell-rea''dy\n".encode())
    output = b""
    deadline = time.monotonic() + 10
    while b"shell-prompt$ " not in output.partition(b"shell-ready")[2]:
        readable, _, _ = select.select([backend._master_fd], [], [], max(0.0, deadline - time.monotonic()))
        assert readable, f"the shell did not answer: {output!r}"
        output += os.read(backend._master_fd, 65536)


def _has_exited(pid: int) -> bool:
    """Return True if *pid* no longer runs: it is a zombie or has left the process table."""
    try:
        stat = Path(f"/proc/{pid}/stat").read_text()
    except (FileNotFoundError, ProcessLookupError):
        return True
    return stat.rpartition(")")[2].split()[0] in ("Z", "X")


def _exits_within(pid: int, seconds: float) -> bool:
    deadline = time.monotonic() + seconds
    while not _has_exited(pid):
        if time.monotonic() > deadline:
            return False
        time.sleep(0.02)
    return True


@contextlib.contextmanager
def _killed_afterwards(pid: int) -> Iterator[int]:
    """Yield *pid* and make sure the process is gone when the block ends."""
    try:
        yield pid
    finally:
        if not _has_exited(pid):
            with contextlib.suppress(ProcessLookupError):
                os.kill(pid, signal.SIGKILL)
        with contextlib.suppress(ChildProcessError):
            os.waitpid(pid, 0)


def _interactive_bash(home: Path, monkeypatch: pytest.MonkeyPatch) -> LocalPtyBackend:
    _isolate_shell_env(home, monkeypatch)
    for name in ("PS1", "PROMPT_COMMAND", "HISTFILE", "BASH_ENV"):
        monkeypatch.delenv(name, raising=False)
    backend = LocalPtyBackend()
    backend.open("/bin/bash", rows=24, cols=80)
    return backend


def test_teardown_ends_an_interactive_shell(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    backend = _interactive_bash(tmp_path, monkeypatch)
    with _killed_afterwards(backend._pid) as shell:
        # Once the shell answers it ignores SIGTERM, as every interactive shell does.
        _run_until_ready(backend)
        backend.teardown()
        assert _exits_within(shell, 10), "the shell outlived teardown()"


def test_later_shell_holds_no_earlier_master(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    first = _interactive_bash(tmp_path, monkeypatch)
    with _killed_afterwards(first._pid):
        second = _interactive_bash(tmp_path, monkeypatch)
        with _killed_afterwards(second._pid):
            _run_until_ready(second)  # the shell has been exec'ed
            targets = []
            for entry in Path(f"/proc/{second._pid}/fd").iterdir():
                with contextlib.suppress(FileNotFoundError):
                    targets.append(str(entry.readlink()))
            second.teardown()
        first.teardown()
    assert targets
    assert [target for target in targets if target.endswith("ptmx")] == []


def test_teardown_leaves_detached_jobs_running(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    backend = _interactive_bash(tmp_path, monkeypatch)
    names = ("nohup", "disown", "setsid")
    jobs = {name: f"sh -c 'echo $$ > {tmp_path}/{name}.pid; exec sleep 60'" for name in names}
    with _killed_afterwards(backend._pid) as shell:
        waits = " && ".join(f"[ -s {tmp_path}/{name}.pid ]" for name in names)
        _run_until_ready(
            backend,
            f"nohup {jobs['nohup']} >/dev/null 2>&1 &\n{jobs['disown']} >/dev/null 2>&1 & disown\nsetsid -f {jobs['setsid']} >/dev/null 2>&1\nuntil {waits}; do sleep 0.05; done\n",
        )
        pids = {name: int((tmp_path / f"{name}.pid").read_text()) for name in names}
        with (
            _killed_afterwards(pids["nohup"]) as nohup,
            _killed_afterwards(pids["disown"]) as disowned,
            _killed_afterwards(pids["setsid"]) as new_session,
        ):
            backend.teardown()
            assert _exits_within(shell, 10), "the shell outlived teardown()"
            # The shell has exited, so it has sent every hangup it is going to send.  Ordinary jobs
            # are not asserted on: bash usually hangs them up, but not on every hangup of its terminal.
            assert not _exits_within(nohup, 0.5)
            assert not _exits_within(disowned, 0)
            assert not _exits_within(new_session, 0)


@pytest.mark.asyncio
async def test_teardown_removes_an_attached_reader(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    backend = _interactive_bash(tmp_path, monkeypatch)
    loop = asyncio.get_running_loop()
    with _killed_afterwards(backend._pid):
        master = backend._master_fd
        backend.attach_readers(loop, asyncio.Queue())
        backend.teardown()
        assert loop.remove_reader(master) is False


def test_write_and_resize_after_teardown_do_nothing(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    backend = _interactive_bash(tmp_path, monkeypatch)
    with _killed_afterwards(backend._pid):
        backend.teardown()
        backend.write(b"ignored\n")
        backend.resize(rows=30, cols=100)
        backend.teardown()
        assert backend._master_fd == -1


def test_teardown_before_open_is_safe() -> None:
    backend = LocalPtyBackend()
    backend.teardown()
    backend.teardown()


# A shell that ignores everything that normally ends it, the hangup of its terminal included.
_STUBBORN_SHELL = """/bin/sh -c "trap '' HUP TERM INT; echo stubborn-ready; exec sleep 60" """


def _wait_for_output(backend: LocalPtyBackend, marker: bytes) -> None:
    output = b""
    deadline = time.monotonic() + 10
    while marker not in output:
        readable, _, _ = select.select([backend._master_fd], [], [], max(0.0, deadline - time.monotonic()))
        assert readable, f"the shell did not answer: {output!r}"
        output += os.read(backend._master_fd, 65536)


def _is_reaped_within(pid: int, seconds: float) -> bool:
    """Return True once *pid* has left the process table, so it is neither running nor a zombie."""
    deadline = time.monotonic() + seconds
    while Path(f"/proc/{pid}").exists():
        if time.monotonic() > deadline:
            return False
        time.sleep(0.02)
    return True


def test_teardown_kills_a_shell_that_ignores_the_hangup(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    _isolate_shell_env(tmp_path, monkeypatch)
    backend = LocalPtyBackend()
    backend.open(_STUBBORN_SHELL, rows=24, cols=80)
    pid = backend._pid
    with _killed_afterwards(pid) as shell:
        _wait_for_output(backend, b"stubborn-ready")
        started = time.monotonic()
        backend.teardown()
        assert time.monotonic() - started < 0.5, "teardown() waited for the shell"
        assert not _exits_within(shell, 0.5), "the shell got no time to end by itself"
        assert _exits_within(shell, 5), "the shell outlived teardown()"
        assert _is_reaped_within(pid, 5)


def test_teardown_reaps_a_shell_that_ends_after_it(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    backend = _interactive_bash(tmp_path, monkeypatch)
    pid = backend._pid
    with _killed_afterwards(pid):
        _run_until_ready(backend)
        backend.teardown()
        assert _is_reaped_within(pid, 5)


def test_stubborn_shell_is_killed_when_teardown_is_the_last_thing_before_exit(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    _isolate_shell_env(tmp_path, monkeypatch)
    script = """
import os, select, sys
from nova_navigator.terminal.pty_backend import LocalPtyBackend

backend = LocalPtyBackend()
backend.open(sys.argv[1], rows=24, cols=80)
output = b""
while b"stubborn-ready" not in output:
    assert select.select([backend._master_fd], [], [], 10)[0]
    output += os.read(backend._master_fd, 65536)
print(backend._pid, flush=True)
backend.teardown()
"""
    result = subprocess.run([sys.executable, "-c", script, _STUBBORN_SHELL], capture_output=True, text=True, timeout=30, check=True)
    with _killed_afterwards(int(result.stdout)) as shell:
        assert _exits_within(shell, 0), "the shell outlived the process that tore it down"


def _open_stubborn_shell(home: Path, monkeypatch: pytest.MonkeyPatch) -> LocalPtyBackend:
    _isolate_shell_env(home, monkeypatch)
    backend = LocalPtyBackend()
    backend.open(_STUBBORN_SHELL, rows=24, cols=80)
    return backend


def test_teardown_needs_no_pidfd(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    # Some Python builds, those installed by uv among them, have no pidfd functions.
    monkeypatch.delattr(os, "pidfd_open", raising=False)
    monkeypatch.delattr(signal, "pidfd_send_signal", raising=False)
    backend = _open_stubborn_shell(tmp_path, monkeypatch)
    pid = backend._pid
    with _killed_afterwards(pid):
        _wait_for_output(backend, b"stubborn-ready")
        backend.teardown()
        assert _is_reaped_within(pid, 8), "the shell was not killed and reaped"


def test_teardown_kills_the_shell_when_no_thread_can_start(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    backend = _open_stubborn_shell(tmp_path, monkeypatch)
    pid = backend._pid

    def no_thread(_thread: threading.Thread) -> None:
        raise RuntimeError("can't start new thread")

    with _killed_afterwards(pid):
        _wait_for_output(backend, b"stubborn-ready")
        with monkeypatch.context() as patched:
            patched.setattr(threading.Thread, "start", no_thread)
            backend.teardown()
        assert not Path(f"/proc/{pid}").exists(), "the shell was not killed and reaped"


# A shell that needs two seconds to finish after the hangup, as with a slow exit hook.
_SLOW_EXIT_SHELL = """/bin/sh -c "trap '' TERM; trap 'sleep 2; echo done > saved; exit 0' HUP; echo slow-ready; sleep 60 & wait" """


def test_teardown_lets_a_slow_exit_finish(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    _isolate_shell_env(tmp_path, monkeypatch)
    monkeypatch.chdir(tmp_path)
    backend = LocalPtyBackend()
    backend.open(_SLOW_EXIT_SHELL, rows=24, cols=80)
    pid = backend._pid
    with _killed_afterwards(pid):
        _wait_for_output(backend, b"slow-ready")
        backend.teardown()
        assert _is_reaped_within(pid, 8)
        assert (tmp_path / "saved").exists(), "the shell was killed before it had finished"


@pytest.mark.asyncio
async def test_process_chunk_osc7_of_a_shell_without_line_editing_marks_the_message() -> None:
    """OSC 7 with panel=noedit;file:///path posts ["pre_cmd", "/path", True, False]."""
    backend = LocalPtyBackend.__new__(LocalPtyBackend)
    backend._osc_buf = ""
    loop = asyncio.get_running_loop()
    queue: asyncio.Queue[list[object]] = asyncio.Queue()

    backend._process_chunk(b"\033]7;panel=noedit;file:///home/user\007", loop, queue)
    await asyncio.sleep(0)  # let call_soon_threadsafe callbacks run

    assert queue.get_nowait() == ["pre_cmd", "/home/user", True, False]
    assert queue.empty()
