"""CommandRunner — run shell commands in a terminal or as a background job.

The module has no Textual imports: terminals and the job list are reached
through small protocols implemented by the UI layer.
"""

from __future__ import annotations

import asyncio
import shlex
from dataclasses import dataclass
from enum import StrEnum
from pathlib import PurePath
from typing import Protocol

from nova_navigator.scheduler import Job
from nova_navigator.scheduler.context import GuiRequestCallback, TaskContext
from nova_navigator.vfs import Filesystem, VPath
from nova_navigator.vfs.types import ExecResult

from .errors import CommandCancelledError, CommandFailedError, CommandNotSupportedError


class CommandMode(StrEnum):
    """Where a command runs."""

    TERMINAL = "terminal"
    BACKGROUND = "background"


@dataclass(frozen=True)
class Command:
    """A final shell script to run.

    ``script`` is already expanded and quoted.
    The filesystem of ``cwd`` decides where the command runs.
    ``label`` names the job and notifications.
    """

    script: str
    cwd: VPath
    label: str


@dataclass(frozen=True)
class CommandResult:
    """Outcome of a command.

    ``exit_code`` is ``None`` when it is unknown (terminal mode).
    ``output`` is the tail of stdout and stderr (background mode) or ``""``.
    """

    exit_code: int | None
    output: str


class CommandTerminal(Protocol):
    """The part of a terminal widget the runner needs."""

    @property
    def cwd(self) -> PurePath | None: ...

    async def run_command(self, line: str) -> None: ...


class TerminalProvider(Protocol):
    """Supplies the terminal that runs commands for a filesystem."""

    async def terminal_for(self, fs: Filesystem) -> CommandTerminal | None: ...


class JobSink(Protocol):
    """Receives background jobs so the UI can list and cancel them."""

    def add_job(self, job: Job) -> None: ...


def terminal_line(script: str, cwd: PurePath, terminal_cwd: PurePath | None) -> str:
    """Return the line to type into a shell to run *script* in *cwd*.

    A multi-line script is wrapped in ``sh -c``.
    A ``cd`` is prepended when the shell is not already in *cwd*.
    """
    line = script.strip()
    if "\n" in line:
        line = f"sh -c {shlex.quote(line)}"
    if terminal_cwd != cwd:
        line = f"cd {shlex.quote(str(cwd))} && {line}"
    return line


class CommandRunner:
    """Runs a :class:`Command` in a terminal or as a background job.

    The runner never refreshes panels, shows dialogs or expands templates; callers do.
    """

    def __init__(
        self,
        terminals: TerminalProvider,
        jobs: JobSink,
        request_callback: GuiRequestCallback,
    ) -> None:
        self._terminals = terminals
        self._jobs = jobs
        self._request_callback = request_callback

    def can_run(self, cwd: VPath, mode: CommandMode) -> bool:
        """Return True if commands can run in *cwd* in *mode*."""
        return cwd.filesystem.capabilities.commands

    async def run(self, command: Command, mode: CommandMode) -> CommandResult:
        """Run *command* in *mode* and return once it has finished.

        Raises:
            CommandNotSupportedError: The filesystem cannot run commands.
            TerminalBusyError: Terminal mode and the terminal is busy.
            CommandCancelledError: Background mode and the user cancelled the job.
            CommandFailedError: Background mode and the command could not be run.
        """
        if not self.can_run(command.cwd, mode):
            raise CommandNotSupportedError(f"Commands cannot run on {command.cwd.uri}")
        if mode is CommandMode.TERMINAL:
            return await self._run_in_terminal(command)
        return await self._run_in_background(command)

    async def _run_in_terminal(self, command: Command) -> CommandResult:
        terminal = await self._terminals.terminal_for(command.cwd.filesystem)
        if terminal is None:
            raise CommandNotSupportedError(f"No terminal available for {command.cwd.uri}")
        await terminal.run_command(terminal_line(command.script, command.cwd.path, terminal.cwd))
        return CommandResult(exit_code=None, output="")

    async def _run_in_background(self, command: Command) -> CommandResult:
        fs = command.cwd.filesystem
        results: list[ExecResult] = []

        async def _task(ctx: TaskContext) -> None:
            ctx.status.update_progress(inc_total=1)
            cancel_event = ctx.status.cancel_event

            def _should_cancel() -> bool:
                return cancel_event is not None and cancel_event.is_set()

            results.append(await asyncio.to_thread(fs.exec_command, command.script, command.cwd, _should_cancel))
            ctx.status.check_cancelled()
            ctx.status.update_progress(inc_completed=1)

        job = Job(command.label, _task)
        self._jobs.add_job(job)
        await job.start(self._request_callback)
        if job.state is Job.State.CANCELED:
            raise CommandCancelledError(f"{command.label} was cancelled")
        if not results:
            raise CommandFailedError(job.error or f"{command.label} failed")
        return CommandResult(exit_code=results[0].exit_code, output=results[0].output)
