from __future__ import annotations

import os
import time
from pathlib import Path

from nova_navigator.vfs.filesystems import LocalFilesystem
from nova_navigator.vfs.types import OUTPUT_TAIL_LIMIT

_fs = LocalFilesystem.singleton()


def test_local_scheme_and_capability() -> None:
    assert _fs.scheme == "local"
    assert _fs.capabilities.commands is True


def test_exec_command_returns_exit_code_and_merged_output(tmp_path: Path) -> None:
    result = _fs.exec_command("echo out; echo err >&2; exit 4", _fs.path(tmp_path))
    assert result.exit_code == 4
    assert "out" in result.output
    assert "err" in result.output


def test_exec_command_runs_in_cwd(tmp_path: Path) -> None:
    result = _fs.exec_command("pwd", _fs.path(tmp_path))
    assert result.output.strip() == os.path.realpath(tmp_path)


def test_exec_command_closes_stdin(tmp_path: Path) -> None:
    start = time.monotonic()
    result = _fs.exec_command("read x; echo got:$x", _fs.path(tmp_path))
    assert time.monotonic() - start < 5
    assert "got:" in result.output


def test_exec_command_keeps_only_output_tail(tmp_path: Path) -> None:
    result = _fs.exec_command("head -c 100000 /dev/zero | tr '\\0' a", _fs.path(tmp_path))
    assert len(result.output) == OUTPUT_TAIL_LIMIT


def test_exec_command_cancel_terminates_process(tmp_path: Path) -> None:
    start = time.monotonic()
    result = _fs.exec_command("sleep 10", _fs.path(tmp_path), should_cancel=lambda: True)
    assert result.exit_code == -1
    assert time.monotonic() - start < 5


def test_exec_command_cancel_kills_process_ignoring_sigterm(tmp_path: Path) -> None:
    start = time.monotonic()
    result = _fs.exec_command("trap '' TERM; sleep 30", _fs.path(tmp_path), should_cancel=lambda: True)
    assert result.exit_code == -1
    assert time.monotonic() - start < 10
