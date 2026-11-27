"""Per-hop connect deadline of ``SSHFilesystem``: watchdog unit tests and hung-peer scenarios."""

from __future__ import annotations

import threading
import time
from collections.abc import Generator
from pathlib import Path
from typing import Any
from unittest.mock import MagicMock

import paramiko
import pytest

from nova_navigator.remotes.known_hosts import KnownHostsLocations, host_key_name
from nova_navigator.remotes.ssh_route import SshHop
from nova_navigator.vfs.filesystems.ssh import SshConnectTimeoutError, SSHFilesystem, SshHopError, _Deadline
from tests._utils.known_hosts_files import key_line
from tests._utils.silent_server import SilentServer
from tests._utils.ssh_close_recorder import record_closes
from tests._utils.stub_ssh_server import StubSSHServer, make_host_key

_LOGIN_WORD = "testpass"
_SLOW = 3.5
_JUMP_LOGIN = {0: ("j", _LOGIN_WORD)}


@pytest.fixture(autouse=True)
def isolated_home(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("HOME", str(tmp_path / "realhome-guard"))
    monkeypatch.delenv("SSH_AUTH_SOCK", raising=False)


@pytest.fixture
def locations(tmp_path: Path) -> KnownHostsLocations:
    (tmp_path / "home" / ".ssh").mkdir(parents=True)
    return KnownHostsLocations.under(tmp_path / "home", tmp_path / "etc")


@pytest.fixture
def silent() -> Generator[SilentServer, None, None]:
    server = SilentServer()
    server.start()
    yield server
    server.stop()


def test_deadline_closes_registered_resources_on_expiry() -> None:
    closed: list[str] = []
    with _Deadline(0.2) as deadline:
        deadline.register(lambda: closed.append("x"))
        time.sleep(0.5)
        assert closed == ["x"]
        assert deadline.expired


def test_deadline_does_not_close_after_exit() -> None:
    closed: list[str] = []
    with _Deadline(0.2) as deadline:
        deadline.register(lambda: closed.append("x"))
    time.sleep(0.5)
    assert closed == []


def test_finish_before_expiry_marks_done_and_the_timer_closes_nothing() -> None:
    closed: list[str] = []
    with _Deadline(0.2) as deadline:
        deadline.register(lambda: closed.append("x"))
        assert deadline.finish() is True
        time.sleep(0.5)
    assert closed == []


def test_finish_after_expiry_reports_failure() -> None:
    closed: list[str] = []
    with _Deadline(0.1) as deadline:
        deadline.register(lambda: closed.append("x"))
        time.sleep(0.4)
        assert closed == ["x"]
        assert deadline.finish() is False


def test_register_after_expiry_runs_the_closer_immediately() -> None:
    closed: list[str] = []
    with _Deadline(0.1) as deadline:
        time.sleep(0.4)
        deadline.register(lambda: closed.append("late"))
    assert closed == ["late"]


@pytest.mark.wall_clock
def test_silent_host_fails_after_the_timeout_naming_host_and_seconds(silent: SilentServer, locations: KnownHostsLocations) -> None:
    route = [SshHop("silent", "127.0.0.1", silent.port, "u", ())]
    start = time.monotonic()
    with pytest.raises(SshConnectTimeoutError) as info:
        SSHFilesystem("silent", silent.port, "u", None, _LOGIN_WORD, route=route, connect_timeout=1.0, known_hosts=locations)
    elapsed = time.monotonic() - start
    assert 0.9 <= elapsed <= _SLOW
    message = str(info.value)
    assert "127.0.0.1" in message
    assert "silent" in message
    assert "1 s" in message
    assert isinstance(info.value, SshHopError)


@pytest.mark.wall_clock
def test_silent_second_hop_names_the_target_and_closes_the_bastion(silent: SilentServer, locations: KnownHostsLocations, tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    (tmp_path / "b").mkdir()
    bastion = StubSSHServer(
        root_dir=tmp_path / "b",
        host_key=make_host_key("ecdsa"),
        forward_map={("silent.internal", 22): ("127.0.0.1", silent.port)},
    )
    bastion.start()
    closed = record_closes(monkeypatch)
    connected: list[int] = []
    original_connect = paramiko.SSHClient.connect

    def track_connect(self: paramiko.SSHClient, *args: Any, **kwargs: Any) -> None:
        connected.append(id(self))
        original_connect(self, *args, **kwargs)

    monkeypatch.setattr(paramiko.SSHClient, "connect", track_connect)
    try:
        locations.user_known_hosts.write_text(key_line(host_key_name(bastion.host, bastion.port), bastion.host_key) + "\n")
        route = [SshHop("bastion", bastion.host, bastion.port, "j", ()), SshHop("silent.internal", "silent.internal", 22, "u", ())]
        start = time.monotonic()
        with pytest.raises(SshConnectTimeoutError) as info:
            SSHFilesystem("silent.internal", 22, "u", None, _LOGIN_WORD, route=route, connect_timeout=1.0, known_hosts=locations, credentials=_JUMP_LOGIN)
        assert time.monotonic() - start <= 2 * 1.0 + 2.5
        assert info.value.index == 1
        assert "silent.internal" in str(info.value)
        assert len(connected) >= 1
        assert connected[0] in closed  # the bastion (first hop) is closed, not only the failing target
    finally:
        bastion.stop()


@pytest.mark.wall_clock
def test_hanging_sftp_subsystem_is_bounded(tmp_path: Path, locations: KnownHostsLocations) -> None:
    (tmp_path / "root").mkdir()
    server = StubSSHServer(root_dir=tmp_path / "root", host_key=make_host_key("rsa"), hang_sftp=True)
    server.start()
    try:
        locations.user_known_hosts.write_text(key_line(host_key_name(server.host, server.port), server.host_key) + "\n")
        route = [SshHop("stub", server.host, server.port, "u", ())]
        start = time.monotonic()
        with pytest.raises(SshConnectTimeoutError):
            SSHFilesystem("stub", server.port, "u", None, _LOGIN_WORD, route=route, connect_timeout=1.0, known_hosts=locations)
        assert time.monotonic() - start <= _SLOW
    finally:
        server.stop()


def test_hop_whose_watchdog_fired_during_the_last_step_fails_with_the_timeout(tmp_path: Path, locations: KnownHostsLocations, monkeypatch: pytest.MonkeyPatch) -> None:
    (tmp_path / "root").mkdir()
    server = StubSSHServer(root_dir=tmp_path / "root", host_key=make_host_key("rsa"))
    server.start()
    try:
        locations.user_known_hosts.write_text(key_line(host_key_name(server.host, server.port), server.host_key) + "\n")

        def slow_sftp(_self: paramiko.SSHClient) -> paramiko.SFTPClient:
            time.sleep(1.5)  # the watchdog (1 s) fires and closes the client, yet this call "succeeds"
            return sftp

        sftp = MagicMock(spec=paramiko.SFTPClient)
        closed = record_closes(monkeypatch)
        monkeypatch.setattr(paramiko.SSHClient, "open_sftp", slow_sftp)
        route = [SshHop("stub", server.host, server.port, "u", ())]
        with pytest.raises(SshConnectTimeoutError):
            SSHFilesystem("stub", server.port, "u", None, _LOGIN_WORD, route=route, connect_timeout=1.0, known_hosts=locations)
        sftp.close.assert_called()
        assert closed
    finally:
        server.stop()


def _watchdogs() -> list[threading.Thread]:
    """Live timers of a hop deadline; other timers, such as the one of pytest-timeout, do not count."""
    return [t for t in threading.enumerate() if isinstance(t, threading.Timer) and isinstance(getattr(t.function, "__self__", None), _Deadline)]


def test_fast_connect_leaves_no_watchdog_threads(tmp_path: Path, locations: KnownHostsLocations) -> None:
    (tmp_path / "root").mkdir()
    server = StubSSHServer(root_dir=tmp_path / "root", host_key=make_host_key("rsa"))
    server.start()
    try:
        locations.user_known_hosts.write_text(key_line(host_key_name(server.host, server.port), server.host_key) + "\n")
        route = [SshHop("stub", server.host, server.port, "u", ())]
        fs = SSHFilesystem("stub", server.port, "u", None, _LOGIN_WORD, route=route, connect_timeout=30.0, known_hosts=locations)
        fs.close()
        end = time.monotonic() + 2.0
        while _watchdogs() and time.monotonic() < end:
            time.sleep(0.05)
        assert _watchdogs() == []
    finally:
        server.stop()


def test_timeout_per_hop_not_per_chain(tmp_path: Path, locations: KnownHostsLocations, monkeypatch: pytest.MonkeyPatch) -> None:
    for name in ("t", "b"):
        (tmp_path / name).mkdir()
    target = StubSSHServer(root_dir=tmp_path / "t", host_key=make_host_key("rsa"))
    target.start()
    bastion = StubSSHServer(
        root_dir=tmp_path / "b",
        host_key=make_host_key("ecdsa"),
        forward_map={("t.internal", 22): (target.host, target.port)},
    )
    bastion.start()
    original = paramiko.SSHClient.connect
    calls: list[float] = []

    def slow_connect(self: paramiko.SSHClient, *args: Any, **kwargs: Any) -> None:
        calls.append(time.monotonic())
        time.sleep(1.2)  # every hop uses 1.2 s of its own 2 s budget; the chain needs 2.4 s
        original(self, *args, **kwargs)

    monkeypatch.setattr(paramiko.SSHClient, "connect", slow_connect)
    try:
        locations.user_known_hosts.write_text(key_line(host_key_name(bastion.host, bastion.port), bastion.host_key) + "\n" + key_line("t.internal", target.host_key) + "\n")
        route = [SshHop("bastion", bastion.host, bastion.port, "j", ()), SshHop("t", "t.internal", 22, "u", ())]
        start = time.monotonic()
        fs = SSHFilesystem("t", 22, "u", None, _LOGIN_WORD, route=route, connect_timeout=2.0, known_hosts=locations, credentials=_JUMP_LOGIN)
        fs.close()
        assert len(calls) == 2
        assert time.monotonic() - start > 2.0
    finally:
        bastion.stop()
        target.stop()
