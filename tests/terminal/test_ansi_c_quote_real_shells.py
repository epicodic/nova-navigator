"""Real-shell checks that driver-quoted long paths reach the shell unchanged."""

from __future__ import annotations

import os
import shutil
import subprocess
from pathlib import Path

import pytest

from nova_navigator.terminal.shell_driver import BashDriver, ShellDriver, ZshDriver

# Quoted form must reach the continuation limit so the line-split code path runs.
_MIN_QUOTED_LENGTH = 250

# Spaces, non-ASCII, a quote, a backslash, a dollar sign and a backtick.
_TRICKY_PATH = '/home/user/Дом dir/it\'s "big" \\ $HOME `id` ü/' + "ä b'c$d`e\\f " * 12


@pytest.mark.parametrize(
    ("driver", "shell_name", "shell_args"),
    [
        pytest.param(BashDriver(), "bash", ["--norc", "--noprofile"], id="bash"),
        pytest.param(ZshDriver(), "zsh", ["-f"], id="zsh"),
    ],
)
def test_long_quoted_path_round_trips_through_real_shell(
    tmp_path: Path,
    driver: ShellDriver,
    shell_name: str,
    shell_args: list[str],
) -> None:
    shell = shutil.which(shell_name)
    assert shell is not None, f"{shell_name} is required for this test"
    quoted = driver.quote(_TRICKY_PATH)
    assert len(quoted) >= _MIN_QUOTED_LENGTH

    home = tmp_path / "home"
    home.mkdir()
    env = {"PATH": os.environ.get("PATH", ""), "HOME": str(home), "TERM": "dumb"}
    result = subprocess.run(
        [shell, *shell_args, "-c", f"printf %s {quoted}"],
        capture_output=True,
        env=env,
        timeout=30,
        check=False,
    )

    assert result.returncode == 0, result.stderr
    assert result.stdout == _TRICKY_PATH.encode("utf-8")
