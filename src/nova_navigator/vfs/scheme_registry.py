"""SchemeRegistry — maps URI scheme strings to VPath-producing handler functions."""

from __future__ import annotations

import os
from collections.abc import Callable

from nova_navigator.vfs.filesystems import LocalFilesystem
from nova_navigator.vfs.parse_uri import parse_uri
from nova_navigator.vfs.vpath import VPath

SchemeHandler = Callable[[str, str | None], VPath]
"""Handler callable receiving ``(path, netloc)`` from the parsed URI component."""


class SchemeRegistry:
    """Registry mapping URI scheme strings to :data:`SchemeHandler` callables."""

    _schemes: dict[str, SchemeHandler]

    def __init__(self) -> None:
        self._schemes = {}

    def register_scheme(self, scheme: str, handler: SchemeHandler) -> None:
        """Register *handler* for *scheme*."""
        self._schemes[scheme] = handler

    def find(self, scheme: str) -> SchemeHandler | None:
        """Return the handler for *scheme*, or ``None`` if not registered."""
        return self._schemes.get(scheme)


SCHEME_REGISTRY: SchemeRegistry = SchemeRegistry()
"""Process-wide scheme registry."""


def vfspath_from_uri(uri: str) -> VPath:
    """Resolve *uri* to a :class:`~nova_navigator.vfs.VPath` using the registered handlers.

    Only the outermost URI component is resolved.

    Raises:
        ValueError: If the scheme has no registered handler.
    """
    result = parse_uri(uri)
    component = result.components[0]
    scheme = component.scheme or ""
    handler = SCHEME_REGISTRY.find(scheme)
    if not handler:
        raise ValueError(f"Unknown scheme: {scheme!r}")
    return handler(component.path, component.netloc)


def local_uri(path: str, _netloc: str | None) -> VPath:
    """Resolve a local ``file://`` or bare-path URI to a :class:`~nova_navigator.vfs.VPath`."""
    path = os.path.expandvars(path)
    return LocalFilesystem.singleton().path(path)


def register_common_schemes() -> None:
    """Register built-in URI schemes: ``file`` and the empty (bare-path) scheme."""
    SCHEME_REGISTRY.register_scheme("file", local_uri)
    SCHEME_REGISTRY.register_scheme("", local_uri)
