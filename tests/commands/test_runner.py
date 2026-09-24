from __future__ import annotations

import asyncio
from pathlib import Path, PurePath

import pytest

from nova_navigator.commands import (
    Command,
    CommandCancelledError,
    CommandMode,
    CommandNotSupportedError,
    CommandRunner,
    TerminalBusyError,
    terminal_line,
)
from nova_navigator.response import Response
from nova_navigator.scheduler import Job, ResponseRequest
from nova_navigator.vfs import Filesystem
from nova_navigator.vfs.filesystems import LocalFilesystem
from tests._utils.mock_filesystem import MockFilesystem

_fs = LocalFilesystem.singleton()


class FakeTerminal:
    def __init__(self, cwd: PurePath | None, busy: bool = False) -> None:
        self._cwd = cwd
        self._busy = busy
        self.lines: list[str] = []

    @property
    def cwd(self) -> PurePath | None:
        return self._cwd

    async def run_command(self, line: str) -> None:
        if self._busy:
            raise TerminalBusyError("busy")
        self.lines.append(line)


class FakeTerminals:
    def __init__(self, terminal: FakeTerminal | None) -> None:
        self._terminal = terminal

    async def terminal_for(self, fs: Filesystem) -> FakeTerminal | None:
        return self._terminal


class FakeJobs:
    def __init__(self) -> None:
        self.jobs: list[Job] = []

    def add_job(self, job: Job) -> None:
        self.jobs.append(job)


async def _no_requests(_request: ResponseRequest, _future: asyncio.Future[Response]) -> None:
    raise AssertionError("no response requests expected")


def _runner(terminal: FakeTerminal | None = None, jobs: FakeJobs | None = None) -> CommandRunner:
    return CommandRunner(FakeTerminals(terminal), jobs or FakeJobs(), _no_requests)


def test_terminal_line_single_line_is_sent_unchanged() -> None:
    assert terminal_line("make all\n", PurePath("/w"), PurePath("/w")) == "make all"


def test_terminal_line_wraps_multi_line_script() -> None:
    assert terminal_line("a\nb", PurePath("/w"), PurePath("/w")) == "sh -c 'a\nb'"


def test_terminal_line_prepends_cd_when_cwd_differs() -> None:
    assert terminal_line("make", PurePath("/my dir"), PurePath("/other")) == "cd '/my dir' && make"


def test_can_run_depends_on_commands_capability(tmp_path: Path) -> None:
    runner = _runner()
    assert runner.can_run(_fs.path(tmp_path), CommandMode.TERMINAL) is True
    mock = MockFilesystem()
    assert runner.can_run(mock.path("/"), CommandMode.BACKGROUND) is False


@pytest.mark.asyncio
async def test_run_in_terminal_sends_line(tmp_path: Path) -> None:
    terminal = FakeTerminal(PurePath(tmp_path))
    result = await _runner(terminal).run(Command("make", _fs.path(tmp_path), "Make"), CommandMode.TERMINAL)
    assert terminal.lines == ["make"]
    assert result.exit_code is None
    assert result.output == ""


@pytest.mark.asyncio
async def test_run_in_terminal_propagates_busy(tmp_path: Path) -> None:
    runner = _runner(FakeTerminal(PurePath(tmp_path), busy=True))
    with pytest.raises(TerminalBusyError):
        await runner.run(Command("make", _fs.path(tmp_path), "Make"), CommandMode.TERMINAL)


@pytest.mark.asyncio
async def test_run_without_terminal_is_not_supported(tmp_path: Path) -> None:
    with pytest.raises(CommandNotSupportedError):
        await _runner(None).run(Command("make", _fs.path(tmp_path), "Make"), CommandMode.TERMINAL)


@pytest.mark.asyncio
async def test_run_on_filesystem_without_commands_is_not_supported() -> None:
    mock = MockFilesystem()
    with pytest.raises(CommandNotSupportedError):
        await _runner().run(Command("ls", mock.path("/"), "List"), CommandMode.BACKGROUND)


@pytest.mark.asyncio
async def test_run_in_background_returns_result_and_registers_job(tmp_path: Path) -> None:
    jobs = FakeJobs()
    result = await _runner(jobs=jobs).run(Command("echo hi; exit 3", _fs.path(tmp_path), "Echo"), CommandMode.BACKGROUND)
    assert result.exit_code == 3
    assert result.output.strip() == "hi"
    assert [job.title for job in jobs.jobs] == ["Echo"]


@pytest.mark.asyncio
async def test_run_in_background_cancel_raises(tmp_path: Path) -> None:
    jobs = FakeJobs()
    task = asyncio.ensure_future(_runner(jobs=jobs).run(Command("sleep 10", _fs.path(tmp_path), "Sleep"), CommandMode.BACKGROUND))
    for _ in range(100):
        if jobs.jobs:
            break
        await asyncio.sleep(0.01)
    jobs.jobs[0].cancel()
    with pytest.raises(CommandCancelledError):
        await asyncio.wait_for(task, timeout=5)
