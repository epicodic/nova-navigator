"""``connect_ssh`` with ssh_config aliases, ProxyJump chains and the connect timeout (stub servers, fake dialogs)."""

from __future__ import annotations

import logging
import socket
import time
from collections.abc import Generator
from dataclasses import dataclass
from pathlib import Path, PurePosixPath
from typing import Any

import paramiko
import pytest

from nova_navigator.config.remotes import RemoteConfig, RemoteConnection, SshSettings
from nova_navigator.config.settings import Settings
from nova_navigator.remotes import ssh as ssh_module
from nova_navigator.remotes.known_hosts import host_key_name, read_known_hosts
from nova_navigator.remotes.remote import RemoteConnector
from nova_navigator.remotes.ssh import SshConnector, connect_ssh
from nova_navigator.vfs.filesystems import HopAuthenticationError
from nova_navigator.vfs.filesystems.remote import RemoteFilesystem
from nova_navigator.vfs.filesystems.ssh import SSHFilesystem
from tests._utils.known_hosts_files import key_line
from tests._utils.silent_server import SilentServer
from tests._utils.ssh_identity import write_identity
from tests._utils.stub_ssh_server import StubSSHServer, make_host_key
from tests.remotes.conftest import FakeCredentialsDialog, FakeMessageBox, FlowEnv

INTERNAL = "target.internal"
UNKNOWN = "Unknown Host"
_USER = "testuser"


@dataclass
class RouteEnv:
    flow: FlowEnv
    bastion: StubSSHServer
    target: StubSSHServer
    identity: str
    root: Path

    @property
    def bastion_name(self) -> str:
        return host_key_name(self.bastion.host, self.bastion.port)

    def write_config(self, text: str) -> None:
        path = self.flow.locations.user_config
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(text)

    def chain_config(self) -> str:
        return (
            f"Host target\n  HostName {INTERNAL}\n  User {_USER}\n  IdentityFile {self.identity}\n  ProxyJump jump\n"
            f"Host jump\n  HostName {self.bastion.host}\n  Port {self.bastion.port}\n  User jumpuser\n  IdentityFile {self.identity}\n"
        )

    def alias_config(self, **extra: str) -> str:
        values = {"HostName": self.target.host, "Port": str(self.target.port), "User": _USER, "IdentityFile": self.identity} | extra
        return "Host myalias\n" + "".join(f"  {key} {value}\n" for key, value in values.items())

    def trust(self, names: dict[str, paramiko.PKey]) -> None:
        self.flow.write_known_hosts("".join(key_line(name, key) + "\n" for name, key in names.items()))

    def trust_chain(self, *, bastion: bool = True, target: bool = True) -> None:
        names: dict[str, paramiko.PKey] = {}
        if bastion:
            names[self.bastion_name] = self.bastion.host_key
        if target:
            names[INTERNAL] = self.target.host_key
        self.trust(names)

    def trust_target(self) -> None:
        self.trust({host_key_name(self.target.host, self.target.port): self.target.host_key})

    def connection(self, host: str = "target") -> RemoteConnection:
        return RemoteConnection(name=host, ssh=SshSettings(host=host))

    @property
    def boxes(self) -> list[FakeMessageBox]:
        return FakeMessageBox.shown


@pytest.fixture
def route_env(flow_env: FlowEnv, tmp_path: Path) -> Generator[RouteEnv, None, None]:
    for name in ("troot", "broot"):
        (tmp_path / name).mkdir()
    identity, _ = write_identity(tmp_path / "id_route")
    target = StubSSHServer(root_dir=tmp_path / "troot", host_key=make_host_key("rsa"))
    target.start()
    bastion = StubSSHServer(root_dir=tmp_path / "broot", host_key=make_host_key("ecdsa"), forward_map={(INTERNAL, 22): (target.host, target.port)})
    bastion.start()
    yield RouteEnv(flow_env, bastion, target, identity, tmp_path)
    bastion.stop()
    target.stop()


def _make_config(*connections: RemoteConnection) -> RemoteConfig:
    config = object.__new__(RemoteConfig)
    config._items = list(connections)
    return config


def _slow_user() -> None:
    time.sleep(1.6)


def _titles(env: RouteEnv) -> list[str]:
    return [box.title for box in env.boxes]


@pytest.mark.asyncio
async def test_alias_in_ssh_config_connects_to_hostname_user_port_and_key(route_env: RouteEnv) -> None:
    route_env.write_config(route_env.alias_config())
    route_env.trust_target()

    fs = await connect_ssh(route_env.connection("myalias"))

    assert fs is not None
    assert route_env.target.auth_log[-1].username == _USER
    assert route_env.target.auth_log[-1].method == "publickey"
    assert fs._hostname == "myalias"
    assert fs.uri_for_path(PurePosixPath("/x")) == f"ssh://{_USER}@myalias/x"
    assert route_env.boxes == []


@pytest.mark.asyncio
async def test_saved_remote_alias_resolves_through_remote_connector(route_env: RouteEnv) -> None:
    route_env.write_config(route_env.alias_config())
    route_env.trust_target()
    conn = route_env.connection("myalias")

    vpath = await RemoteConnector(_make_config(conn)).resolve("/", "myalias")

    assert vpath is not None
    assert isinstance(vpath.filesystem, RemoteFilesystem)
    assert isinstance(vpath.filesystem._inner, SSHFilesystem)
    assert vpath.filesystem._inner._ssh_client.get_transport() is not None


@pytest.mark.asyncio
async def test_explicit_values_win_over_config(route_env: RouteEnv) -> None:
    route_env.write_config("Host myalias\n  HostName 127.0.0.1\n  Port 1\n  User wrong\n  IdentityFile /nonexistent\n")
    route_env.trust_target()
    ssh = SshSettings(host="myalias", user=_USER, port=route_env.target.port, identity_file=route_env.identity)

    fs = await connect_ssh(RemoteConnection(name="saved", ssh=ssh))

    assert fs is not None
    assert route_env.target.auth_log[-1].username == _USER


@pytest.mark.asyncio
async def test_ssh_uri_values_win_over_config(route_env: RouteEnv) -> None:
    route_env.write_config(f"Host myalias\n  HostName 127.0.0.1\n  Port 1\n  User wrong\n  IdentityFile {route_env.identity}\n")
    route_env.trust_target()

    vpath = await SshConnector().resolve("/x", f"{_USER}@myalias:{route_env.target.port}")

    assert vpath is not None
    assert str(vpath.path) == "/x"


@pytest.mark.asyncio
async def test_ssh_uri_without_path_opens_the_target_login_directory(route_env: RouteEnv) -> None:
    route_env.write_config(route_env.alias_config())
    route_env.trust_target()

    vpath = await SshConnector().resolve("", f"{_USER}@myalias:{route_env.target.port}")

    assert vpath is not None
    assert str(vpath.path) == str(route_env.root / "troot")


@pytest.mark.asyncio
async def test_saved_remote_without_path_opens_the_target_login_directory(route_env: RouteEnv) -> None:
    route_env.write_config(route_env.alias_config())
    route_env.trust_target()

    vpath = await RemoteConnector(_make_config(route_env.connection("myalias"))).resolve("", "myalias")

    assert vpath is not None
    assert isinstance(vpath.filesystem, RemoteFilesystem)
    assert str(vpath.path) == str(route_env.root / "troot")


@pytest.mark.asyncio
async def test_no_matching_entry_uses_given_host_and_port(route_env: RouteEnv) -> None:
    route_env.write_config("")
    route_env.trust_target()
    ssh = SshSettings(host=route_env.target.host, port=route_env.target.port, user=_USER, identity_file=route_env.identity)

    fs = await connect_ssh(RemoteConnection(name="plain", ssh=ssh))

    assert fs is not None


@pytest.mark.asyncio
async def test_unreadable_config_still_connects(route_env: RouteEnv) -> None:
    route_env.flow.locations.user_config.mkdir(parents=True)
    route_env.trust_target()
    ssh = SshSettings(host=route_env.target.host, port=route_env.target.port, user=_USER, identity_file=route_env.identity)

    fs = await connect_ssh(RemoteConnection(name="plain", ssh=ssh))

    assert fs is not None
    assert route_env.boxes == []


@pytest.mark.asyncio
async def test_match_exec_in_user_config_is_never_executed(route_env: RouteEnv) -> None:
    marker = route_env.root / "marker"
    route_env.write_config(f'Match exec "touch {marker}"\n  User wrong\n' + route_env.alias_config())
    route_env.trust_target()

    fs = await connect_ssh(route_env.connection("myalias"))

    assert fs is not None
    assert not marker.exists()


@pytest.mark.asyncio
async def test_chain_end_to_end_with_one_question_per_unknown_hop(route_env: RouteEnv) -> None:
    route_env.write_config(route_env.chain_config())
    route_env.flow.write_known_hosts("")

    fs = await connect_ssh(route_env.connection())

    assert fs is not None
    assert _titles(route_env) == [UNKNOWN, UNKNOWN]
    assert route_env.bastion.host in route_env.boxes[0].message
    assert str(route_env.bastion.port) in route_env.boxes[0].message
    assert INTERNAL in route_env.boxes[1].message
    assert route_env.flow.constructions == 3
    stored = read_known_hosts([route_env.flow.known_hosts])
    assert len(stored.keys_for(route_env.bastion_name)) == 1
    assert len(stored.keys_for(INTERNAL)) == 1
    assert set(route_env.bastion.forwarded) == {(INTERNAL, 22)}  # the chain restarts after each answered question
    assert route_env.bastion.auth_log[-1].username == "jumpuser"
    assert route_env.target.auth_log[-1].username == _USER
    _stdin, stdout, _stderr = fs._ssh_client.exec_command("echo hi")
    assert stdout.read().strip() == b"hi"


@pytest.mark.asyncio
async def test_trusted_chain_connects_without_questions(route_env: RouteEnv) -> None:
    route_env.write_config(route_env.chain_config())
    route_env.trust_chain()

    fs = await connect_ssh(route_env.connection())

    assert fs is not None
    assert route_env.boxes == []
    assert FakeCredentialsDialog.runs == 0


@pytest.mark.asyncio
async def test_chain_a_b_goes_through_both_hops_in_order(route_env: RouteEnv) -> None:
    second_root = route_env.root / "sroot"
    second_root.mkdir()
    second = StubSSHServer(root_dir=second_root, host_key=make_host_key("ed25519"), forward_map={(INTERNAL, 22): (route_env.target.host, route_env.target.port)})
    second.start()
    first = StubSSHServer(root_dir=second_root, host_key=make_host_key("ecdsa"), forward_map={("second.internal", 22): (second.host, second.port)})
    first.start()
    try:
        identity = route_env.identity
        route_env.write_config(
            f"Host target\n  HostName {INTERNAL}\n  User {_USER}\n  IdentityFile {identity}\n  ProxyJump first,second\n"
            f"Host first\n  HostName {first.host}\n  Port {first.port}\n  User {_USER}\n  IdentityFile {identity}\n"
            f"Host second\n  HostName second.internal\n  User {_USER}\n  IdentityFile {identity}\n"
        )
        route_env.trust({host_key_name(first.host, first.port): first.host_key, "second.internal": second.host_key, INTERNAL: route_env.target.host_key})

        fs = await connect_ssh(route_env.connection())

        assert fs is not None
        assert first.forwarded == [("second.internal", 22)]
        assert second.forwarded == [(INTERNAL, 22)]
        assert route_env.bastion.forwarded == []
    finally:
        first.stop()
        second.stop()


@pytest.mark.asyncio
async def test_proxy_jump_none_connects_directly(route_env: RouteEnv) -> None:
    route_env.write_config(route_env.alias_config(ProxyJump="none"))
    route_env.trust_target()

    fs = await connect_ssh(route_env.connection("myalias"))

    assert fs is not None
    assert route_env.bastion.forwarded == []
    assert route_env.bastion.auth_log == []


@pytest.mark.asyncio
async def test_jump_forwarding_refused_shows_error_box_naming_both_hosts(route_env: RouteEnv) -> None:
    refusing = StubSSHServer(root_dir=route_env.root / "broot", host_key=make_host_key("ecdsa"))
    refusing.start()
    try:
        config = route_env.chain_config().replace(str(route_env.bastion.port), str(refusing.port))
        route_env.write_config(config)
        route_env.trust({host_key_name(refusing.host, refusing.port): refusing.host_key, INTERNAL: route_env.target.host_key})

        fs = await connect_ssh(route_env.connection())

        assert fs is None
        assert [box.variant for box in route_env.boxes] == ["error"]
        text = route_env.boxes[0].message
        assert "Could not connect to 'target'" in text
        assert INTERNAL in text
        assert "jump" in text
    finally:
        refusing.stop()


@pytest.mark.asyncio
async def test_jump_host_down_shows_error_box_naming_the_jump_host(route_env: RouteEnv) -> None:
    with socket.socket() as probe:
        probe.bind(("127.0.0.1", 0))
        closed_port = probe.getsockname()[1]
    route_env.write_config(route_env.chain_config().replace(str(route_env.bastion.port), str(closed_port)))

    fs = await connect_ssh(route_env.connection())

    assert fs is None
    assert [box.variant for box in route_env.boxes] == ["error"]
    assert "Jump host 'jump'" in route_env.boxes[0].message


@pytest.mark.asyncio
async def test_credentials_are_asked_for_the_failing_jump_hop_only(route_env: RouteEnv) -> None:
    rejecting = StubSSHServer(
        root_dir=route_env.root / "broot",
        host_key=make_host_key("ecdsa"),
        reject_publickey=True,
        forward_map={(INTERNAL, 22): (route_env.target.host, route_env.target.port)},
    )
    rejecting.start()
    try:
        route_env.write_config(route_env.chain_config().replace(str(route_env.bastion.port), str(rejecting.port)))
        route_env.trust({host_key_name(rejecting.host, rejecting.port): rejecting.host_key, INTERNAL: route_env.target.host_key})

        fs = await connect_ssh(route_env.connection())

        assert fs is not None
        assert FakeCredentialsDialog.runs == 1
        assert FakeCredentialsDialog.hostnames == [rejecting.host]
        assert route_env.boxes == []
    finally:
        rejecting.stop()


@pytest.mark.asyncio
async def test_second_authentication_failure_of_a_hop_ends_with_an_error_box(route_env: RouteEnv, monkeypatch: pytest.MonkeyPatch) -> None:
    route_env.write_config(route_env.chain_config())

    class _AlwaysRefused:
        def __init__(self, *args: Any, **kwargs: Any) -> None:
            route_env.flow.constructions += 1
            route = kwargs["route"]
            raise HopAuthenticationError(route, 0)

    monkeypatch.setattr(ssh_module, "SSHFilesystem", _AlwaysRefused)

    fs = await connect_ssh(route_env.connection())

    assert fs is None
    assert FakeCredentialsDialog.runs == 1
    assert [box.variant for box in route_env.boxes] == ["error"]
    assert route_env.flow.constructions == 2


@pytest.mark.asyncio
async def test_route_loop_shows_error_box(route_env: RouteEnv) -> None:
    route_env.write_config("Host a\n  ProxyJump b\nHost b\n  ProxyJump a\n")

    fs = await connect_ssh(RemoteConnection(name="loop", ssh=SshSettings(host="a")))

    assert fs is None
    assert [box.variant for box in route_env.boxes] == ["error"]
    assert "'a'" in route_env.boxes[0].message
    assert route_env.flow.constructions == 0


@pytest.mark.asyncio
async def test_proxy_command_alone_connects_directly_and_logs_info(route_env: RouteEnv, caplog: pytest.LogCaptureFixture) -> None:
    marker = route_env.root / "proxy_marker"
    route_env.write_config(route_env.alias_config(ProxyCommand=f"touch {marker}"))
    route_env.trust_target()

    with caplog.at_level(logging.INFO):
        fs = await connect_ssh(route_env.connection("myalias"))

    assert fs is not None
    assert "ProxyCommand" in caplog.text
    assert not marker.exists()
    assert route_env.boxes == []


@pytest.mark.asyncio
async def test_silent_host_fails_after_ssh_timeout_with_error_box(route_env: RouteEnv, default_settings: Settings) -> None:
    default_settings.network.ssh_timeout = 1
    silent = SilentServer()
    silent.start()
    try:
        ssh = SshSettings(host="127.0.0.1", port=silent.port, user=_USER, identity_file=route_env.identity)
        start = time.monotonic()
        fs = await connect_ssh(RemoteConnection(name="silent", ssh=ssh))
        elapsed = time.monotonic() - start
    finally:
        silent.stop()

    assert fs is None
    assert 0.9 <= elapsed <= 3.5
    assert [box.variant for box in route_env.boxes] == ["error"]
    assert "127.0.0.1" in route_env.boxes[0].message
    assert "Timed out after 1 s" in route_env.boxes[0].message


@pytest.mark.asyncio
async def test_waiting_for_the_user_is_not_counted_against_the_timeout(route_env: RouteEnv, default_settings: Settings) -> None:
    default_settings.network.ssh_timeout = 1
    route_env.flow.write_known_hosts("")
    FakeMessageBox.hooks[UNKNOWN] = _slow_user
    ssh = SshSettings(host=route_env.target.host, port=route_env.target.port, user=_USER, identity_file=route_env.identity)

    fs = await connect_ssh(RemoteConnection(name="slow-user", ssh=ssh))

    assert fs is not None
    stored = read_known_hosts([route_env.flow.known_hosts])
    assert len(stored.keys_for(host_key_name(route_env.target.host, route_env.target.port))) == 1


async def _timed_failure(route_env: RouteEnv, port: int) -> float:
    ssh = SshSettings(host="127.0.0.1", port=port, user=_USER, identity_file=route_env.identity)
    start = time.monotonic()
    fs = await connect_ssh(RemoteConnection(name="silent", ssh=ssh))
    assert fs is None
    return time.monotonic() - start


@pytest.mark.asyncio
async def test_changed_timeout_setting_applies_to_the_next_connection(route_env: RouteEnv, default_settings: Settings, monkeypatch: pytest.MonkeyPatch) -> None:
    route_env.trust_target()
    ssh = SshSettings(host=route_env.target.host, port=route_env.target.port, user=_USER, identity_file=route_env.identity)
    conn = RemoteConnection(name="t", ssh=ssh)

    async def connect_once() -> None:
        fs = await connect_ssh(conn)
        assert fs is not None
        fs.close()

    default_settings.network.ssh_timeout = 1
    await connect_once()
    default_settings.network.ssh_timeout = 2
    await connect_once()
    replaced = Settings()
    replaced.network.ssh_timeout = 1
    monkeypatch.setattr(ssh_module.conf_, "settings", replaced, raising=False)
    await connect_once()

    assert route_env.flow.timeouts == [1.0, 2.0, 1.0]


@pytest.mark.asyncio
async def test_timeout_below_one_second_is_clamped_to_one(route_env: RouteEnv, default_settings: Settings) -> None:
    default_settings.network.ssh_timeout = 0
    silent = SilentServer()
    silent.start()
    try:
        elapsed = await _timed_failure(route_env, silent.port)
    finally:
        silent.stop()

    assert 0.9 <= elapsed <= 3.5
