from __future__ import annotations

import pytest

from nova_navigator.vfs.filesystems import LocalFilesystem
from nova_navigator.vfs.scheme_registry import (
    SchemeRegistry,
    register_common_schemes,
    vfspath_from_uri,
)
from nova_navigator.vfs.vpath import VPath


def test_register_and_find() -> None:
    registry = SchemeRegistry()
    handler_called: list[tuple[str, str | None]] = []

    def _handler(path: str, netloc: str | None) -> VPath:
        handler_called.append((path, netloc))
        return LocalFilesystem.singleton().path(path)

    registry.register_scheme("myscheme", _handler)
    found = registry.find("myscheme")
    assert found is _handler


def test_find_unknown_returns_none() -> None:
    registry = SchemeRegistry()
    assert registry.find("notregistered") is None


def test_vfspath_from_uri_local() -> None:
    register_common_schemes()
    vpath = vfspath_from_uri("/usr")
    assert str(vpath.path) == "/usr"
    assert isinstance(vpath.filesystem, LocalFilesystem)


def test_vfspath_from_uri_file_scheme() -> None:
    register_common_schemes()
    vpath = vfspath_from_uri("file:///usr")
    assert str(vpath.path) == "/usr"
    assert isinstance(vpath.filesystem, LocalFilesystem)


def test_vfspath_from_uri_unknown_scheme_raises() -> None:
    with pytest.raises(ValueError, match="Unknown scheme"):
        vfspath_from_uri("ftp://host/path")


def test_vfspath_from_uri_no_scheme() -> None:
    register_common_schemes()
    vpath = vfspath_from_uri("/usr/local/bin")
    assert str(vpath.path) == "/usr/local/bin"


def test_vfspath_from_uri_azure_scheme() -> None:
    from unittest.mock import patch

    from nova_navigator.vfs.filesystems.azure import AzureFilesystem

    register_common_schemes()
    with (
        patch("nova_navigator.vfs.filesystems.azure.ContainerClient"),
        patch("nova_navigator.vfs.filesystems.azure.DefaultAzureCredential"),
    ):
        vpath = vfspath_from_uri("azure://myaccount.blob.core.windows.net/mycontainer/folder/file.txt")
    assert isinstance(vpath.filesystem, AzureFilesystem)
    assert str(vpath.path) == "/folder/file.txt"


def test_vfspath_from_uri_azure_root() -> None:
    from unittest.mock import patch

    from nova_navigator.vfs.filesystems.azure import AzureFilesystem

    register_common_schemes()
    with (
        patch("nova_navigator.vfs.filesystems.azure.ContainerClient"),
        patch("nova_navigator.vfs.filesystems.azure.DefaultAzureCredential"),
    ):
        vpath = vfspath_from_uri("azure://myaccount.blob.core.windows.net/mycontainer/")
    assert isinstance(vpath.filesystem, AzureFilesystem)
    assert str(vpath.path) == "/"
