"""Integration tests for AzureFilesystem against the pinned Azurite container.

The container is started once per process by `tests.real_servers.azurite`; without Docker the tests skip, and
they fail instead when real servers are required (CI or NOVA_REQUIRE_REAL_SERVERS).
"""

from __future__ import annotations

import contextlib

import pytest
from azure.core.exceptions import ResourceExistsError
from azure.storage.blob import BlobServiceClient, ContainerClient

from nova_navigator.vfs.filesystems.azure import AzureFilesystem
from tests.real_servers.azurite import ACCOUNT, ACCOUNT_KEY, Azurite, azurite_or_unavailable

pytestmark = [pytest.mark.real_server, pytest.mark.integration]

_CONTAINER = "testcontainer"


@pytest.fixture(scope="session")
def azurite() -> Azurite:
    """The shared Azurite container, or a skip or failure of the requesting test."""
    return azurite_or_unavailable()


@pytest.fixture(scope="session")
def container_client(azurite: Azurite) -> ContainerClient:
    """Create the test container once per session and return its ContainerClient."""
    connection_string = f"DefaultEndpointsProtocol=https;AccountName={ACCOUNT};AccountKey={ACCOUNT_KEY};BlobEndpoint={azurite.path_style_url};"
    svc = BlobServiceClient.from_connection_string(connection_string, connection_verify=str(azurite.cert))
    with contextlib.suppress(ResourceExistsError):
        svc.create_container(_CONTAINER)
    return svc.get_container_client(_CONTAINER)


@pytest.fixture
def fs(azurite: Azurite, container_client: ContainerClient) -> AzureFilesystem:
    """Return a fresh AzureFilesystem for each test, with container wiped clean."""
    for blob in container_client.list_blobs():
        container_client.get_blob_client(blob.name).delete_blob()
    return AzureFilesystem(azurite.path_style_url, _CONTAINER, client=container_client)


# ── tests ─────────────────────────────────────────────────────────────────────


@pytest.mark.asyncio
async def test_list_empty_container(fs: AzureFilesystem) -> None:
    result = [p async for p in fs.iterdir(fs.path("/"))]
    assert result == []


def test_write_read_roundtrip(fs: AzureFilesystem) -> None:
    writer = fs.write(fs.path("/hello.txt"))
    writer.write(b"hello world")
    writer.close()

    reader = fs.read(fs.path("/hello.txt"))
    data = reader.read(1024)
    reader.close()
    assert data == b"hello world"


def test_stat_after_write(fs: AzureFilesystem) -> None:
    writer = fs.write(fs.path("/sized.txt"))
    writer.write(b"abc")
    writer.close()

    s = fs.stat(fs.path("/sized.txt"))
    assert s.size == 3
    assert s.modified > 0


def test_remove(fs: AzureFilesystem) -> None:
    writer = fs.write(fs.path("/todelete.txt"))
    writer.write(b"x")
    writer.close()

    fs.remove(fs.path("/todelete.txt"))

    with pytest.raises(FileNotFoundError):
        fs.stat(fs.path("/todelete.txt"))


@pytest.mark.asyncio
async def test_mkdir_and_iterdir(fs: AzureFilesystem) -> None:
    fs.mkdir(fs.path("/mydir"))
    paths = [str(p.path) async for p in fs.iterdir(fs.path("/"))]
    assert "/mydir" in paths


def test_rename(fs: AzureFilesystem) -> None:
    writer = fs.write(fs.path("/original.txt"))
    writer.write(b"content")
    writer.close()

    fs.rename(fs.path("/original.txt"), fs.path("/renamed.txt"))

    with pytest.raises(FileNotFoundError):
        fs.stat(fs.path("/original.txt"))

    s = fs.stat(fs.path("/renamed.txt"))
    assert s.size == len(b"content")
