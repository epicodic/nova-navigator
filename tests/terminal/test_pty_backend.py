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


def test_local_pty_backend_supports_precmd_pipe() -> None:
    backend = LocalPtyBackend()
    assert backend.supports_precmd_pipe is True


def test_open_returns_precmd_fd_number() -> None:
    backend = LocalPtyBackend()
    try:
        precmd_fd = backend.open("/bin/sh", rows=24, cols=80)
        assert isinstance(precmd_fd, int)
        assert precmd_fd > 0
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
