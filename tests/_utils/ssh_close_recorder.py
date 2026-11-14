"""Helper that records which ``paramiko.SSHClient`` instances get closed."""

from __future__ import annotations

import paramiko
import pytest


def record_closes(monkeypatch: pytest.MonkeyPatch) -> list[int]:
    """Patch ``SSHClient.close`` and return the list that collects ``id(client)`` per call."""
    closed: list[int] = []
    original = paramiko.SSHClient.close

    def record(self: paramiko.SSHClient) -> None:
        closed.append(id(self))
        original(self)

    monkeypatch.setattr(paramiko.SSHClient, "close", record)
    return closed
