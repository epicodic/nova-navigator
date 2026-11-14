"""``SSHFilesystem`` over ProxyJump routes against stub servers (bastion with ``forward_map`` and target)."""

from __future__ import annotations

import logging
import socket
from collections.abc import Generator
from dataclasses import dataclass
from pathlib import Path, PurePosixPath

import paramiko
import pytest

from nova_navigator.remotes.known_hosts import KnownHostsLocations, host_key_name
from nova_navigator.remotes.ssh_config import SshConfig
from nova_navigator.remotes.ssh_route import SshHop
from nova_navigator.vfs.filesystems.ssh import (
    ChangedHostKeyError,
    HopAuthenticationError,
    RevokedHostKeyError,
    SSHFilesystem,
    SshHopError,
    UnknownHostKeyError,
)
from tests._utils.known_hosts_files import key_line, make_key
from tests._utils.ssh_identity import write_identity
from tests._utils.stub_ssh_server import StubSSHServer, make_host_key

_LOGIN_WORD = "testpass"
INTERNAL = "target.internal"


@dataclass
class Servers:
    bastion: StubSSHServer
    target: StubSSHServer
    locations: KnownHostsLocations
    target_root: Path

    @property
    def route(self) -> list[SshHop]:
        return [
            SshHop("bastion", self.bastion.host, self.bastion.port, "jumpuser", ()),
            SshHop("target", INTERNAL, 22, "testuser", ()),
        ]

    @property
    def bastion_name(self) -> str:
        return host_key_name(self.bastion.host, self.bastion.port)

    def trust_all(self) -> None:
        self.locations.user_known_hosts.parent.mkdir(parents=True, exist_ok=True)
        self.locations.user_known_hosts.write_text(key_line(self.bastion_name, self.bastion.host_key) + "\n" + key_line(INTERNAL, self.target.host_key) + "\n")


@pytest.fixture(autouse=True)
def isolated_home(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("HOME", str(tmp_path / "realhome-guard"))
    monkeypatch.delenv("SSH_AUTH_SOCK", raising=False)


@pytest.fixture
def servers(tmp_path: Path) -> Generator[Servers, None, None]:
    for name in ("t", "b", "home"):
        (tmp_path / name).mkdir()
    target = StubSSHServer(root_dir=tmp_path / "t", host_key=make_host_key("rsa"))
    target.start()
    bastion = StubSSHServer(
        root_dir=tmp_path / "b",
        host_key=make_host_key("ecdsa"),
        forward_map={(INTERNAL, 22): (target.host, target.port)},
    )
    bastion.start()
    yield Servers(bastion, target, KnownHostsLocations.under(tmp_path / "home", tmp_path / "etc"), tmp_path / "t")
    bastion.stop()
    target.stop()


def _open(
    env: Servers,
    route: list[SshHop] | None = None,
    *,
    password: str | None = _LOGIN_WORD,
    trusted_host_keys: dict[str, paramiko.PKey] | None = None,
    ssh_config: SshConfig | None = None,
    locations: KnownHostsLocations | None = None,
) -> SSHFilesystem:
    hops = route or env.route
    jump_logins = {index: (hop.user, password) for index, hop in enumerate(hops[:-1])} if password is not None else {}
    return SSHFilesystem(
        "target",
        22,
        "testuser",
        None,
        password,
        route=hops,
        credentials=jump_logins,
        known_hosts=locations or env.locations,
        connect_timeout=10.0,
        trusted_host_keys=trusted_host_keys,
        ssh_config=ssh_config,
    )


def _closed_port() -> int:
    with socket.socket() as sock:
        sock.bind(("127.0.0.1", 0))
        return int(sock.getsockname()[1])


def test_single_jump_reaches_target_listing_reading_and_writing(servers: Servers) -> None:
    servers.trust_all()
    (servers.target_root / "file.txt").write_text("x")
    fs = _open(servers)
    try:
        assert "file.txt" in fs._sftp_client.listdir(str(servers.target_root))
        remote = str(servers.target_root / "new.txt")
        with fs._sftp_client.open(remote, "w") as handle:
            handle.write("hello")
        with fs._sftp_client.open(remote, "r") as handle:
            assert handle.read() == b"hello"
        assert servers.bastion.forwarded == [(INTERNAL, 22)]
    finally:
        fs.close()


def test_target_is_reachable_only_through_the_bastion(servers: Servers) -> None:
    servers.trust_all()
    with pytest.raises(socket.gaierror):
        SSHFilesystem(INTERNAL, 22, "testuser", None, _LOGIN_WORD, known_hosts=servers.locations, connect_timeout=10.0)
    _open(servers).close()


def test_chain_of_two_bastions(servers: Servers, tmp_path: Path) -> None:
    (tmp_path / "b2").mkdir()
    bastion2 = StubSSHServer(
        root_dir=tmp_path / "b2",
        host_key=make_host_key("ecdsa"),
        forward_map={(INTERNAL, 22): (servers.target.host, servers.target.port)},
    )
    bastion2.start()
    first = StubSSHServer(
        root_dir=tmp_path / "b",
        host_key=make_host_key("ecdsa"),
        forward_map={("b2.internal", 22): (bastion2.host, bastion2.port)},
    )
    first.start()
    try:
        route = [
            SshHop("b1", first.host, first.port, "u1", ()),
            SshHop("b2", "b2.internal", 22, "u2", ()),
            SshHop("target", INTERNAL, 22, "testuser", ()),
        ]
        servers.locations.user_known_hosts.parent.mkdir(parents=True, exist_ok=True)
        servers.locations.user_known_hosts.write_text(
            "\n".join(
                [
                    key_line(host_key_name(first.host, first.port), first.host_key),
                    key_line("b2.internal", bastion2.host_key),
                    key_line(INTERNAL, servers.target.host_key),
                ]
            )
            + "\n"
        )
        fs = _open(servers, route)
        try:
            assert first.forwarded == [("b2.internal", 22)]
            assert bastion2.forwarded == [(INTERNAL, 22)]
            assert len(fs._hop_clients) == 2
        finally:
            fs.close()
    finally:
        first.stop()
        bastion2.stop()


def test_each_hop_uses_its_own_user_and_identity_file(servers: Servers, tmp_path: Path) -> None:
    servers.trust_all()
    path0, blob0 = write_identity(tmp_path / "id0")
    path1, blob1 = write_identity(tmp_path / "id1")
    route = [
        SshHop("bastion", servers.bastion.host, servers.bastion.port, "jumpuser", (path0,)),
        SshHop("target", INTERNAL, 22, "testuser", (path1,)),
    ]
    fs = _open(servers, route, password=None)
    try:
        assert servers.bastion.auth_log[-1].username == "jumpuser"
        assert servers.bastion.auth_log[-1].method == "publickey"
        assert servers.bastion.auth_log[-1].key_base64 == blob0
        assert servers.target.auth_log[-1].username == "testuser"
        assert servers.target.auth_log[-1].key_base64 == blob1
    finally:
        fs.close()


def test_unknown_key_on_jump_host_raises_with_jump_name_and_target_untouched(servers: Servers) -> None:
    with pytest.raises(UnknownHostKeyError) as info:
        _open(servers)
    assert info.value.hostname == servers.bastion_name
    assert servers.target.auth_log == []


def test_unknown_key_on_target_names_the_target(servers: Servers) -> None:
    servers.locations.user_known_hosts.parent.mkdir(parents=True, exist_ok=True)
    line = key_line(servers.bastion_name, servers.bastion.host_key) + "\n"
    servers.locations.user_known_hosts.write_text(line)
    with pytest.raises(UnknownHostKeyError) as info:
        _open(servers)
    assert info.value.hostname == INTERNAL
    assert info.value.key == servers.target.host_key
    assert servers.locations.user_known_hosts.read_text() == line


def test_confirmed_keys_for_both_hops_are_stored_once_each(servers: Servers) -> None:
    trusted = {servers.bastion_name: servers.bastion.host_key, INTERNAL: servers.target.host_key}
    fs = _open(servers, trusted_host_keys=trusted)
    try:
        assert len(servers.locations.user_known_hosts.read_text().splitlines()) == 2
        assert fs.host_key_save_error is None
    finally:
        fs.close()


@pytest.mark.parametrize("revoked", [True, False])
def test_revoked_and_changed_keys_on_a_jump_host_are_refused(servers: Servers, revoked: bool) -> None:
    servers.locations.user_known_hosts.parent.mkdir(parents=True, exist_ok=True)
    if revoked:
        line = key_line(servers.bastion_name, servers.bastion.host_key, marker="@revoked")
    else:
        line = key_line(servers.bastion_name, make_key("ecdsa"))
    servers.locations.user_known_hosts.write_text(line + "\n")
    with pytest.raises(RevokedHostKeyError if revoked else ChangedHostKeyError):
        _open(servers)
    assert servers.target.auth_log == []


def test_forwarding_prohibited_names_the_target_and_the_jump(servers: Servers, tmp_path: Path) -> None:
    (tmp_path / "nb").mkdir()
    bastion = StubSSHServer(root_dir=tmp_path / "nb", host_key=make_host_key("ecdsa"))
    bastion.start()
    try:
        route = [SshHop("bastion", bastion.host, bastion.port, "jumpuser", ()), SshHop("target", INTERNAL, 22, "testuser", ())]
        servers.locations.user_known_hosts.parent.mkdir(parents=True, exist_ok=True)
        servers.locations.user_known_hosts.write_text(key_line(host_key_name(bastion.host, bastion.port), bastion.host_key) + "\n")
        with pytest.raises(SshHopError) as info:
            _open(servers, route)
        assert info.value.index == 1
        message = str(info.value)
        assert INTERNAL in message
        assert "bastion" in message
        assert "via" in message
    finally:
        bastion.stop()


def test_jump_authentication_refused_names_the_jump_host(servers: Servers, tmp_path: Path) -> None:
    servers.trust_all()
    (tmp_path / "rb").mkdir()
    bastion = StubSSHServer(
        root_dir=tmp_path / "rb",
        host_key=servers.bastion.host_key,
        reject_publickey=True,
        forward_map={(INTERNAL, 22): (servers.target.host, servers.target.port)},
    )
    bastion.start()
    try:
        identity, _blob = write_identity(tmp_path / "id")
        route = [SshHop("bastion", bastion.host, bastion.port, "jumpuser", (identity,)), SshHop("target", INTERNAL, 22, "testuser", ())]
        servers.locations.user_known_hosts.write_text(key_line(host_key_name(bastion.host, bastion.port), bastion.host_key) + "\n" + key_line(INTERNAL, servers.target.host_key) + "\n")
        with pytest.raises(HopAuthenticationError) as info:
            _open(servers, route, password=None)
        assert info.value.index == 0
        assert "bastion" in str(info.value)
    finally:
        bastion.stop()


def test_jump_host_down_names_the_hop(servers: Servers) -> None:
    route = [SshHop("bastion", "127.0.0.1", _closed_port(), "jumpuser", ()), SshHop("target", INTERNAL, 22, "testuser", ())]
    with pytest.raises(SshHopError) as info:
        _open(servers, route)
    assert info.value.index == 0
    assert str(info.value).startswith("Jump host 'bastion'") or str(info.value).startswith("Jump host 'bastion' (")
    assert "hop 1 of 2" in str(info.value)


def test_proxy_jump_none_connects_directly(servers: Servers) -> None:
    target = servers.target
    servers.locations.user_known_hosts.parent.mkdir(parents=True, exist_ok=True)
    servers.locations.user_known_hosts.write_text(key_line(host_key_name(target.host, target.port), target.host_key) + "\n")
    fs = _open(servers, [SshHop("target", target.host, target.port, "u", ())])
    try:
        assert servers.bastion.forwarded == []
        assert servers.bastion.auth_log == []
    finally:
        fs.close()


def test_single_hop_errors_are_not_wrapped(servers: Servers) -> None:
    with pytest.raises(ConnectionRefusedError):
        _open(servers, [SshHop("x", "127.0.0.1", _closed_port(), "u", ())])


def _record_closes(monkeypatch: pytest.MonkeyPatch) -> list[int]:
    closed: list[int] = []
    original = paramiko.SSHClient.close

    def record(self: paramiko.SSHClient) -> None:
        closed.append(id(self))
        original(self)

    monkeypatch.setattr(paramiko.SSHClient, "close", record)
    return closed


def test_close_order_is_target_then_jumps_in_reverse(servers: Servers, tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    (tmp_path / "b2").mkdir()
    bastion2 = StubSSHServer(
        root_dir=tmp_path / "b2",
        host_key=make_host_key("ecdsa"),
        forward_map={(INTERNAL, 22): (servers.target.host, servers.target.port)},
    )
    bastion2.start()
    first = StubSSHServer(
        root_dir=tmp_path / "b",
        host_key=make_host_key("ecdsa"),
        forward_map={("b2.internal", 22): (bastion2.host, bastion2.port)},
    )
    first.start()
    try:
        route = [
            SshHop("b1", first.host, first.port, "u1", ()),
            SshHop("b2", "b2.internal", 22, "u2", ()),
            SshHop("target", INTERNAL, 22, "testuser", ()),
        ]
        servers.locations.user_known_hosts.parent.mkdir(parents=True, exist_ok=True)
        servers.locations.user_known_hosts.write_text(
            "\n".join(
                [
                    key_line(host_key_name(first.host, first.port), first.host_key),
                    key_line("b2.internal", bastion2.host_key),
                    key_line(INTERNAL, servers.target.host_key),
                ]
            )
            + "\n"
        )
        fs = _open(servers, route)
        closed = _record_closes(monkeypatch)
        expected = [id(fs._ssh_client), id(fs._hop_clients[1]), id(fs._hop_clients[0])]
        fs.close()
        assert closed == expected
        fs.close()
    finally:
        first.stop()
        bastion2.stop()


def test_failed_attempt_closes_opened_jump_clients(servers: Servers, tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    servers.trust_all()
    (tmp_path / "rt").mkdir()
    (tmp_path / "rb").mkdir()
    target = StubSSHServer(root_dir=tmp_path / "rt", host_key=servers.target.host_key, reject_publickey=True)
    target.start()
    bastion = StubSSHServer(
        root_dir=tmp_path / "rb",
        host_key=servers.bastion.host_key,
        forward_map={(INTERNAL, 22): (target.host, target.port)},
    )
    bastion.start()
    try:
        identity, _blob = write_identity(tmp_path / "id")
        route = [
            SshHop("bastion", bastion.host, bastion.port, "jumpuser", ()),
            SshHop("target", INTERNAL, 22, "testuser", (identity,)),
        ]
        servers.locations.user_known_hosts.write_text(key_line(host_key_name(bastion.host, bastion.port), bastion.host_key) + "\n" + key_line(INTERNAL, target.host_key) + "\n")
        closed = _record_closes(monkeypatch)
        with pytest.raises(HopAuthenticationError):
            SSHFilesystem(
                "target",
                22,
                "testuser",
                known_hosts=servers.locations,
                route=route,
                connect_timeout=10.0,
                credentials={0: ("jumpuser", _LOGIN_WORD)},
            )
        assert len(closed) >= 2
    finally:
        bastion.stop()
        target.stop()


def test_terminal_style_session_on_the_target(servers: Servers) -> None:
    servers.trust_all()
    fs = _open(servers)
    try:
        transport = fs._ssh_client.get_transport()
        assert transport is not None
        chan = transport.open_session()
        chan.exec_command("echo hi")
        assert chan.recv(16) == b"hi\n"
    finally:
        fs.close()


def test_uri_keeps_the_requested_alias(servers: Servers) -> None:
    servers.trust_all()
    fs = SSHFilesystem(
        "myalias",
        22,
        "bob",
        route=servers.route,
        known_hosts=servers.locations,
        connect_timeout=10.0,
        password=_LOGIN_WORD,
        credentials={0: ("jumpuser", _LOGIN_WORD)},
    )
    try:
        assert fs.uri_for_path(PurePosixPath("/x")) == "ssh://bob@myalias/x"
    finally:
        fs.close()


def test_only_the_first_save_failure_is_reported(servers: Servers, tmp_path: Path, caplog: pytest.LogCaptureFixture) -> None:
    blocked_home = tmp_path / "blocked"
    blocked_home.mkdir()
    (blocked_home / ".ssh").write_text("not a directory")
    locations = KnownHostsLocations.under(blocked_home, tmp_path / "etc")
    trusted = {servers.bastion_name: servers.bastion.host_key, INTERNAL: servers.target.host_key}
    with caplog.at_level(logging.WARNING):
        fs = _open(servers, trusted_host_keys=trusted, locations=locations)
    try:
        assert isinstance(fs.host_key_save_error, OSError)
        assert fs.host_key_save_path == locations.user_known_hosts
        assert len([r for r in caplog.records if "Could not save host key" in r.getMessage()]) == 2
    finally:
        fs.close()


def test_hash_known_hosts_is_looked_up_by_alias_and_stored_under_the_resolved_name(servers: Servers, tmp_path: Path) -> None:
    config_file = tmp_path / "cfg"
    config_file.write_text("Host bastion\n  HashKnownHosts yes\n")
    trusted = {servers.bastion_name: servers.bastion.host_key, INTERNAL: servers.target.host_key}
    fs = _open(servers, trusted_host_keys=trusted, ssh_config=SshConfig.load([config_file]))
    try:
        lines = servers.locations.user_known_hosts.read_text().splitlines()
        assert lines[0].startswith("|1|")
        hosts = paramiko.HostKeys()
        hosts.load(str(servers.locations.user_known_hosts))
        assert hosts.lookup(servers.bastion_name) is not None
        assert lines[1].startswith(INTERNAL + " ")
    finally:
        fs.close()
