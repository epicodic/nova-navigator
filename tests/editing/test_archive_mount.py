"""Archive mount ownership and refresh."""

import io
import zipfile
from contextlib import closing
from pathlib import Path

import pytest

from nova_navigator.editing.archive_mount import open_archive_mount
from nova_navigator.vfs.filesystems.local import LocalFilesystem
from tests._utils.mock_filesystem import MockFilesystem


def _zip(content: bytes) -> bytes:
    buffer = io.BytesIO()
    with zipfile.ZipFile(buffer, "w") as archive:
        archive.writestr("member.txt", content)
    return buffer.getvalue()


def test_remote_mount_owns_private_unique_stage(tmp_path: Path) -> None:
    fs = MockFilesystem({"/source.zip": _zip(b"original")})
    source = fs.path("/source.zip")
    first = open_archive_mount(source, tmp_path, lambda: False)
    second = open_archive_mount(source, tmp_path, lambda: False)
    assert first.source == source
    assert first.source.uri == source.uri
    assert first.local_path != second.local_path
    assert first.local_path.parent.stat().st_mode & 0o777 == 0o700
    assert first.local_path.stat().st_mode & 0o777 == 0o600
    with closing(first.read(first.path("/member.txt"))) as reader:
        assert reader.read(100) == b"original"
    first.close()
    first.close()
    assert not first.local_path.parent.exists()
    assert second.local_path.exists()
    second.close()


@pytest.mark.asyncio
async def test_reload_invalidates_listed_and_direct_member_stats(tmp_path: Path) -> None:
    fs = MockFilesystem({"/source.zip": _zip(b"old")})
    mount = open_archive_mount(fs.path("/source.zip"), tmp_path, lambda: False)
    direct = mount.path("/member.txt")
    assert direct.stat.size == 3
    listed = [entry async for entry in mount.iterdir(mount.root())]
    previous_hash = hash(direct)
    replacement = tmp_path / "replacement.zip"
    replacement.write_bytes(_zip(b"new contents"))
    replacement.replace(mount.local_path)
    mount.reload()
    assert hash(direct) == previous_hash
    assert direct.stat.size == listed[0].stat.size == 12
    with closing(mount.read(direct)) as reader:
        assert reader.read(100) == b"new contents"
    mount.close()


def test_local_mount_keeps_original_file(tmp_path: Path) -> None:
    source = tmp_path / "source.zip"
    source.write_bytes(_zip(b"local"))
    stage = tmp_path / "unused"
    mount = open_archive_mount(LocalFilesystem.singleton().path(source), stage, lambda: False)
    assert mount.local_path == source
    mount.close()
    assert source.exists()
    assert not stage.exists()


@pytest.mark.parametrize("cancel_after", [1, 3])
def test_cancel_cleans_only_owned_stage(tmp_path: Path, cancel_after: int) -> None:
    fs = MockFilesystem({"/source.zip": _zip(b"a" * 200000)})
    sentinel = tmp_path / "keep"
    sentinel.touch()
    calls = 0

    def cancelled() -> bool:
        nonlocal calls
        calls += 1
        return calls >= cancel_after

    with pytest.raises(InterruptedError):
        open_archive_mount(fs.path("/source.zip"), tmp_path, cancelled)
    assert list(tmp_path.iterdir()) == [sentinel]
    assert all(reader.close_count == 1 for reader in fs.readers)


def test_invalid_remote_archive_cleans_stage(tmp_path: Path) -> None:
    fs = MockFilesystem({"/source.zip": b"invalid"})
    with pytest.raises(zipfile.BadZipFile):
        open_archive_mount(fs.path("/source.zip"), tmp_path, lambda: False)
    assert not list(tmp_path.iterdir())


def test_mount_retains_azure_download_etag(tmp_path: Path) -> None:
    from unittest.mock import MagicMock

    from azure.storage.blob import ContainerClient

    from nova_navigator.vfs.filesystems.azure import AzureFilesystem

    client = MagicMock(spec=ContainerClient)
    downloader = client.get_blob_client.return_value.download_blob.return_value
    downloader.properties.etag = '"download-version"'
    downloader.read.side_effect = [_zip(b"azure"), b""]
    fs = AzureFilesystem("https://example.blob.core.windows.net", "files", client=client)
    mount = open_archive_mount(fs.path("/source.zip"), tmp_path, lambda: False)
    try:
        assert mount.source_version is not None
        assert mount.source_version.kind == "etag"
        assert mount.source_version.value == '"download-version"'
        client.get_blob_client.return_value.get_blob_properties.assert_not_called()
    finally:
        mount.close()
