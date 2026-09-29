"""REQ-14 detection: truncating the file underneath the source never ends the process."""

from __future__ import annotations

import os
import subprocess
import sys
import threading
from pathlib import Path

import pytest

CHILD = Path(__file__).with_name("trunc_child.py")
SIZE = 8 * 1024 * 1024
CHILD_TIMEOUT = 60.0


@pytest.mark.parametrize("mode", ["stat-and-short-read", "short-read-only"])
@pytest.mark.parametrize("new_size", [0, 1024 * 1024])
def test_truncation_raises_source_changed_and_process_survives(tmp_path: Path, mode: str, new_size: int) -> None:
    path = tmp_path / "data.bin"
    path.write_bytes(os.urandom(SIZE))
    child = subprocess.Popen([sys.executable, str(CHILD), mode, str(path)], stdin=subprocess.PIPE, stdout=subprocess.PIPE, text=True)
    watchdog = threading.Timer(CHILD_TIMEOUT, child.kill)  # a hung child ends the blocking readline below
    watchdog.start()
    try:
        assert child.stdout is not None
        assert child.stdin is not None
        assert child.stdout.readline().strip() == "READY"
        os.truncate(path, new_size)
        child.stdin.write("go\n")
        child.stdin.flush()
        assert child.stdout.readline().strip() == "SOURCECHANGED"
        assert child.wait(timeout=30) == 0  # exit code 0: no SIGBUS (-7) or other signal
    finally:
        watchdog.cancel()
        child.kill()
        child.wait(timeout=30)
        for pipe in (child.stdin, child.stdout):
            if pipe is not None:
                pipe.close()
