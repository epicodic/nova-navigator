"""Tests for VirtualPtyBackend."""

from __future__ import annotations

import asyncio
from typing import Any

import pytest

from nova_navigator.terminal.shell_driver import RESTORE_SEQUENCE, STASH_SEQUENCE
from nova_navigator.terminal.vfs_shell.virtual_pty_backend import VfsShellDriver, VirtualPtyBackend
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


# ---------------------------------------------------------------------------
# VfsShellDriver
# ---------------------------------------------------------------------------


def test_vfs_shell_driver_supports_line_editing() -> None:
    assert VfsShellDriver().supports_line_editing is True


def test_vfs_shell_driver_supports_editor_protocol() -> None:
    assert VfsShellDriver().supports_editor_protocol is True


# ---------------------------------------------------------------------------
# Stash / restore protocol
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_stash_sequence_clears_the_line_editor_buffer(fs: MockFilesystem) -> None:
    backend, queue = make_backend(fs)
    await asyncio.sleep(0)
    await drain(queue)

    backend.write(b"typed but not submitted")
    assert backend._line_editor is not None
    assert backend._line_editor.line == "typed but not submitted"

    backend.write(STASH_SEQUENCE)
    assert backend._line_editor.line == ""
    backend.teardown()


@pytest.mark.asyncio
async def test_restore_sequence_puts_back_the_stashed_line(fs: MockFilesystem) -> None:
    backend, queue = make_backend(fs)
    await asyncio.sleep(0)
    await drain(queue)

    backend.write(b"typed")
    backend.write(STASH_SEQUENCE)
    backend.write(RESTORE_SEQUENCE)

    assert backend._line_editor is not None
    assert backend._line_editor.line == "typed"
    backend.teardown()


@pytest.mark.asyncio
async def test_restore_sequence_echoes_restored_text_to_stdout(fs: MockFilesystem) -> None:
    backend, queue = make_backend(fs)
    await asyncio.sleep(0)
    await drain(queue)

    backend.write(b"typed")
    await asyncio.sleep(0)
    await drain(queue)  # discard the per-character typing echoes
    backend.write(STASH_SEQUENCE)
    backend.write(RESTORE_SEQUENCE)
    await asyncio.sleep(0)

    msgs = await drain(queue)
    stdout_text = "".join(str(m[1]) for m in msgs if m[0] == "stdout")
    assert "typed" in stdout_text
    backend.teardown()


@pytest.mark.asyncio
async def test_restore_without_a_prior_stash_is_a_noop(fs: MockFilesystem) -> None:
    backend, queue = make_backend(fs)
    await asyncio.sleep(0)
    await drain(queue)

    backend.write(RESTORE_SEQUENCE)

    assert backend._line_editor is not None
    assert backend._line_editor.line == ""
    backend.teardown()


@pytest.mark.asyncio
async def test_stash_sequence_split_across_writes_is_still_recognized(fs: MockFilesystem) -> None:
    backend, queue = make_backend(fs)
    await asyncio.sleep(0)
    await drain(queue)

    backend.write(b"typed")
    backend.write(STASH_SEQUENCE[:2])
    backend.write(STASH_SEQUENCE[2:])

    assert backend._line_editor is not None
    assert backend._line_editor.line == ""
    backend.teardown()


@pytest.mark.asyncio
async def test_stash_sequence_after_ordinary_text_in_same_write_call(fs: MockFilesystem) -> None:
    backend, queue = make_backend(fs)
    await asyncio.sleep(0)
    await drain(queue)

    backend.write(b"ab" + STASH_SEQUENCE)
    assert backend._line_editor is not None
    assert backend._line_editor.line == ""

    backend.write(RESTORE_SEQUENCE)
    assert backend._line_editor.line == "ab"
    backend.teardown()


@pytest.mark.asyncio
async def test_resize_updates_dimensions(fs: MockFilesystem) -> None:
    backend, _queue = make_backend(fs)
    await asyncio.sleep(0)
    backend.resize(40, 120)
    assert backend._rows == 40
    assert backend._cols == 120
    backend.teardown()
