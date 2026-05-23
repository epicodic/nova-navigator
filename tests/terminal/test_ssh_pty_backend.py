"""Tests for SshPtyBackend."""

from __future__ import annotations

import asyncio
from collections.abc import Generator
from pathlib import Path

import paramiko
import pytest

from nova_navigator.terminal.pty_backend import PtyBackend
from nova_navigator.terminal.ssh_pty_backend import SshPtyBackend
from tests._utils.stub_ssh_server import StubSSHServer


@pytest.fixture
def ssh_backend() -> Generator[tuple[SshPtyBackend, StubSSHServer], None, None]:
    server = StubSSHServer(root_dir=Path("/tmp"))
    server.start()
    client = paramiko.SSHClient()
    client.set_missing_host_key_policy(paramiko.AutoAddPolicy())
    client.connect("127.0.0.1", port=server.port, username="test", password="test")
    backend = SshPtyBackend(ssh_client=client)
    try:
        yield backend, server
    finally:
        backend.teardown()
        client.close()
        server.stop()


def test_ssh_pty_backend_is_a_pty_backend(
    ssh_backend: tuple[SshPtyBackend, StubSSHServer],
) -> None:
    backend, _ = ssh_backend
    assert isinstance(backend, PtyBackend)


def test_ssh_pty_backend_supports_precmd_is_true() -> None:
    from unittest.mock import MagicMock

    client = MagicMock(spec=paramiko.SSHClient)
    backend = SshPtyBackend(client)
    assert backend.supports_precmd is True


def test_open_returns_none(
    ssh_backend: tuple[SshPtyBackend, StubSSHServer],
) -> None:
    backend, _ = ssh_backend
    result = backend.open("/bin/sh", rows=24, cols=80)
    assert result is None


def test_open_creates_channel(
    ssh_backend: tuple[SshPtyBackend, StubSSHServer],
) -> None:
    backend, _ = ssh_backend
    backend.open("/bin/sh", rows=24, cols=80)
    assert backend._channel is not None


def test_resize_does_not_raise(
    ssh_backend: tuple[SshPtyBackend, StubSSHServer],
) -> None:
    backend, _ = ssh_backend
    backend.open("/bin/sh", rows=24, cols=80)
    backend.resize(rows=30, cols=120)


def test_resume_does_not_raise(
    ssh_backend: tuple[SshPtyBackend, StubSSHServer],
) -> None:
    backend, _ = ssh_backend
    backend.resume()  # no-op in Option A; must never raise


def test_teardown_closes_channel(
    ssh_backend: tuple[SshPtyBackend, StubSSHServer],
) -> None:
    backend, _ = ssh_backend
    backend.open("/bin/sh", rows=24, cols=80)
    backend.teardown()
    assert backend._channel is None


def test_teardown_is_idempotent(
    ssh_backend: tuple[SshPtyBackend, StubSSHServer],
) -> None:
    backend, _ = ssh_backend
    backend.open("/bin/sh", rows=24, cols=80)
    backend.teardown()
    backend.teardown()  # must not raise


@pytest.mark.asyncio
async def test_attach_readers_posts_stdout_to_recv_queue(
    ssh_backend: tuple[SshPtyBackend, StubSSHServer],
) -> None:
    backend, _ = ssh_backend
    backend.open("/bin/sh", rows=24, cols=80)
    loop = asyncio.get_running_loop()
    recv_queue: asyncio.Queue[list[object]] = asyncio.Queue()
    backend.attach_readers(loop, recv_queue)

    await asyncio.sleep(0.3)  # let shell initialise

    # Drain startup noise
    while not recv_queue.empty():
        recv_queue.get_nowait()

    backend.write(b"echo SSH_MARKER_42\n")

    found = False
    for _ in range(60):
        await asyncio.sleep(0.05)
        while not recv_queue.empty():
            msg = recv_queue.get_nowait()
            if msg[0] == "stdout" and "SSH_MARKER_42" in str(msg[1]):
                found = True
                break
        if found:
            break

    backend.detach_readers()
    assert found, "Expected stdout containing SSH_MARKER_42 in recv_queue"


@pytest.mark.asyncio
async def test_detach_readers_does_not_post_disconnect(
    ssh_backend: tuple[SshPtyBackend, StubSSHServer],
) -> None:
    """Intentional stop must not generate a spurious disconnect message."""
    backend, _ = ssh_backend
    backend.open("/bin/sh", rows=24, cols=80)
    loop = asyncio.get_running_loop()
    recv_queue: asyncio.Queue[list[object]] = asyncio.Queue()
    backend.attach_readers(loop, recv_queue)
    await asyncio.sleep(0.3)

    backend.detach_readers()
    await asyncio.sleep(0.1)

    messages = []
    while not recv_queue.empty():
        messages.append(recv_queue.get_nowait())

    disconnect_msgs = [m for m in messages if m[0] == "disconnect"]
    assert disconnect_msgs == [], f"Unexpected disconnect messages: {disconnect_msgs}"
