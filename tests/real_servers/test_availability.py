"""Docker availability: a missing server skips locally and fails when the run requires it."""

from __future__ import annotations

import stat
import sys
from pathlib import Path

import pytest

from tests.real_servers import docker
from tests.real_servers.docker import ServerUnavailable, build_image, run_docker, unavailable


@pytest.mark.parametrize("variable", ["CI", "NOVA_REQUIRE_REAL_SERVERS"])
def test_unavailable_server_fails_when_required(variable: str) -> None:
    with pytest.raises(pytest.fail.Exception, match="docker daemon not reachable"):
        unavailable("docker daemon not reachable", {variable: "1"})


def test_unavailable_server_is_skipped_with_the_reason() -> None:
    with pytest.raises(pytest.skip.Exception, match="Real server unavailable: docker daemon not reachable"):
        unavailable("docker daemon not reachable", {})


def test_missing_docker_binary_is_reported(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(docker, "DOCKER_COMMAND", "nova-no-such-docker-binary")
    with pytest.raises(ServerUnavailable, match="info"):
        run_docker("info")


def test_failed_command_reports_only_the_last_lines_of_its_output(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    output = "\n".join(["DEPRECATED: The legacy builder is deprecated"] + [f"step {n}" for n in range(1, 31)])
    fake = tmp_path / "docker"
    fake.write_text(f"#!{sys.executable}\nimport sys\nsys.stderr.write({output!r} + chr(10))\nsys.exit(1)\n")
    fake.chmod(fake.stat().st_mode | stat.S_IXUSR)
    monkeypatch.setattr(docker, "DOCKER_COMMAND", str(fake))
    with pytest.raises(ServerUnavailable) as info:
        run_docker("build")
    message = str(info.value)
    assert message.endswith("step 30")
    assert "step 16" in message
    assert "step 15\n" not in message
    assert "DEPRECATED" not in message


def test_failed_command_with_only_stdout_reports_the_last_lines_of_stdout(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    output = "\n".join(f"step {n}" for n in range(1, 31))
    fake = tmp_path / "docker"
    fake.write_text(f"#!{sys.executable}\nimport sys\nsys.stdout.write({output!r} + chr(10))\nsys.exit(1)\n")
    fake.chmod(fake.stat().st_mode | stat.S_IXUSR)
    monkeypatch.setattr(docker, "DOCKER_COMMAND", str(fake))
    with pytest.raises(ServerUnavailable) as info:
        run_docker("build")
    message = str(info.value)
    assert message.endswith("step 30")
    assert "step 16" in message
    assert "step 15\n" not in message


def test_image_build_removes_intermediate_containers_of_failed_steps(monkeypatch: pytest.MonkeyPatch) -> None:
    calls: list[tuple[str, ...]] = []

    def _record(*args: str, **_kwargs: object) -> bytes:
        calls.append(args)
        return b""

    monkeypatch.setattr(docker, "run_docker", _record)
    build_image("gnu")
    (args,) = calls
    assert "--force-rm" in args
