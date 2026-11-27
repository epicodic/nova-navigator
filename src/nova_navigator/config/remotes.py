"""RemoteConfig — saved remote connection entries."""

from __future__ import annotations

from dataclasses import dataclass
from typing import ClassVar, Self, override

from nova_navigator.config.loader import ListConfig
from nova_navigator.config.model import BaseModel, key_field
from nova_navigator.secret_registry import register_uri_secrets, register_userinfo


@dataclass
class SshSettings(BaseModel):
    """SSH protocol settings for a remote connection."""

    host: str = ""
    user: str | None = None
    port: int | None = None
    identity_file: str | None = None


@dataclass
class AzureSettings(BaseModel):
    """Azure Blob Storage settings for a remote connection."""

    account_url: str = ""
    container: str = ""
    use_managed_identity: bool = False


@dataclass
class ProxySettings(BaseModel):
    """Proxy settings for a remote connection."""

    host: str = ""
    port: int = 1080


@dataclass
class RemoteConnection(BaseModel):
    """A single saved remote connection entry."""

    name: str = key_field()
    icon: str | None = None
    ssh: SshSettings | None = None
    azure: AzureSettings | None = None
    proxy: ProxySettings | None = None


class RemoteConfig(ListConfig):
    """Saved remote connection config backed by remotes.toml."""

    CONFIG_NAME: ClassVar[str] = "remotes"
    _item_cls: ClassVar[type[BaseModel]] = RemoteConnection

    @classmethod
    def default_items(cls) -> list[BaseModel]:
        return []

    @classmethod
    @override
    def load(cls) -> Self:
        """Load the connections and register passwords written into their settings."""
        config = super().load()
        for item in config._items:
            if not isinstance(item, RemoteConnection):
                continue
            if item.ssh is not None:
                register_userinfo(item.ssh.user)
                if "@" in item.ssh.host:
                    register_userinfo(item.ssh.host.rsplit("@", 1)[0])
            if item.azure is not None:
                register_uri_secrets(item.azure.account_url)
        return config
