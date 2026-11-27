"""Plain ``docker`` CLI helpers that start the real servers of the tests.

Run ``uv run python -m tests.real_servers.docker prepare`` once before the tests to build the images.
"""

from __future__ import annotations

import functools
import os
import subprocess
import sys
import threading
from collections.abc import Callable, Mapping
from pathlib import Path
from typing import NoReturn

import pytest

# Captured at import: tests change HOME, but the docker CLI needs the real one.
DOCKER_ENV: dict[str, str] = dict(os.environ)
DOCKER_COMMAND = "docker"
CALL_TIMEOUT = 90.0  # below the 120 s per-test limit
PREPARE_TIMEOUT = 600.0
AZURITE_IMAGE = "mcr.microsoft.com/azure-storage/azurite:3.38.0@sha256:c26024d62bf4a5b88a40968afd217234c00e1fc9a4790dc6105d1ca7979f6eec"
IMAGES_DIR = Path(__file__).resolve().parent / "images"


class ServerUnavailable(Exception):
    """A Docker call failed, timed out or the docker binary is missing."""


def start_once[T](start: Callable[[], T]) -> Callable[[], T]:
    """Call *start* at most once per process and share its result.

    A ServerUnavailable is remembered too: later calls re-raise it without starting again, so a broken
    server start costs one attempt instead of one per test. Other errors are not remembered and retry.
    """
    outcome: list[T | ServerUnavailable] = []
    lock = threading.Lock()

    @functools.wraps(start)
    def _shared() -> T:
        with lock:
            if not outcome:
                try:
                    outcome.append(start())
                except ServerUnavailable as exc:
                    outcome.append(exc)
        result = outcome[0]
        if isinstance(result, ServerUnavailable):
            raise result
        return result

    return _shared


def run_docker(*args: str, input: bytes | None = None, timeout: float = CALL_TIMEOUT) -> bytes:
    """Run ``docker <args>`` and return its stdout; any failure raises `ServerUnavailable` naming the step."""
    step = " ".join(args[:1]) or "docker"
    try:
        result = subprocess.run(
            [DOCKER_COMMAND, *args],
            input=input,
            capture_output=True,
            timeout=timeout,
            env=DOCKER_ENV,
            check=False,
        )
    except subprocess.TimeoutExpired as exc:
        raise ServerUnavailable(f"docker {step} timed out after {timeout:g} s") from exc
    except OSError as exc:
        raise ServerUnavailable(f"docker {step} could not be started: {exc}") from exc
    if result.returncode != 0:
        stderr = result.stderr.decode(errors="replace").strip()
        raise ServerUnavailable(f"docker {step} failed with exit code {result.returncode}: {stderr}")
    return result.stdout


def unavailable(reason: str, env: Mapping[str, str]) -> NoReturn:
    """Fail when the run requires real servers (CI or ``NOVA_REQUIRE_REAL_SERVERS``), otherwise skip with the reason."""
    if env.get("CI") or env.get("NOVA_REQUIRE_REAL_SERVERS"):
        pytest.fail(f"Real server required but unavailable: {reason}")
    pytest.skip(f"Docker is not available: {reason}")


def build_image(flavour: str, timeout: float = CALL_TIMEOUT) -> str:
    """Build the image of *flavour* (``gnu`` or ``busybox``) and return its tag."""
    tag = f"nova-rs-{flavour}"
    run_docker("build", "--quiet", "-t", tag, str(IMAGES_DIR / flavour), timeout=timeout)
    return tag


def pull_azurite(timeout: float = CALL_TIMEOUT) -> None:
    """Pull the pinned Azurite image unless it is already present."""
    try:
        run_docker("image", "inspect", AZURITE_IMAGE)
    except ServerUnavailable:
        run_docker("pull", AZURITE_IMAGE, timeout=timeout)


def prepare() -> None:
    """Check the daemon, build both SSH images, print their OpenSSH versions and pull Azurite."""
    run_docker("info", timeout=PREPARE_TIMEOUT)
    for flavour, version_command in (("gnu", "dpkg-query -W openssh-server"), ("busybox", "apk info -v openssh-server")):
        tag = build_image(flavour, timeout=PREPARE_TIMEOUT)
        version = run_docker("run", "--rm", tag, "sh", "-c", version_command, timeout=PREPARE_TIMEOUT)
        print(f"{tag}: {version.decode().strip()}")
    pull_azurite(timeout=PREPARE_TIMEOUT)
    print(f"azurite: {AZURITE_IMAGE}")


if __name__ == "__main__":
    if sys.argv[1:] != ["prepare"]:
        raise SystemExit("usage: python -m tests.real_servers.docker prepare")
    prepare()
