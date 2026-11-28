"""The shared real servers start once per process, and a failed start is not retried by every test."""

from __future__ import annotations

import pytest

from tests.real_servers.docker import ServerUnavailable, start_once


def test_unavailable_start_is_not_retried() -> None:
    calls: list[int] = []

    def _start() -> str:
        calls.append(1)
        raise ServerUnavailable("docker is down")

    shared = start_once(_start)
    for _ in range(3):
        with pytest.raises(ServerUnavailable, match="docker is down"):
            shared()
    assert len(calls) == 1


def test_successful_start_is_shared() -> None:
    calls: list[int] = []

    def _start() -> str:
        calls.append(1)
        return "server"

    shared = start_once(_start)
    assert shared() == "server"
    assert shared() == "server"
    assert len(calls) == 1


def test_other_errors_are_not_cached() -> None:
    attempts: list[int] = []

    def _start() -> str:
        attempts.append(1)
        if len(attempts) == 1:
            raise RuntimeError("transient")
        return "server"

    shared = start_once(_start)
    with pytest.raises(RuntimeError, match="transient"):
        shared()
    assert shared() == "server"
    assert len(attempts) == 2
