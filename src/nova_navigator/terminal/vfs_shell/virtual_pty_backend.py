"""Virtual PTY backend — terminal emulation for non-native filesystems."""

from __future__ import annotations

import asyncio
import shlex
from typing import Any

from nova_navigator.terminal.pty_backend import PtyBackend
from nova_navigator.terminal.shell_driver import ShellDriver
from nova_navigator.terminal.vfs_shell.completer import TabCompleter
from nova_navigator.terminal.vfs_shell.interpreter import VfsShellInterpreter
from nova_navigator.terminal.vfs_shell.line_editor import LineEditor, LineEditorEvent
from nova_navigator.vfs.filesystem import Filesystem
from nova_navigator.vfs.vpath import VPath


class VfsShellDriver(ShellDriver):
    """Shell driver for the VFS virtual backend.

    No init code is injected — the virtual shell handles commands directly
    and does not need shell hooks.  Uses :mod:`shlex` for safe quoting.
    """

    def __init__(self) -> None:
        super().__init__(prompt_ready=False)

    def init_code(self) -> str:
        return ""

    def quote(self, arg: str) -> str:
        return shlex.quote(arg)


class VirtualPtyBackend(PtyBackend):
    """A virtual PTY backend that emulates a shell over a VFS.

    Unlike ``LocalPtyBackend``, this backend does not fork a process.
    Instead it drives a ``VfsShellInterpreter`` to handle commands directly.

    Lifecycle: ``attach_readers()`` → ``open()`` → (normal operation) → ``teardown()``.
    The ordering of ``attach_readers`` and ``open`` does not matter; the initial
    prompt is posted whichever call happens second.
    """

    def __init__(self, filesystem: Filesystem, cwd: VPath) -> None:
        super().__init__()
        self._filesystem = filesystem
        self._initial_cwd = cwd
        self._loop: asyncio.AbstractEventLoop | None = None
        self._recv_queue: asyncio.Queue[list[object]] | None = None
        self._cols: int = 80
        self._rows: int = 24
        self._interpreter: VfsShellInterpreter | None = None
        self._line_editor: LineEditor | None = None
        self._completer: TabCompleter | None = None
        self._tab_candidates: list[str] = []
        self._tab_index: int = 0
        self._tab_word_start: int = 0
        self._tab_word_end: int = 0
        self._tab_cursor_pos: int = 0
        self._running: bool = False
        self._command_task: asyncio.Task[Any] | None = None

    # ------------------------------------------------------------------
    # PtyBackend ABC
    # ------------------------------------------------------------------

    def attach_readers(
        self,
        loop: asyncio.AbstractEventLoop,
        recv_queue: asyncio.Queue[list[object]],
    ) -> None:
        self._loop = loop
        self._recv_queue = recv_queue
        # If open() was already called, post the initial prompt now.
        if self._running and self._interpreter is not None:
            self._post_initial_prompt()

    def detach_readers(self) -> None:
        self._loop = None
        self._recv_queue = None

    def open(self, command: str, rows: int, cols: int) -> int | None:
        """Start the virtual shell.

        Args:
            command: Ignored for the virtual backend.
            rows: Initial terminal height.
            cols: Initial terminal width.

        Returns:
            Always ``None``.
        """
        self._rows = rows
        self._cols = cols
        interpreter = VfsShellInterpreter(
            self._filesystem,
            self._initial_cwd,
            cols=self._cols,
            rows=self._rows,
        )
        self._interpreter = interpreter
        self._line_editor = LineEditor()
        self._completer = TabCompleter(
            interpreter.registry,
            self._filesystem,
            lambda: interpreter.cwd,
        )
        self._running = True
        # If attach_readers() was already called, post the initial prompt now.
        if self._loop is not None and self._recv_queue is not None:
            self._post_initial_prompt()
        return None

    def write(self, data: bytes) -> None:
        """Feed raw bytes from the terminal into the virtual shell."""
        if not self._running or self._line_editor is None or self._interpreter is None:
            return

        text = data.decode("utf-8", errors="replace")
        for char in text:
            event = self._line_editor.feed(char)
            echo = self._line_editor.echo
            if echo:
                self._post_stdout(echo)

            if event == LineEditorEvent.TAB:
                self._schedule_tab()
                return

            # Any non-tab input clears the tab cycling state
            if self._tab_candidates:
                self._tab_candidates = []

            if event == LineEditorEvent.COMPLETE_LINE:
                line = self._line_editor.line
                self._line_editor.add_to_history(line)
                self._line_editor.reset()
                self._schedule_command(line)
                return

            if event == LineEditorEvent.INTERRUPT:
                if self._command_task is not None and not self._command_task.done():
                    self._interpreter.cancel()
                else:
                    # "^C" already echoed; just add a newline and re-prompt.
                    self._post_stdout("\r\n")
                    self._line_editor.reset()
                    self._post_stdout(self._interpreter.prompt)
                    self._post_message(["prompt_ready"])
                return

            if event == LineEditorEvent.EOF:
                self._post_message(["disconnect", 0])
                return

    def resize(self, rows: int, cols: int) -> None:
        self._rows = rows
        self._cols = cols
        if self._interpreter is not None:
            self._interpreter.cols = cols
            self._interpreter.rows = rows

    def resume(self) -> None:
        """No-op: virtual backend has no process to resume."""

    def teardown(self) -> None:
        """Stop the virtual shell."""
        self._running = False
        if self._interpreter is not None:
            self._interpreter.cancel()

    # ------------------------------------------------------------------
    # Internal helpers
    # ------------------------------------------------------------------

    def _post_initial_prompt(self) -> None:
        """Post pre_cmd, prompt text, and prompt_ready to the recv queue."""
        assert self._interpreter is not None
        cwd = self._interpreter.cwd
        self._post_message(["pre_cmd", str(cwd.path), True])
        self._post_stdout(self._interpreter.prompt)
        self._post_message(["prompt_ready"])

    def _schedule_command(self, line: str) -> None:
        assert self._loop is not None
        self._loop.call_soon_threadsafe(self._loop.create_task, self._run_command(line))

    def _schedule_tab(self) -> None:
        assert self._loop is not None
        self._loop.call_soon_threadsafe(self._loop.create_task, self._handle_tab())

    async def _handle_tab(self) -> None:
        """Handle tab completion: first tab fetches candidates, subsequent tabs cycle."""
        assert self._line_editor is not None
        assert self._completer is not None

        if self._tab_candidates:
            # Cycle to next candidate. Keep the cursor where the user stopped
            # typing rather than at the end of the completed candidate.
            self._tab_index = (self._tab_index + 1) % len(self._tab_candidates)
            candidate = self._tab_candidates[self._tab_index]
            echo = self._line_editor.replace_word(self._tab_word_start, self._tab_word_end, candidate, cursor_pos=self._tab_cursor_pos)
            self._tab_word_end = self._tab_word_start + len(candidate)
            if echo:
                self._post_stdout(echo)
            return

        # First tab: fetch candidates
        line = self._line_editor.line
        cursor = self._line_editor.cursor
        candidates = await self._completer.complete(line, cursor)
        if not candidates:
            return

        start, end = self._completer.word_boundaries(line, cursor)
        self._tab_candidates = candidates
        self._tab_index = 0
        self._tab_word_start = start
        self._tab_word_end = end
        self._tab_cursor_pos = cursor

        candidate = candidates[0]
        echo = self._line_editor.replace_word(start, end, candidate, cursor_pos=cursor)
        self._tab_word_end = start + len(candidate)
        if echo:
            self._post_stdout(echo)

    async def _run_command(self, line: str) -> None:
        assert self._interpreter is not None
        assert self._line_editor is not None

        def write_fn(text: str) -> None:
            self._post_stdout(text)

        def write_error_fn(text: str) -> None:
            self._post_stdout(text)

        await self._interpreter.execute(line, write_fn, write_error_fn, history=self._line_editor.history)

        cwd = self._interpreter.cwd
        self._post_message(["pre_cmd", str(cwd.path), True])
        self._post_stdout(self._interpreter.prompt)
        self._post_message(["prompt_ready"])

    def _post_stdout(self, text: str) -> None:
        self._post_message(["stdout", text])

    def _post_message(self, msg: list[object]) -> None:
        assert self._loop is not None
        assert self._recv_queue is not None
        self._loop.call_soon_threadsafe(self._recv_queue.put_nowait, msg)
