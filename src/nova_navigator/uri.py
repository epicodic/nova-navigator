import os
from collections.abc import Callable
from urllib.parse import urlparse

from .vfs import VPath
from .vfs.filesystems import LocalFilesystem

SchemeHandler = Callable[[str], VPath]


class SchemeRegistry:
    _schemes: dict[str, SchemeHandler]

    def __init__(self) -> None:
        self._schemes = {}

    def registerScheme(self, scheme: str, handler: SchemeHandler) -> None:
        self._schemes[scheme] = handler

    def find(self, scheme: str) -> SchemeHandler | None:
        return self._schemes.get(scheme, None)


SCHEME_REGISTRY = SchemeRegistry()


def vfspath_from_uri(uri: str) -> VPath:
    parts = urlparse(uri)

    handler = SCHEME_REGISTRY.find(parts.scheme)
    if not handler:
        raise ValueError(f"Unknown scheme: {parts.scheme}")

    return handler(parts.path)


# common URI handlers


def local_uri(path: str) -> VPath:
    # replace environment variables
    path = os.path.expandvars(path)
    return LocalFilesystem.singleton().path(path)


def register_common_schemes() -> None:
    SCHEME_REGISTRY.registerScheme("file", local_uri)
    SCHEME_REGISTRY.registerScheme("", local_uri)
