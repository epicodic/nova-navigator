"""Verify that the built distribution installs and starts: ``uv run install_check [--dist DIR]``."""

import argparse
import contextlib
import fcntl
import os
import pty
import select
import signal
import struct
import subprocess
import sys
import tempfile
import termios
import time
import tomllib
from pathlib import Path

_ROOT = Path(__file__).parent.parent.parent
_SETTLE_SECONDS = 1.0

_PROBE = """
import sys
from importlib import import_module, metadata
from importlib.util import find_spec
from pathlib import Path

def installed(name):
    try:
        return find_spec(name) is not None
    except ModuleNotFoundError:
        return False

dist = metadata.distribution("nova-navigator")
commands = {e.name for e in dist.entry_points if e.group == "console_scripts"}
assert commands == {"nn", "ned"}, commands
assert dist.version == sys.argv[1], dist.version
for name in ("tools", "debugpy", "azure.identity", "azure.storage.blob"):
    assert not installed(name), name + " must not be installed"
for name in ("nova_navigator.main", "nova_editor.app", "nova_widgets"):
    import_module(name)

import nova_navigator
package = Path(nova_navigator.__file__).parent
assert package.is_relative_to(sys.prefix), package
for data in ("_default/icons.csv", "_default/usermenu.toml", "nn.tcss"):
    assert (package / data).is_file(), data + " is missing"

from nova_navigator.vfs.filesystems import AzureFilesystem, MissingAzureExtraError
try:
    AzureFilesystem("https://account.blob.core.windows.net", "container")
except MissingAzureExtraError:
    pass
else:
    raise AssertionError("expected MissingAzureExtraError")
"""


class CheckError(Exception):
    """The distribution failed the install check."""


def run_until(argv: list[str], marker: bytes, *, env: dict[str, str], cwd: Path, timeout: float) -> None:
    """Run *argv* in a pseudo-terminal until *marker* is printed, then kill its process group.

    Raises:
        CheckError: If the process exits, times out or prints a traceback.
    """
    master, slave = pty.openpty()
    fcntl.ioctl(slave, termios.TIOCSWINSZ, struct.pack("HHHH", 40, 120, 0, 0))
    process = subprocess.Popen(argv, stdin=slave, stdout=slave, stderr=slave, env=env, cwd=cwd, start_new_session=True)
    os.close(slave)
    output = bytearray()
    deadline = time.monotonic() + timeout
    seen = False
    try:
        while time.monotonic() < deadline:
            if select.select([master], [], [], 0.1)[0]:
                try:
                    chunk = os.read(master, 65536)
                except OSError:
                    chunk = b""
                if not chunk:
                    break
                output += chunk
            elif process.poll() is not None:
                break
            if not seen and marker in output:
                seen = True
                deadline = time.monotonic() + _SETTLE_SECONDS
        else:
            if seen and process.poll() is None and b"Traceback" not in output:
                return
        tail = bytes(output[-3000:]).decode(errors="replace")
        raise CheckError(f"{argv[0]}: no healthy start (marker seen: {seen}, exit code: {process.poll()})\n{tail}")
    finally:
        _kill_group(process)
        os.close(master)


def _kill_group(process: subprocess.Popen[bytes]) -> None:
    """Kill the process group (the app and any helpers it spawned) and reap the leader."""
    with contextlib.suppress(ProcessLookupError):
        os.killpg(process.pid, signal.SIGKILL)
    process.wait()


def _run(argv: list[str], *, env: dict[str, str] | None = None, cwd: Path | None = None) -> None:
    result = subprocess.run(argv, env=env, cwd=cwd, capture_output=True, text=True, check=False)
    if result.returncode != 0:
        raise CheckError(f"{' '.join(argv[:4])} failed:\n{result.stdout}{result.stderr}")


def _check_artifact(artifact: Path, work: Path, version: str) -> None:
    venv = work / "venv"
    python = venv / "bin" / "python"
    _run(["uv", "venv", "--python", f"{sys.version_info.major}.{sys.version_info.minor}", str(venv)], cwd=work)
    _run(["uv", "pip", "install", "--python", str(python), str(artifact)], cwd=work)

    home = work / "home"
    files = work / "files"
    home.mkdir()
    files.mkdir()
    env = {
        "HOME": str(home),
        "PATH": f"{venv / 'bin'}:/usr/bin:/bin",
        "TERM": "xterm-256color",
        "LANG": "C.UTF-8",
        "SHELL": "/bin/bash",
    }
    _run([str(python), "-I", "-c", _PROBE, version], env=env, cwd=files)

    (files / "NNCHECK").touch()
    run_until([str(venv / "bin" / "nn")], b"NNCHECK", env=env, cwd=files, timeout=60)
    sample = files / "sample.txt"
    sample.write_text("first line\nNEDCHECKCONTENT\n", encoding="utf-8")
    run_until([str(venv / "bin" / "ned"), str(sample)], b"NEDCHECKCONTENT", env=env, cwd=files, timeout=60)


def _find_one(dist: Path, pattern: str) -> Path:
    matches = sorted(dist.glob(pattern))
    if len(matches) != 1:
        raise CheckError(f"expected exactly one {pattern} in {dist}, found {len(matches)}")
    return matches[0].resolve()


def main() -> None:
    parser = argparse.ArgumentParser(description="Check that the wheel and the sdist install and start.")
    parser.add_argument("--dist", type=Path, help="directory with an existing build; default: build now")
    args = parser.parse_args()
    version = tomllib.loads((_ROOT / "pyproject.toml").read_text(encoding="utf-8"))["project"]["version"]
    try:
        with tempfile.TemporaryDirectory(prefix="nn-install-check-") as tmp:
            dist: Path = args.dist or Path(tmp) / "dist"
            if args.dist is None:
                _run(["uv", "build", "--out-dir", str(dist)], cwd=_ROOT)
            for kind, pattern in (("wheel", "*.whl"), ("sdist", "*.tar.gz")):
                work = Path(tmp) / kind
                work.mkdir()
                _check_artifact(_find_one(dist, pattern), work, version)
                sys.stdout.write(f"{kind}: OK\n")
    except CheckError as error:
        sys.stderr.write(f"install check failed: {error}\n")
        sys.exit(1)


if __name__ == "__main__":
    main()
