"""Integration tests for AzureFilesystem against a local Azurite emulator.

Skipped automatically when `azurite-blob` is not on PATH.
Install Azurite with: npm install -g azurite
"""

from __future__ import annotations

import contextlib
import shutil
import subprocess
import tempfile
import time
from collections.abc import Generator

import pytest
from azure.core.exceptions import ResourceExistsError
from azure.storage.blob import BlobServiceClient, ContainerClient

from nova_navigator.vfs.filesystems.azure import AzureFilesystem

# ── Azurite setup ─────────────────────────────────────────────────────────────

_AZURITE_CONN_STR = (
    "DefaultEndpointsProtocol=http;"
    "AccountName=devstoreaccount1;"
    "AccountKey=Eby8vdM02xNOcqFlqUwJPLlmEtlCDXJ1OUzFT50uSRZ6IFsuFq2UVErCz4I6tq/K1SZFPTOtr/KBHBeksoGMGw==;"
    "BlobEndpoint=http://127.0.0.1:10000/devstoreaccount1;"
)
_CONTAINER = "testcontainer"
_ACCOUNT_URL = "http://127.0.0.1:10000/devstoreaccount1"


def _azurite_available() -> bool:
    return shutil.which("azurite-blob") is not None


@pytest.fixture(scope="session")
def azurite_process() -> Generator[subprocess.Popen[bytes], None, None]:
    """Start azurite-blob on port 10000; skip session if not available."""
    if not _azurite_available():
        pytest.skip("azurite-blob not on PATH — skipping integration tests")
    tmp_dir = tempfile.mkdtemp(prefix="azurite_")
    proc = subprocess.Popen(
        ["azurite-blob", "--blobPort", "10000", "--blobHost", "127.0.0.1", "--silent", "--location", tmp_dir],
        stdout=subprocess.DEVNULL,
        stderr=subprocess.DEVNULL,
    )
    time.sleep(1.5)
    yield proc
    proc.terminate()
    proc.wait()


@pytest.fixture(scope="session")
def container_client(azurite_process: subprocess.Popen[bytes]) -> ContainerClient:
    """Create the test container once per session and return its ContainerClient."""
    assert azurite_process.poll() is None, "azurite-blob process terminated unexpectedly"
    svc = BlobServiceClient.from_connection_string(_AZURITE_CONN_STR)
    with contextlib.suppress(ResourceExistsError):
        svc.create_container(_CONTAINER)
    return svc.get_container_client(_CONTAINER)


@pytest.fixture
def fs(container_client: ContainerClient) -> AzureFilesystem:
    """Return a fresh AzureFilesystem for each test, with container wiped clean."""
    for blob in container_client.list_blobs():
        container_client.get_blob_client(blob.name).delete_blob()
    return AzureFilesystem(_ACCOUNT_URL, _CONTAINER, client=container_client)


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
