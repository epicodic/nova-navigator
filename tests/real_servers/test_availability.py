"""Docker availability: a missing server skips locally and fails when the run requires it."""

from __future__ import annotations

import pytest

from tests.real_servers import docker
from tests.real_servers.docker import ServerUnavailable, run_docker, unavailable


@pytest.mark.parametrize("variable", ["CI", "NOVA_REQUIRE_REAL_SERVERS"])
def test_unavailable_server_fails_when_required(variable: str) -> None:
    with pytest.raises(pytest.fail.Exception, match="docker daemon not reachable"):
        unavailable("docker daemon not reachable", {variable: "1"})


def test_unavailable_server_is_skipped_with_the_reason() -> None:
    with pytest.raises(pytest.skip.Exception, match="Docker is not available: docker daemon not reachable"):
        unavailable("docker daemon not reachable", {})


def test_missing_docker_binary_is_reported(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(docker, "DOCKER_COMMAND", "nova-no-such-docker-binary")
    with pytest.raises(ServerUnavailable, match="info"):
        run_docker("info")
