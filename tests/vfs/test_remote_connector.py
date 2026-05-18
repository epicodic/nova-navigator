"""Tests for RemoteConnector and register_remote_scheme."""

from __future__ import annotations

from unittest.mock import AsyncMock, patch

import pytest

from nova_navigator.config.remotes import AzureSettings, RemoteConfig, RemoteConnection, SshSettings
from nova_navigator.vfs.filesystems.remote import RemoteFilesystem


def _make_config(*connections: RemoteConnection) -> RemoteConfig:
    config = object.__new__(RemoteConfig)
    config._items = list(connections)
    return config


# ---------------------------------------------------------------------------
# RemoteConnector
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_unknown_remote_raises_value_error() -> None:
    from nova_navigator.remotes.remote import RemoteConnector

    config = _make_config()
    connector = RemoteConnector(config)
    with pytest.raises(ValueError, match="Unknown remote"):
        await connector.resolve("/", "no-such-remote")


@pytest.mark.asyncio
async def test_ssh_remote_wraps_in_remote_filesystem() -> None:
    from nova_navigator.remotes.remote import RemoteConnector
    from tests._utils.mock_filesystem import MockFilesystem

    conn = RemoteConnection(name="dev-box", ssh=SshSettings(host="dev.example.com"))
    config = _make_config(conn)
    connector = RemoteConnector(config)

    mock_fs = MockFilesystem()
    with patch("nova_navigator.remotes.remote.connect_ssh", new_callable=AsyncMock, return_value=mock_fs):
        vpath = await connector.resolve("/home/user", "dev-box")

    assert vpath is not None
    assert isinstance(vpath.filesystem, RemoteFilesystem)
    assert vpath.filesystem._remote_name == "dev-box"
    assert str(vpath.path) == "/home/user"


@pytest.mark.asyncio
async def test_ssh_remote_returns_none_when_connect_ssh_returns_none() -> None:
    from nova_navigator.remotes.remote import RemoteConnector

    conn = RemoteConnection(name="box", ssh=SshSettings(host="host"))
    config = _make_config(conn)
    connector = RemoteConnector(config)

    with patch("nova_navigator.remotes.remote.connect_ssh", new_callable=AsyncMock, return_value=None):
        result = await connector.resolve("/", "box")

    assert result is None


@pytest.mark.asyncio
async def test_remote_with_no_protocol_raises_value_error() -> None:
    from nova_navigator.remotes.remote import RemoteConnector

    conn = RemoteConnection(name="empty")  # no ssh, no azure
    config = _make_config(conn)
    connector = RemoteConnector(config)
    with pytest.raises(ValueError, match="no protocol"):
        await connector.resolve("/", "empty")


@pytest.mark.asyncio
async def test_ssh_with_empty_host_falls_through_to_azure() -> None:
    """An SshSettings with no host must not shadow a valid Azure config."""
    from nova_navigator.remotes.remote import RemoteConnector
    from tests._utils.mock_filesystem import MockFilesystem

    conn = RemoteConnection(
        name="az",
        ssh=SshSettings(host=""),  # present but empty — should be ignored
        azure=AzureSettings(account_url="https://example.blob.core.windows.net", container="data"),
    )
    config = _make_config(conn)
    connector = RemoteConnector(config)

    mock_fs = MockFilesystem()
    with patch("nova_navigator.remotes.remote.connect_azure", new_callable=AsyncMock, return_value=mock_fs):
        vpath = await connector.resolve("/", "az")

    assert vpath is not None
    assert isinstance(vpath.filesystem, RemoteFilesystem)


@pytest.mark.asyncio
async def test_empty_path_defaults_to_root() -> None:
    from nova_navigator.remotes.remote import RemoteConnector
    from tests._utils.mock_filesystem import MockFilesystem

    conn = RemoteConnection(name="srv", ssh=SshSettings(host="srv"))
    config = _make_config(conn)
    connector = RemoteConnector(config)

    mock_fs = MockFilesystem()
    with patch("nova_navigator.remotes.remote.connect_ssh", new_callable=AsyncMock, return_value=mock_fs):
        vpath = await connector.resolve("", "srv")

    assert vpath is not None
    assert str(vpath.path) == "/"


# ---------------------------------------------------------------------------
# register_remote_scheme
# ---------------------------------------------------------------------------


def test_register_remote_scheme_registers_remote_scheme() -> None:
    from nova_navigator.remotes.remote import RemoteConnector, register_remote_scheme
    from nova_navigator.vfs.scheme_registry import SchemeRegistry

    registry = SchemeRegistry()
    config = _make_config()

    register_remote_scheme(config, registry=registry)

    connector = registry.find("remote")
    assert isinstance(connector, RemoteConnector)
