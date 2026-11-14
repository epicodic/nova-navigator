"""The dataclasses that hold typed passwords during login keep them out of their repr."""

from __future__ import annotations

from nova_navigator.config.remotes import SshSettings
from nova_navigator.remotes.known_hosts import KnownHostsLocations
from nova_navigator.remotes.ssh import _Attempt
from nova_navigator.remotes.ssh_config import SshConfig
from nova_navigator.remotes.ssh_route import SshHop
from nova_navigator.vfs.filesystems.ssh import _RouteContext

TYPED_TEXT = "hunter2zq9"
OTHER_TEXT = "other8xk4"


def test_attempt_repr_hides_typed_credentials() -> None:
    attempt = _Attempt(
        ssh=SshSettings(host="h.example"),
        route=[SshHop("h", "h.example", 22, "u", ())],
        username="u",
        timeout=5.0,
        locations=KnownHostsLocations.default(),
        config=SshConfig.load([]),
    )
    attempt.credentials[0] = ("u", TYPED_TEXT)
    assert TYPED_TEXT not in repr(attempt)


def test_route_context_repr_hides_password_and_credentials() -> None:
    context = _RouteContext(
        locations=KnownHostsLocations.default(),
        ssh_config=SshConfig.load([]),
        connect_timeout=5.0,
        trusted_host_keys={},
        credentials={0: ("u", TYPED_TEXT)},
        target_password=OTHER_TEXT,
    )
    text = repr(context)
    assert TYPED_TEXT not in text
    assert OTHER_TEXT not in text
