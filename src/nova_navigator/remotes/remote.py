"""RemoteConnector — resolves ``remote://name/path`` against saved RemoteConfig."""

from __future__ import annotations

from nova_navigator.config.remotes import RemoteConfig
from nova_navigator.remotes.azure import connect_azure
from nova_navigator.remotes.ssh import connect_ssh
from nova_navigator.vfs.filesystems.remote import RemoteFilesystem
from nova_navigator.vfs.scheme_registry import SCHEME_REGISTRY, SchemeRegistry
from nova_navigator.vfs.vpath import VPath


class RemoteConnector:
    """Connector that resolves ``remote://name`` URIs against a :class:`RemoteConfig`.

    Looks up *netloc* (the connection name) in the saved remotes, connects using
    the appropriate protocol, and wraps the resulting filesystem in a
    :class:`~nova_navigator.vfs.filesystems.remote.RemoteFilesystem` so that the
    panel title shows ``remote://name/path`` instead of raw credentials.

    Returns ``None`` if the user cancels any interactive dialog.
    """

    def __init__(self, config: RemoteConfig) -> None:
        self._config = config

    async def resolve(self, path: str, netloc: str | None) -> VPath | None:
        name = netloc or ""
        conn = next((c for c in self._config._items if c.name == name), None)
        if conn is None:
            raise ValueError(f"Unknown remote: {name!r}")

        if conn.ssh is not None and conn.ssh.host:
            inner = await connect_ssh(conn)
        elif conn.azure is not None and conn.azure.account_url:
            inner = await connect_azure(conn)
        else:
            raise ValueError(f"Remote {name!r} has no protocol configured (add [ssh] or [azure] settings)")

        if inner is None:
            return None

        fs = RemoteFilesystem(name, inner)
        return VPath(path or "/", fs)


def register_remote_scheme(config: RemoteConfig, *, registry: SchemeRegistry = SCHEME_REGISTRY) -> None:
    """Register the ``remote`` scheme against *registry* (default: process-wide registry)."""
    registry.register_scheme("remote", RemoteConnector(config))
