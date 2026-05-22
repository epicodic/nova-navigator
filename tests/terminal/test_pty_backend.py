"""Unit tests for PtyBackend ABC and LocalPtyBackend."""

from __future__ import annotations

import asyncio
import os
import time

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


@pytest.fixture
def backend() -> LocalPtyBackend:
    return LocalPtyBackend()


def test_process_chunk_posts_stdout_before_trailing_osc(backend: LocalPtyBackend) -> None:
    """Stdout that precedes OSC 133;B in the same chunk must arrive before prompt_ready."""
    loop = asyncio.new_event_loop()
    queue: asyncio.Queue[list[object]] = asyncio.Queue()
    backend._process_chunk(b"prompt$ \033]133;B\007", loop, queue)
    loop.run_until_complete(asyncio.sleep(0))

    messages = []
    while not queue.empty():
        messages.append(queue.get_nowait())

    # stdout must come before prompt_ready
    stdout_idx = next(i for i, m in enumerate(messages) if m[0] == "stdout")
    prompt_idx = next(i for i, m in enumerate(messages) if m[0] == "prompt_ready")
    assert stdout_idx < prompt_idx


def test_process_chunk_dispatches_osc133b_as_prompt_ready(backend: LocalPtyBackend) -> None:
    """OSC 133;B must post ["prompt_ready"] to the queue."""
    loop = asyncio.new_event_loop()
    queue: asyncio.Queue[list[object]] = asyncio.Queue()
    backend._process_chunk(b"\033]133;B\007", loop, queue)
    loop.run_until_complete(asyncio.sleep(0))

    messages = []
    while not queue.empty():
        messages.append(queue.get_nowait())

    assert ["prompt_ready"] in messages


def test_process_chunk_discards_unknown_osc133_subtypes(backend: LocalPtyBackend) -> None:
    """OSC 133;A and OSC 133;D should be silently ignored (not crash)."""
    loop = asyncio.new_event_loop()
    queue: asyncio.Queue[list[object]] = asyncio.Queue()
    backend._process_chunk(b"\033]133;A\007\033]133;D\007", loop, queue)
    loop.run_until_complete(asyncio.sleep(0))

    messages = []
    while not queue.empty():
        messages.append(queue.get_nowait())

    # No prompt_ready for unknown subtypes
    assert ["prompt_ready"] not in messages


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
