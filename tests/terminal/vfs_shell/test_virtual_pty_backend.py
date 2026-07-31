"""Tests for VirtualPtyBackend."""

from __future__ import annotations

import asyncio
from typing import Any

import pytest

from nova_navigator.terminal.vfs_shell.virtual_pty_backend import VirtualPtyBackend
from tests._utils.mock_filesystem import MockFilesystem


def make_backend(fs: MockFilesystem) -> tuple[VirtualPtyBackend, asyncio.Queue[list[object]]]:
    """Create a VirtualPtyBackend attached to a queue, already opened."""
    backend = VirtualPtyBackend(fs, fs.cwd())
    loop = asyncio.get_event_loop()
    queue: asyncio.Queue[list[object]] = asyncio.Queue()
    backend.attach_readers(loop, queue)
    backend.open("", 24, 80)
    return backend, queue


async def drain(queue: asyncio.Queue[list[object]]) -> list[list[Any]]:
    """Collect all available messages from queue without blocking."""
    msgs = []
    while True:
        try:
            msgs.append(queue.get_nowait())
        except asyncio.QueueEmpty:
            break
    return msgs


@pytest.fixture
def fs() -> MockFilesystem:
    return MockFilesystem(
        {
            "/home/user/hello.txt": b"hello world\n",
        }
    )


@pytest.mark.asyncio
async def test_open_posts_initial_messages(fs: MockFilesystem) -> None:
    backend, queue = make_backend(fs)
    await asyncio.sleep(0)  # let event loop process
    msgs = await drain(queue)
    types = [m[0] for m in msgs]
    assert "pre_cmd" in types
    assert "prompt_ready" in types
    assert "stdout" in types
    backend.teardown()


@pytest.mark.asyncio
async def test_write_command_posts_stdout(fs: MockFilesystem) -> None:
    backend, queue = make_backend(fs)
    await asyncio.sleep(0)

    # Clear initial messages
    await drain(queue)

    # Type "pwd\r"
    backend.write(b"pwd\r")

    # Let command run
    await asyncio.sleep(0.1)

    msgs = await drain(queue)
    text = "".join(str(m[1]) for m in msgs if m[0] == "stdout")
    assert "/home/user" in text


@pytest.mark.asyncio
async def test_teardown_stops_running(fs: MockFilesystem) -> None:
    backend, _queue = make_backend(fs)
    await asyncio.sleep(0)
    backend.teardown()
    assert not backend._running


@pytest.mark.asyncio
async def test_resize_updates_dimensions(fs: MockFilesystem) -> None:
    backend, _queue = make_backend(fs)
    await asyncio.sleep(0)
    backend.resize(40, 120)
    assert backend._rows == 40
    assert backend._cols == 120
    backend.teardown()


@pytest.mark.asyncio
async def test_prompt_ready_after_command(fs: MockFilesystem) -> None:
    backend, queue = make_backend(fs)
    await asyncio.sleep(0)
    await drain(queue)

    backend.write(b"pwd\r")
    await asyncio.sleep(0.1)

    msgs = await drain(queue)
    types = [m[0] for m in msgs]
    assert "prompt_ready" in types
