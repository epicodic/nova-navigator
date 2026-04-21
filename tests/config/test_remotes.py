from __future__ import annotations

from pathlib import Path

import pytest


def test_remote_config_empty_defaults(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    from nova_navigator.config import loader

    monkeypatch.setattr(loader, "_APP_CONFIG_DIR", tmp_path)

    from nova_navigator.config.remotes import RemoteConfig

    cfg = RemoteConfig.load()
    assert cfg._items == []


def test_remote_config_writes_file_on_first_load(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    from nova_navigator.config import loader

    monkeypatch.setattr(loader, "_APP_CONFIG_DIR", tmp_path)

    from nova_navigator.config.remotes import RemoteConfig

    RemoteConfig.load()
    assert (tmp_path / "remotes.toml").exists()


def test_remote_connection_round_trip(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    from nova_navigator.config import loader

    monkeypatch.setattr(loader, "_APP_CONFIG_DIR", tmp_path)

    from nova_navigator.config.remotes import ProxySettings, RemoteConfig, RemoteConnection, SshSettings

    conn = RemoteConnection(
        name="myserver",
        uri="ssh://user@example.com",
        icon="server",
        ssh=SshSettings(host="192.168.1.10", user="alice", port=2222, identity_file="~/.ssh/foo"),
        proxy=ProxySettings(host="proxy.example.com", port=1080),
    )
    cfg = RemoteConfig.load()
    cfg._items = [conn]
    cfg.save()

    cfg2 = RemoteConfig.load()
    assert len(cfg2._items) == 1
    loaded = cfg2._items[0]
    assert loaded.name == "myserver"
    assert loaded.uri == "ssh://user@example.com"
    assert loaded.icon == "server"
    assert loaded.ssh is not None
    assert loaded.ssh.host == "192.168.1.10"
    assert loaded.ssh.user == "alice"
    assert loaded.ssh.port == 2222
    assert loaded.ssh.identity_file == "~/.ssh/foo"
    assert loaded.proxy is not None
    assert loaded.proxy.host == "proxy.example.com"
    assert loaded.proxy.port == 1080


def test_remote_connection_optional_fields_none(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    from nova_navigator.config import loader

    monkeypatch.setattr(loader, "_APP_CONFIG_DIR", tmp_path)

    from nova_navigator.config.remotes import RemoteConfig, RemoteConnection

    conn = RemoteConnection(name="minimal", uri="sftp://host/")
    cfg = RemoteConfig.load()
    cfg._items = [conn]
    cfg.save()

    cfg2 = RemoteConfig.load()
    assert len(cfg2._items) == 1
    loaded = cfg2._items[0]
    assert loaded.ssh is None
    assert loaded.proxy is None


def test_remote_connection_key_field(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    from nova_navigator.config import loader

    monkeypatch.setattr(loader, "_APP_CONFIG_DIR", tmp_path)

    from nova_navigator.config.remotes import RemoteConfig, RemoteConnection

    conn = RemoteConnection(name="myhost", uri="ssh://myhost/")
    cfg = RemoteConfig.load()
    cfg._items = [conn]
    cfg.save()

    cfg2 = RemoteConfig.load()
    assert len(cfg2._items) == 1
    assert cfg2._items[0].name == "myhost"
