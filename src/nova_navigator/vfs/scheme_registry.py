"""SchemeRegistry — maps URI scheme strings to async Connector instances."""

from __future__ import annotations

import os
from typing import Protocol

from nova_navigator.vfs.filesystems import LocalFilesystem
from nova_navigator.vfs.parse_uri import parse_uri
from nova_navigator.vfs.vpath import VPath


class Connector(Protocol):
    """Protocol for URI scheme handlers.

    Implementations may perform async I/O (e.g., establishing a network connection)
    and may show UI dialogs for authentication or confirmation.
    Return ``None`` to signal that the user cancelled the operation.
    """

    async def resolve(self, path: str, netloc: str | None) -> VPath | None:
        """Resolve a URI component to a VPath.

        Args:
            path: The path component of the URI.
            netloc: The netloc component of the URI, or None.

        Returns:
            A VPath, or None if the operation was cancelled by the user.
        """
        ...


class SchemeRegistry:
    """Registry mapping URI scheme strings to :class:`Connector` instances."""

    _connectors: dict[str, Connector]

    def __init__(self) -> None:
        self._connectors = {}

    def register_scheme(self, scheme: str, connector: Connector) -> None:
        """Register *connector* for *scheme*."""
        self._connectors[scheme] = connector

    def find(self, scheme: str) -> Connector | None:
        """Return the connector for *scheme*, or ``None`` if not registered."""
        return self._connectors.get(scheme)


SCHEME_REGISTRY: SchemeRegistry = SchemeRegistry()
"""Process-wide scheme registry."""


async def vfspath_from_uri(uri: str) -> VPath | None:
    """Resolve *uri* to a :class:`~nova_navigator.vfs.VPath` using registered connectors.

    Only the outermost URI component is resolved.
    Returns ``None`` if the operation was cancelled by the user (e.g., auth dialog dismissed).

    Raises:
        ValueError: If the scheme has no registered connector.
    """
    result = parse_uri(uri)
    component = result.components[0]
    scheme = component.scheme or ""
    connector = SCHEME_REGISTRY.find(scheme)
    if not connector:
        raise ValueError(f"Unknown scheme: {scheme!r}")
    return await connector.resolve(component.path, component.netloc)


class _LocalConnector:
    async def resolve(self, path: str, netloc: str | None) -> VPath | None:
        return LocalFilesystem.singleton().path(os.path.expanduser(os.path.expandvars(path)))


_LOCAL_CONNECTOR = _LocalConnector()


def register_common_schemes() -> None:
    """Register built-in URI schemes: ``file`` and bare-path."""
    SCHEME_REGISTRY.register_scheme("file", _LOCAL_CONNECTOR)
    SCHEME_REGISTRY.register_scheme("", _LOCAL_CONNECTOR)
