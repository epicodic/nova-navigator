"""RemoteConfig — saved remote connection entries."""

from __future__ import annotations

from dataclasses import dataclass
from typing import ClassVar

from nova_navigator.config.loader import ListConfig
from nova_navigator.config.model import BaseModel, key_field


@dataclass
class SshSettings(BaseModel):
    """SSH protocol settings for a remote connection."""

    host: str = ""
    user: str | None = None
    port: int | None = None
    identity_file: str | None = None


@dataclass
class ProxySettings(BaseModel):
    """Proxy settings for a remote connection."""

    type: str = "socks5"
    host: str = ""
    port: int = 1080


@dataclass
class RemoteConnection(BaseModel):
    """A single saved remote connection entry."""

    name: str = key_field()
    uri: str = ""
    icon: str | None = None
    ssh: SshSettings | None = None
    proxy: ProxySettings | None = None


class RemoteConfig(ListConfig):
    """Saved remote connection config backed by remotes.toml."""

    CONFIG_NAME: ClassVar[str] = "remotes"
    _item_cls: ClassVar[type[BaseModel]] = RemoteConnection

    @classmethod
    def default_items(cls) -> list[BaseModel]:
        return []
