from __future__ import annotations

import os

import pytest

from nova_navigator.remotes.azure import AZURE_PLUGIN
from nova_navigator.vfs.filesystems import LocalFilesystem
from nova_navigator.vfs.scheme_registry import (
    SCHEME_REGISTRY,
    SchemeRegistry,
    register_common_schemes,
    vfspath_from_uri,
)
from nova_navigator.vfs.vpath import VPath


def test_register_and_find() -> None:
    registry = SchemeRegistry()

    class _TestConnector:
        async def resolve(self, path: str, netloc: str | None) -> VPath | None:
            return LocalFilesystem.singleton().path(path)

    connector = _TestConnector()
    registry.register_scheme("myscheme", connector)
    found = registry.find("myscheme")
    assert found is connector


def test_find_unknown_returns_none() -> None:
    registry = SchemeRegistry()
    assert registry.find("notregistered") is None


@pytest.mark.asyncio
async def test_vfspath_from_uri_local() -> None:
    register_common_schemes()
    vpath = await vfspath_from_uri("/usr")
    assert vpath is not None
    assert str(vpath.path) == "/usr"
    assert isinstance(vpath.filesystem, LocalFilesystem)


@pytest.mark.asyncio
async def test_vfspath_from_uri_file_scheme() -> None:
    register_common_schemes()
    vpath = await vfspath_from_uri("file:///usr")
    assert vpath is not None
    assert str(vpath.path) == "/usr"
    assert isinstance(vpath.filesystem, LocalFilesystem)


@pytest.mark.asyncio
async def test_vfspath_from_uri_unknown_scheme_raises() -> None:
    with pytest.raises(ValueError, match="Unknown scheme"):
        await vfspath_from_uri("ftp://host/path")


@pytest.mark.asyncio
async def test_vfspath_from_uri_no_scheme() -> None:
    register_common_schemes()
    vpath = await vfspath_from_uri("/usr/local/bin")
    assert vpath is not None
    assert str(vpath.path) == "/usr/local/bin"


@pytest.mark.asyncio
async def test_vfspath_from_uri_tilde_expands_to_home() -> None:
    register_common_schemes()
    vpath = await vfspath_from_uri("~/Documents")
    assert vpath is not None
    assert str(vpath.path) == os.path.join(os.path.expanduser("~"), "Documents")
    assert isinstance(vpath.filesystem, LocalFilesystem)


@pytest.mark.asyncio
async def test_vfspath_from_uri_azure_scheme() -> None:
    from unittest.mock import patch

    from nova_navigator.vfs.filesystems.azure import AzureFilesystem

    SCHEME_REGISTRY.register_scheme("azure", AZURE_PLUGIN.connector)
    with (
        patch("nova_navigator.vfs.filesystems.azure.ContainerClient"),
        patch("nova_navigator.vfs.filesystems.azure.DefaultAzureCredential"),
    ):
        vpath = await vfspath_from_uri("azure://myaccount.blob.core.windows.net/mycontainer/folder/file.txt")
    assert vpath is not None
    assert isinstance(vpath.filesystem, AzureFilesystem)
    assert str(vpath.path) == "/folder/file.txt"


@pytest.mark.asyncio
async def test_vfspath_from_uri_azure_root() -> None:
    from unittest.mock import patch

    from nova_navigator.vfs.filesystems.azure import AzureFilesystem

    SCHEME_REGISTRY.register_scheme("azure", AZURE_PLUGIN.connector)
    with (
        patch("nova_navigator.vfs.filesystems.azure.ContainerClient"),
        patch("nova_navigator.vfs.filesystems.azure.DefaultAzureCredential"),
    ):
        vpath = await vfspath_from_uri("azure://myaccount.blob.core.windows.net/mycontainer/")
    assert vpath is not None
    assert isinstance(vpath.filesystem, AzureFilesystem)
    assert str(vpath.path) == "/"
