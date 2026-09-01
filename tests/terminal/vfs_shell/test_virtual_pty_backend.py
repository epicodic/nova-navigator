"""Tests for VirtualPtyBackend."""

from __future__ import annotations

import asyncio
from typing import Any

import pytest

from nova_navigator.terminal.shell_editor_protocol import (
    PROBE_SEQUENCE,
    RESTORE_SEQUENCE,
    STASH_SEQUENCE,
    EditorOperation,
    EditorResponse,
)
from nova_navigator.terminal.vfs_shell.virtual_pty_backend import VirtualPtyBackend
from tests._utils.mock_filesystem import MockFilesystem


def make_backend(
    fs: MockFilesystem,
    *,
    nonce: str | None = None,
) -> tuple[VirtualPtyBackend, asyncio.Queue[list[object]]]:
    """Create a VirtualPtyBackend attached to a queue, already opened."""
    backend = VirtualPtyBackend(fs, fs.cwd())
    if nonce is not None:
        backend.configure_editor_protocol(nonce)
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


def _editor_responses(messages: list[list[Any]]) -> list[EditorResponse]:
    return [m[1] for m in messages if m[0] == "editor_response"]


@pytest.mark.asyncio
async def test_editor_protocol_ready_announced_once_after_configure_open_attach(fs: MockFilesystem) -> None:
    backend = VirtualPtyBackend(fs, fs.cwd())
    loop = asyncio.get_event_loop()
    queue: asyncio.Queue[list[object]] = asyncio.Queue()

    backend.configure_editor_protocol("nonce123")
    backend.open("", 24, 80)
    backend.attach_readers(loop, queue)
    await asyncio.sleep(0)
    first = await drain(queue)

    ready = _editor_responses(first)
    assert ready == [
        EditorResponse(
            nonce="nonce123",
            operation=EditorOperation.READY,
            sequence=0,
            buffer_length=0,
            cursor=0,
        )
    ]

    backend.detach_readers()
    backend.attach_readers(loop, queue)
    await asyncio.sleep(0)
    second = await drain(queue)
    assert _editor_responses(second) == []


@pytest.mark.asyncio
async def test_probe_sequence_does_not_enter_line_and_reports_state(fs: MockFilesystem) -> None:
    backend, queue = make_backend(fs, nonce="nonce123")
    await asyncio.sleep(0)
    await drain(queue)

    backend.write(b"pasted text")
    backend.write(PROBE_SEQUENCE)
    await asyncio.sleep(0)
    msgs = await drain(queue)

    responses = _editor_responses(msgs)
    assert responses[-1] == EditorResponse(
        nonce="nonce123",
        operation=EditorOperation.PROBE,
        sequence=1,
        buffer_length=11,
        cursor=11,
    )

    backend.write(PROBE_SEQUENCE)
    await asyncio.sleep(0)
    msgs = await drain(queue)
    responses = _editor_responses(msgs)
    assert responses[-1].sequence == 2
    assert responses[-1].buffer_length == 11
    assert responses[-1].cursor == 11


@pytest.mark.asyncio
async def test_stash_and_restore_sequences_emit_ack_and_restore_echo_after_ack(fs: MockFilesystem) -> None:
    backend, queue = make_backend(fs, nonce="nonce123")
    await asyncio.sleep(0)
    await drain(queue)

    backend.write(b"left right")
    backend.write(b"\x1b[D" * 5)
    backend.write(STASH_SEQUENCE)
    await asyncio.sleep(0)
    stash_msgs = await drain(queue)
    stash = _editor_responses(stash_msgs)[-1]
    assert stash == EditorResponse(
        nonce="nonce123",
        operation=EditorOperation.STASH,
        sequence=1,
        buffer_length=0,
        cursor=0,
    )

    backend.write(PROBE_SEQUENCE)
    await asyncio.sleep(0)
    probe_after_stash = _editor_responses(await drain(queue))[-1]
    assert probe_after_stash.sequence == 2
    assert probe_after_stash.buffer_length == 0
    assert probe_after_stash.cursor == 0

    backend.write(RESTORE_SEQUENCE)
    await asyncio.sleep(0)
    restore_msgs = await drain(queue)
    restore_response_index = next(i for i, msg in enumerate(restore_msgs) if msg[0] == "editor_response")
    restore_stdout_index = next(i for i, msg in enumerate(restore_msgs) if msg[0] == "stdout")
    assert restore_response_index < restore_stdout_index
    restore = _editor_responses(restore_msgs)[-1]
    assert restore == EditorResponse(
        nonce="nonce123",
        operation=EditorOperation.RESTORE,
        sequence=3,
        buffer_length=10,
        cursor=5,
    )


@pytest.mark.asyncio
async def test_probe_sequence_split_at_every_boundary(fs: MockFilesystem) -> None:
    for split in range(1, len(PROBE_SEQUENCE)):
        backend, queue = make_backend(fs, nonce="nonce123")
        await asyncio.sleep(0)
        await drain(queue)
        backend.write(b"abc")
        backend.write(PROBE_SEQUENCE[:split])
        backend.write(PROBE_SEQUENCE[split:])
        await asyncio.sleep(0)
        msgs = await drain(queue)
        responses = _editor_responses(msgs)
        assert responses[-1].operation is EditorOperation.PROBE
        assert responses[-1].buffer_length == 3
        assert responses[-1].cursor == 3


@pytest.mark.asyncio
async def test_mixed_chunks_handle_requests_before_ordinary_input(fs: MockFilesystem) -> None:
    backend, queue = make_backend(fs, nonce="nonce123")
    await asyncio.sleep(0)
    await drain(queue)

    backend.write(b"ab" + PROBE_SEQUENCE + b"cd" + PROBE_SEQUENCE)
    await asyncio.sleep(0)
    msgs = await drain(queue)
    responses = _editor_responses(msgs)
    assert [response.sequence for response in responses] == [1, 2]
    assert responses[0].buffer_length == 2
    assert responses[1].buffer_length == 4


@pytest.mark.asyncio
async def test_unknown_escape_sequence_passes_through_as_editor_input(fs: MockFilesystem) -> None:
    backend, queue = make_backend(fs, nonce="nonce123")
    await asyncio.sleep(0)
    await drain(queue)

    backend.write(b"ab")
    backend.write(b"\x1b[31m")
    backend.write(PROBE_SEQUENCE)
    await asyncio.sleep(0)
    msgs = await drain(queue)

    responses = _editor_responses(msgs)
    assert responses[-1].buffer_length == 2
    assert responses[-1].cursor == 2
