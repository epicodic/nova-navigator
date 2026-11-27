"""Tests of the pseudo-terminal runner used by the install check."""

import os
import sys
from pathlib import Path

import pytest

from tools.install_check import CheckError, run_until


def _run(code: str, tmp_path: Path, timeout: float = 10.0) -> None:
    run_until([sys.executable, "-c", code], b"MARKER", env=dict(os.environ), cwd=tmp_path, timeout=timeout)


def test_succeeds_when_the_marker_appears(tmp_path: Path) -> None:
    _run("import time; print('MARKER', flush=True); time.sleep(30)", tmp_path)


@pytest.mark.parametrize(
    "code",
    [
        "print('other')",
        "print('MARKER')",
        "import time; time.sleep(30)",
        "import time; print('MARKER\\nTraceback (most recent call last):', flush=True); time.sleep(30)",
    ],
    ids=["exits-without-marker", "exits-after-marker", "times-out", "prints-traceback"],
)
def test_fails(code: str, tmp_path: Path) -> None:
    with pytest.raises(CheckError):
        _run(code, tmp_path, timeout=3.0)


def test_missing_executable_raises_check_error_without_leaking_fds(tmp_path: Path) -> None:
    before = len(os.listdir("/proc/self/fd"))
    with pytest.raises(CheckError):
        run_until(["/nonexistent/nn-install-check"], b"MARKER", env=dict(os.environ), cwd=tmp_path, timeout=3.0)
    assert len(os.listdir("/proc/self/fd")) == before


def test_marker_split_by_sgr_sequences_succeeds(tmp_path: Path) -> None:
    _run("import time; print('MAR\\x1b[0mKER', flush=True); time.sleep(30)", tmp_path)


def test_coloured_traceback_fails(tmp_path: Path) -> None:
    code = "import time; print('\\x1b[31mTraceback (most recent call last):\\x1b[0m', flush=True); time.sleep(30)"
    with pytest.raises(CheckError):
        _run(code, tmp_path, timeout=3.0)
