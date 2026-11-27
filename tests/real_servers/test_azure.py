"""Azure Blob operations against the pinned Azurite container: connect, list, copy, edit, move and erase (A1-A3)."""

from __future__ import annotations

import socket
import uuid
from collections.abc import AsyncIterator, Callable, Generator
from contextlib import asynccontextmanager
from pathlib import Path

import pytest
from azure.core.exceptions import ResourceNotFoundError
from azure.storage.blob import BlobServiceClient

from nova_navigator.config.remotes import AzureSettings, RemoteConnection
from nova_navigator.filemanager.tasks import EraseFilesOptions, copy_files, erase_files, move_files
from nova_navigator.local_copies.manager import CopyStatus, LocalCopyManager
from nova_navigator.remotes.azure import AzureConnector, connect_azure
from nova_navigator.vfs.filesystems.azure import AzureFilesystem
from nova_navigator.vfs.filesystems.local import LocalFilesystem
from nova_navigator.vfs.filesystems.remote import RemoteFilesystem
from nova_navigator.vfs.vpath import VPath
from nova_widgets import Response
from tests._utils.local_copy_helpers import ScriptedRunner
from tests.filemanager.common import run_task
from tests.real_servers.azurite import ACCOUNT, ACCOUNT_KEY, Azurite, azurite_or_unavailable

pytestmark = [pytest.mark.real_server, pytest.mark.integration, pytest.mark.usefixtures("azure_env")]

PAYLOAD = b"payload\n"


@pytest.fixture
def azurite() -> Azurite:
    """The shared Azurite container, or a skip or failure of the requesting test."""
    return azurite_or_unavailable()


@pytest.fixture
def azure_env(azurite: Azurite, tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Path:
    """Point the environment at Azurite: the fake `az` credential, the CA bundle and an isolated HOME."""
    home = tmp_path / "home"
    home.mkdir()
    for name, value in azurite.environment(home).items():
        monkeypatch.setenv(name, value)
    return home


def _service(azurite: Azurite) -> BlobServiceClient:
    """A test-side client that uses the account key, for arranging and checking blobs out of band."""
    connection_string = f"DefaultEndpointsProtocol=https;AccountName={ACCOUNT};AccountKey={ACCOUNT_KEY};BlobEndpoint={azurite.path_style_url};"
    return BlobServiceClient.from_connection_string(connection_string, connection_verify=str(azurite.cert))


@pytest.fixture
def new_container(azurite: Azurite) -> Generator[Callable[[], str], None, None]:
    """Return a factory of fresh containers; each one is deleted at teardown."""
    service = _service(azurite)
    made: list[str] = []

    def _make() -> str:
        name = f"nova-{uuid.uuid4().hex[:20]}"
        service.create_container(name)
        made.append(name)
        return name

    yield _make
    for name in made:
        service.delete_container(name)


@pytest.fixture
def container(new_container: Callable[[], str]) -> str:
    return new_container()


def _put(azurite: Azurite, container: str, name: str, data: bytes) -> None:
    _service(azurite).get_container_client(container).upload_blob(name, data, overwrite=True)


def _get(azurite: Azurite, container: str, name: str) -> bytes:
    data = _service(azurite).get_container_client(container).download_blob(name, encoding=None).readall()
    assert isinstance(data, bytes)
    return data


def _exists(azurite: Azurite, container: str, name: str) -> bool:
    try:
        _service(azurite).get_container_client(container).get_blob_client(name).get_blob_properties()
    except ResourceNotFoundError:
        return False
    return True


def _fs(azurite: Azurite, container: str) -> AzureFilesystem:
    return AzureFilesystem(azurite.path_style_url, container)


async def _names(fs: AzureFilesystem, directory: str) -> set[str]:
    return {str(p.path) async for p in fs.iterdir(fs.path(directory))}


def _local(path: Path) -> VPath:
    return LocalFilesystem().path(str(path))


@asynccontextmanager
async def _manager(tmp_path_factory: pytest.TempPathFactory, answers: list[Response] | None = None) -> AsyncIterator[tuple[LocalCopyManager, ScriptedRunner]]:
    runner = ScriptedRunner(answers)
    manager = LocalCopyManager(tmp_path_factory.mktemp("copies"), runner, poll_interval=0.05, settle_time=0.1)
    try:
        yield manager, runner
    finally:
        await manager.shutdown()


# ── connecting ────────────────────────────────────────────────────────────────


@pytest.mark.asyncio
async def test_connect_from_saved_settings(azurite: Azurite, container: str) -> None:
    _put(azurite, container, "hello.txt", PAYLOAD)
    conn = RemoteConnection(
        name="azurite",
        azure=AzureSettings(account_url=azurite.path_style_url, container=container, use_managed_identity=False),
    )
    fs = await connect_azure(conn)
    assert fs is not None
    assert await _names(fs, "/") == {"/hello.txt"}


@pytest.mark.asyncio
async def test_connect_from_uri(azurite: Azurite, container: str) -> None:
    host = f"{ACCOUNT}.blob.localhost"
    try:
        socket.getaddrinfo(host, azurite.port)
    except OSError as exc:
        pytest.fail(f"{host} does not resolve, so the host-style azure:// URI cannot reach Azurite: {exc}")
    _put(azurite, container, "hello.txt", PAYLOAD)
    vpath = await AzureConnector().resolve(f"/{container}/", f"{host}:{azurite.port}")
    assert vpath is not None
    fs = vpath.filesystem.unwrap()
    assert isinstance(fs, AzureFilesystem)
    assert await _names(fs, "/") == {"/hello.txt"}


# ── listing ───────────────────────────────────────────────────────────────────


@pytest.mark.asyncio
async def test_listing_nested_and_special_names(azurite: Azurite, container: str) -> None:
    for name in ("a/b/c.txt", "sp ace/ü & 'q'.txt", "top.txt"):
        _put(azurite, container, name, PAYLOAD)
    fs = _fs(azurite, container)
    assert await _names(fs, "/") == {"/a", "/sp ace", "/top.txt"}
    assert await _names(fs, "/a/b") == {"/a/b/c.txt"}
    assert await _names(fs, "/sp ace") == {"/sp ace/ü & 'q'.txt"}


@pytest.mark.asyncio
async def test_listing_errors_are_raised(azurite: Azurite) -> None:
    fs = _fs(azurite, f"nova-missing-{uuid.uuid4().hex[:12]}")
    with pytest.raises(FileNotFoundError):
        await _names(fs, "/")


# ── directories ───────────────────────────────────────────────────────────────


@pytest.mark.asyncio
async def test_mkdir(azurite: Azurite, container: str) -> None:
    fs = _fs(azurite, container)
    fs.mkdir(fs.path("/made"))
    assert _exists(azurite, container, "made/.keep")
    assert "/made" in await _names(fs, "/")
    assert fs.stat(fs.path("/made")).is_directory
    assert await _names(fs, "/made") <= {"/made/.keep"}
    with pytest.raises(FileExistsError):
        fs.mkdir(fs.path("/made"))
    fs.rmdir(fs.path("/made"))
    assert not _exists(azurite, container, "made/.keep")
    assert "/made" not in await _names(fs, "/")
    with pytest.raises(FileNotFoundError):
        fs.rmdir(fs.path("/made"))


# ── copy ──────────────────────────────────────────────────────────────────────


@pytest.mark.asyncio
async def test_copy_azure_to_local(azurite: Azurite, container: str, tmp_path: Path) -> None:
    _put(azurite, container, "src/deep/a.txt", b"alpha\n")
    fs = _fs(azurite, container)
    dst = tmp_path / "dst"
    dst.mkdir()
    await run_task(lambda ctx: copy_files(ctx, [fs.path("/src")], _local(dst)))
    assert (dst / "src" / "deep" / "a.txt").read_bytes() == b"alpha\n"


@pytest.mark.asyncio
async def test_copy_local_to_azure(azurite: Azurite, container: str, tmp_path: Path) -> None:
    (tmp_path / "src" / "deep").mkdir(parents=True)
    (tmp_path / "src" / "deep" / "a.txt").write_bytes(b"alpha\n")
    fs = _fs(azurite, container)
    fs.mkdir(fs.path("/dest"))
    await run_task(lambda ctx: copy_files(ctx, [_local(tmp_path / "src")], fs.path("/dest")))
    assert _get(azurite, container, "dest/src/deep/a.txt") == b"alpha\n"


@pytest.mark.asyncio
async def test_copy_empty_directory(azurite: Azurite, container: str, tmp_path: Path) -> None:
    (tmp_path / "empty").mkdir()
    fs = _fs(azurite, container)
    fs.mkdir(fs.path("/dest"))
    await run_task(lambda ctx: copy_files(ctx, [_local(tmp_path / "empty")], fs.path("/dest")))
    assert fs.stat(fs.path("/dest/empty")).is_directory


# ── edit through a local copy ─────────────────────────────────────────────────


@pytest.mark.asyncio
async def test_edit_and_save_back(azurite: Azurite, container: str, tmp_path_factory: pytest.TempPathFactory) -> None:
    _put(azurite, container, "doc.txt", b"old")
    fs = _fs(azurite, container)
    async with _manager(tmp_path_factory) as (manager, _runner):
        entry = await manager.open(fs.path("/doc.txt"))
        assert entry is not None
        entry.copy.path.write_bytes(b"new content\n")
        await manager.sync_now(entry)
        assert entry.status is CopyStatus.SYNCED, entry.error
    assert _get(azurite, container, "doc.txt") == b"new content\n"


@pytest.mark.asyncio
async def test_edit_conflict_is_detected(azurite: Azurite, container: str, tmp_path_factory: pytest.TempPathFactory) -> None:
    _put(azurite, container, "doc.txt", b"old")
    fs = _fs(azurite, container)
    async with _manager(tmp_path_factory, [Response.SKIP]) as (manager, _runner):
        entry = await manager.open(fs.path("/doc.txt"))
        assert entry is not None
        _put(azurite, container, "doc.txt", b"changed elsewhere")
        entry.copy.path.write_bytes(b"my edit")
        await manager.sync_now(entry)
        assert entry.status is CopyStatus.CONFLICT
    assert _get(azurite, container, "doc.txt") == b"changed elsewhere"


# ── move ──────────────────────────────────────────────────────────────────────


@pytest.mark.asyncio
async def test_move_file(azurite: Azurite, container: str) -> None:
    _put(azurite, container, "a.txt", PAYLOAD)
    fs = _fs(azurite, container)
    fs.mkdir(fs.path("/moved"))
    await run_task(lambda ctx: move_files(ctx, [fs.path("/a.txt")], fs.path("/moved")))
    assert not _exists(azurite, container, "a.txt")
    assert _get(azurite, container, "moved/a.txt") == PAYLOAD


@pytest.mark.asyncio
async def test_move_directory(azurite: Azurite, container: str) -> None:
    _put(azurite, container, "src/deep/a.txt", PAYLOAD)
    _put(azurite, container, "src/.keep", b"")
    fs = _fs(azurite, container)
    fs.mkdir(fs.path("/dest"))
    await run_task(lambda ctx: move_files(ctx, [fs.path("/src")], fs.path("/dest")))
    assert not _exists(azurite, container, "src/deep/a.txt")
    assert _get(azurite, container, "dest/src/deep/a.txt") == PAYLOAD


@pytest.mark.asyncio
async def test_move_between_containers(azurite: Azurite, new_container: Callable[[], str]) -> None:
    source, target = new_container(), new_container()
    _put(azurite, source, "a.txt", PAYLOAD)
    src_fs = _fs(azurite, source)
    dst_fs = _fs(azurite, target)
    await run_task(lambda ctx: move_files(ctx, [src_fs.path("/a.txt")], dst_fs.path("/")))
    assert not _exists(azurite, source, "a.txt")
    assert _get(azurite, target, "a.txt") == PAYLOAD


@pytest.mark.asyncio
async def test_move_remote_file_to_local_directory(azurite: Azurite, container: str, tmp_path: Path) -> None:
    _put(azurite, container, "a.txt", PAYLOAD)
    remote = RemoteFilesystem("box", _fs(azurite, container))
    dst = tmp_path / "dst"
    dst.mkdir()
    await run_task(lambda ctx: move_files(ctx, [remote.path("/a.txt")], _local(dst)))
    assert (dst / "a.txt").read_bytes() == PAYLOAD
    assert not _exists(azurite, container, "a.txt")
    assert not _exists(azurite, container, str(dst).lstrip("/") + "/a.txt")


@pytest.mark.asyncio
async def test_move_remote_file_to_other_remote_container(azurite: Azurite, new_container: Callable[[], str]) -> None:
    source, target = new_container(), new_container()
    _put(azurite, source, "a.txt", PAYLOAD)
    src = RemoteFilesystem("one", _fs(azurite, source))
    dst = RemoteFilesystem("two", _fs(azurite, target))
    await run_task(lambda ctx: move_files(ctx, [src.path("/a.txt")], dst.root()))
    assert not _exists(azurite, source, "a.txt")
    assert _get(azurite, target, "a.txt") == PAYLOAD


# ── erase ─────────────────────────────────────────────────────────────────────


@pytest.mark.asyncio
async def test_erase_directory_tree(azurite: Azurite, container: str) -> None:
    _put(azurite, container, "tree/deep/a.txt", PAYLOAD)
    _put(azurite, container, "tree/b.txt", PAYLOAD)
    fs = _fs(azurite, container)
    await run_task(lambda ctx: erase_files(ctx, [fs.path("/tree")], EraseFilesOptions(ask_before_erase=False)))
    assert await _names(fs, "/") == set()


@pytest.mark.asyncio
async def test_erase_directory_with_marker(azurite: Azurite, container: str) -> None:
    _put(azurite, container, "marked/.keep", b"")
    _put(azurite, container, "marked/a.txt", PAYLOAD)
    fs = _fs(azurite, container)
    await run_task(lambda ctx: erase_files(ctx, [fs.path("/marked")], EraseFilesOptions(ask_before_erase=False)))
    assert await _names(fs, "/") == set()
