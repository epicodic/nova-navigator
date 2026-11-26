"""``resolve_route``: aliases, explicit values, ProxyJump chains, loops and ProxyCommand."""

from __future__ import annotations

import getpass
import logging
from pathlib import Path

import pytest

from nova_navigator.remotes.ssh_config import SshConfig, SshOptions
from nova_navigator.remotes.ssh_route import MAX_HOPS, Explicit, SshHop, SshRouteError, resolve_route

SRC = Path(__file__).resolve().parents[2] / "src" / "nova_navigator" / "remotes"


def _config(tmp_path: Path, text: str) -> SshConfig:
    path = tmp_path / "config"
    path.write_text(text)
    return SshConfig.load([path])


@pytest.fixture(autouse=True)
def local_user(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(getpass, "getuser", lambda: "localuser")


def test_no_entry_uses_defaults(tmp_path: Path) -> None:
    assert resolve_route("h.example", Explicit(), _config(tmp_path, "")) == [SshHop("h.example", "h.example", 22, "localuser", ())]


def test_alias_resolves_hostname_user_port_identity(tmp_path: Path) -> None:
    config = _config(tmp_path, "Host myalias\n  HostName 10.0.0.5\n  User alice\n  Port 2222\n  IdentityFile /k/id\n")
    assert resolve_route("myalias", Explicit(), config) == [SshHop("myalias", "10.0.0.5", 2222, "alice", ("/k/id",))]


def test_explicit_values_win_over_config(tmp_path: Path) -> None:
    config = _config(tmp_path, "Host myalias\n  HostName 10.0.0.5\n  User alice\n  Port 2222\n  IdentityFile /k/id\n")
    route = resolve_route("myalias", Explicit(user="bob", port=2022, identity_file="/other/id"), config)
    assert route == [SshHop("myalias", "10.0.0.5", 2022, "bob", ("/other/id",))]


def test_explicit_values_never_apply_to_jump_hosts(tmp_path: Path) -> None:
    config = _config(tmp_path, "Host t\n  ProxyJump j\nHost j\n  User jumper\n  Port 2200\n")
    jump, target = resolve_route("t", Explicit(user="bob", port=2022, identity_file="/id"), config)
    assert (jump.user, jump.port, jump.identity_files) == ("jumper", 2200, ())
    assert (target.user, target.port, target.identity_files) == ("bob", 2022, ("/id",))


def test_single_jump(tmp_path: Path) -> None:
    config = _config(tmp_path, "Host db\n  HostName 10.0.0.9\n  ProxyJump bastion\nHost bastion\n  HostName b.example.com\n  User ops\n  Port 2200\n  IdentityFile /k/b\n")
    assert resolve_route("db", Explicit(), config) == [
        SshHop("bastion", "b.example.com", 2200, "ops", ("/k/b",)),
        SshHop("db", "10.0.0.9", 22, "localuser", ()),
    ]


def test_chain_a_b_is_ordered(tmp_path: Path) -> None:
    config = _config(tmp_path, "Host t\n  ProxyJump a,b\n")
    assert [h.alias for h in resolve_route("t", Explicit(), config)] == ["a", "b", "t"]


def test_user_host_port_written_in_proxy_jump_override_config(tmp_path: Path) -> None:
    config = _config(tmp_path, "Host t\n  ProxyJump ops@j.example:2299\nHost j.example\n  User cfguser\n  Port 1\n")
    jump = resolve_route("t", Explicit(), config)[0]
    assert (jump.alias, jump.user, jump.port) == ("j.example", "ops", 2299)


@pytest.mark.parametrize(("spec", "host", "user", "port"), [("ssh://ops@j:2201", "j", "ops", 2201), ("[::1]:2202", "::1", None, 2202), ("j", "j", None, None)])
def test_proxy_jump_spec_forms(tmp_path: Path, spec: str, host: str, user: str | None, port: int | None) -> None:
    jump = resolve_route("t", Explicit(), _config(tmp_path, f"Host t\n  ProxyJump {spec}\n"))[0]
    assert (jump.alias, jump.port) == (host, port or 22)
    assert jump.user == (user or "localuser")


def test_jump_with_its_own_proxy_jump_goes_through_its_jump_first(tmp_path: Path) -> None:
    config = _config(tmp_path, "Host t\n  ProxyJump b\nHost b\n  ProxyJump a\n")
    assert [h.alias for h in resolve_route("t", Explicit(), config)] == ["a", "b", "t"]


@pytest.mark.parametrize("none", ["none", "None", "NONE"])
def test_proxy_jump_none_connects_directly(tmp_path: Path, none: str) -> None:
    config = _config(tmp_path, f"Host t\n  ProxyJump {none}\n")
    assert [h.alias for h in resolve_route("t", Explicit(), config)] == ["t"]


def test_loop_raises_naming_the_host(tmp_path: Path) -> None:
    config = _config(tmp_path, "Host a\n  ProxyJump b\nHost b\n  ProxyJump a\n")
    with pytest.raises(SshRouteError, match="'a'"):
        resolve_route("a", Explicit(), config)


def test_self_reference_is_a_loop(tmp_path: Path) -> None:
    with pytest.raises(SshRouteError, match="'a'"):
        resolve_route("a", Explicit(), _config(tmp_path, "Host a\n  ProxyJump a\n"))


def test_depth_cap(tmp_path: Path) -> None:
    lines = "".join(f"Host h{i}\n  ProxyJump h{i + 1}\n" for i in range(MAX_HOPS + 2))
    with pytest.raises(SshRouteError, match="more than"):
        resolve_route("h0", Explicit(), _config(tmp_path, lines))


def test_proxy_command_is_ignored_logged_and_never_run(tmp_path: Path, caplog: pytest.LogCaptureFixture) -> None:
    marker = tmp_path / "marker"
    caplog.set_level(logging.INFO)
    config = _config(tmp_path, f"Host t\n  ProxyCommand touch {marker}\n")
    route = resolve_route("t", Explicit(), config)
    assert [h.alias for h in route] == ["t"]
    assert not marker.exists()
    assert any("ProxyCommand" in r.getMessage() for r in caplog.records)
    assert str(marker) not in caplog.text


def test_proxy_jump_wins_over_proxy_command(tmp_path: Path) -> None:
    config = _config(tmp_path, "Host t\n  ProxyJump j\n  ProxyCommand nc %h %p\n")
    assert [h.alias for h in resolve_route("t", Explicit(), config)] == ["j", "t"]


class _PlainConfig(SshConfig):
    """Config whose lookup does not touch ``getpass`` (paramiko's own token expansion does)."""

    def lookup(self, host: str) -> SshOptions:
        return SshOptions(hostname=host)


def test_user_stays_none_when_local_user_is_unknown(monkeypatch: pytest.MonkeyPatch) -> None:
    def fail() -> str:
        raise KeyError("no user")

    monkeypatch.setattr(getpass, "getuser", fail)
    assert resolve_route("h", Explicit(), _PlainConfig())[0].user is None


@pytest.mark.parametrize("module", ["ssh_config.py", "ssh_route.py"])
def test_resolver_modules_are_textual_vfs_and_process_free(module: str) -> None:
    text = (SRC / module).read_text(encoding="utf-8")
    for word in ("textual", "nova_navigator.vfs", "nova_navigator.dialogs", "subprocess", "os.system", "Popen", "ssh-keygen"):
        assert word not in text


def test_route_of_exactly_max_hops_is_accepted(tmp_path: Path) -> None:
    jumps = ",".join(f"j{i}" for i in range(MAX_HOPS - 1))
    assert len(resolve_route("t", Explicit(), _config(tmp_path, f"Host t\n  ProxyJump {jumps}\n"))) == MAX_HOPS


def test_route_of_max_hops_plus_one_is_rejected(tmp_path: Path) -> None:
    jumps = ",".join(f"j{i}" for i in range(MAX_HOPS))
    with pytest.raises(SshRouteError, match="more than"):
        resolve_route("t", Explicit(), _config(tmp_path, f"Host t\n  ProxyJump {jumps}\n"))
