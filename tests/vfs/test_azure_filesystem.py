"""Tests for AzureFilesystem using an injected mock ContainerClient."""

from __future__ import annotations

import errno
from datetime import UTC, datetime
from unittest.mock import MagicMock

import pytest
from azure.core.exceptions import ResourceExistsError, ResourceNotFoundError
from azure.storage.blob import BlobPrefix, BlobProperties, ContainerClient

from nova_navigator.vfs.filesystems.azure import AzureFilesystem


def _make_fs() -> tuple[AzureFilesystem, MagicMock]:
    """Return (fs, mock_client) with no real network activity."""
    mock_client = MagicMock(spec=ContainerClient)
    fs = AzureFilesystem("https://test.blob.core.windows.net", "testcontainer", client=mock_client)
    return fs, mock_client


def test_cwd_returns_root() -> None:
    fs, _ = _make_fs()
    assert str(fs.cwd().path) == "/"


def test_root_returns_slash() -> None:
    fs, _ = _make_fs()
    assert str(fs.root().path) == "/"


def test_home_returns_slash() -> None:
    fs, _ = _make_fs()
    assert str(fs.home().path) == "/"


def test_parent_returns_parent_directory() -> None:
    fs, _ = _make_fs()
    p = fs.path("/foo/bar/baz.txt")
    assert str(fs.parent(p).path) == "/foo/bar"


def test_parent_of_root_is_root() -> None:
    fs, _ = _make_fs()
    p = fs.path("/")
    assert str(fs.parent(p).path) == "/"


def test_is_same_device_always_true() -> None:
    fs, _ = _make_fs()
    p1 = fs.path("/a")
    p2 = fs.path("/b")
    assert fs.is_same_device(p1, p2) is True


def _make_blob_properties(name: str, size: int = 100, mtime: float = 1000.0) -> MagicMock:
    """Build a minimal BlobProperties-like mock."""
    props = MagicMock(spec=BlobProperties)
    props.name = name
    props.size = size
    props.last_modified = datetime.fromtimestamp(mtime, tz=UTC)
    return props


def _make_blob_prefix(name: str) -> MagicMock:
    """Build a BlobPrefix-like mock with a .name attribute."""
    prefix = MagicMock(spec=BlobPrefix)
    prefix.name = name
    return prefix


# ── iterdir ───────────────────────────────────────────────────────────────────


@pytest.mark.asyncio
async def test_iterdir_root_passes_none_prefix() -> None:
    fs, mock_client = _make_fs()
    mock_client.walk_blobs.return_value = []
    async for _ in fs.iterdir(fs.path("/")):
        pass
    mock_client.walk_blobs.assert_called_once_with(name_starts_with=None, delimiter="/")


@pytest.mark.asyncio
async def test_iterdir_subdir_passes_prefix_with_slash() -> None:
    fs, mock_client = _make_fs()
    mock_client.walk_blobs.return_value = []
    async for _ in fs.iterdir(fs.path("/foo")):
        pass
    mock_client.walk_blobs.assert_called_once_with(name_starts_with="foo/", delimiter="/")


@pytest.mark.asyncio
async def test_iterdir_returns_blobs_and_prefixes() -> None:
    fs, mock_client = _make_fs()
    blob = _make_blob_properties("docs/readme.txt")
    prefix = _make_blob_prefix("docs/images/")
    mock_client.walk_blobs.return_value = [blob, prefix]
    paths = [str(p.path) async for p in fs.iterdir(fs.path("/docs"))]
    assert "/docs/readme.txt" in paths
    assert "/docs/images" in paths


@pytest.mark.asyncio
async def test_iterdir_skips_blob_whose_name_equals_prefix() -> None:
    """A blob whose name is exactly the directory prefix (e.g. 'foo/') is skipped."""
    fs, mock_client = _make_fs()
    # Blob named exactly "foo/" (the prefix itself) should be skipped
    dir_marker = _make_blob_properties("foo/")
    blob = _make_blob_properties("foo/bar.txt")
    mock_client.walk_blobs.return_value = [dir_marker, blob]
    paths = [str(p.path) async for p in fs.iterdir(fs.path("/foo"))]
    assert "/foo/bar.txt" in paths
    assert "/foo/" not in paths  # the prefix-named blob is skipped


@pytest.mark.asyncio
async def test_iterdir_keep_marker_blob_appears_in_listing() -> None:
    """The .keep marker blob is a regular blob and appears in iterdir results."""
    fs, mock_client = _make_fs()
    keep = _make_blob_properties("foo/.keep")
    blob = _make_blob_properties("foo/bar.txt")
    mock_client.walk_blobs.return_value = [keep, blob]
    paths = [str(p.path) async for p in fs.iterdir(fs.path("/foo"))]
    assert "/foo/.keep" in paths
    assert "/foo/bar.txt" in paths


# ── stat ──────────────────────────────────────────────────────────────────────


def test_stat_root_is_directory() -> None:
    fs, _ = _make_fs()
    s = fs.stat(fs.path("/"))
    assert s.is_directory is True
    assert s.size == 0


def test_stat_blob_returns_size_and_mtime() -> None:
    fs, mock_client = _make_fs()
    props = _make_blob_properties("data/file.txt", size=42, mtime=1234.0)
    mock_client.get_blob_client.return_value.get_blob_properties.return_value = props
    s = fs.stat(fs.path("/data/file.txt"))
    assert s.size == 42
    assert s.modified == pytest.approx(1234.0)
    assert s.is_directory is False


def test_stat_virtual_dir_synthesised_when_blobs_exist() -> None:
    fs, mock_client = _make_fs()
    mock_client.get_blob_client.return_value.get_blob_properties.side_effect = ResourceNotFoundError
    mock_client.list_blobs.return_value = [_make_blob_properties("folder/file.txt")]
    s = fs.stat(fs.path("/folder"))
    assert s.is_directory is True


def test_stat_missing_blob_raises_file_not_found() -> None:
    fs, mock_client = _make_fs()
    mock_client.get_blob_client.return_value.get_blob_properties.side_effect = ResourceNotFoundError
    mock_client.list_blobs.return_value = []
    with pytest.raises(FileNotFoundError):
        fs.stat(fs.path("/missing.txt"))


# ── read ──────────────────────────────────────────────────────────────────────


def test_read_returns_stream_reader() -> None:
    fs, mock_client = _make_fs()
    downloader = MagicMock()
    downloader.read.return_value = b"data"
    mock_client.get_blob_client.return_value.download_blob.return_value = downloader
    result = fs.read(fs.path("/file.txt"))
    assert result.read(4) == b"data"
    result.close()  # must not raise


def test_read_missing_raises_file_not_found() -> None:
    fs, mock_client = _make_fs()
    mock_client.get_blob_client.return_value.download_blob.side_effect = ResourceNotFoundError
    with pytest.raises(FileNotFoundError):
        fs.read(fs.path("/missing.txt"))


# ── write ─────────────────────────────────────────────────────────────────────


def test_write_uploads_on_close() -> None:
    fs, mock_client = _make_fs()
    writer = fs.write(fs.path("/out/file.txt"))
    writer.write(b"hello")
    writer.close()
    mock_client.get_blob_client.assert_called_with("out/file.txt")
    upload_call = mock_client.get_blob_client.return_value.upload_blob
    upload_call.assert_called_once()
    uploaded_buf = upload_call.call_args.args[0]
    assert uploaded_buf.read() == b"hello"
    _, kwargs = upload_call.call_args
    assert kwargs.get("overwrite") is True


# ── remove ────────────────────────────────────────────────────────────────────


def test_remove_calls_delete_blob() -> None:
    fs, mock_client = _make_fs()
    fs.remove(fs.path("/data/file.txt"))
    mock_client.get_blob_client.assert_called_with("data/file.txt")
    mock_client.get_blob_client.return_value.delete_blob.assert_called_once()


def test_remove_missing_raises_file_not_found() -> None:
    fs, mock_client = _make_fs()
    mock_client.get_blob_client.return_value.delete_blob.side_effect = ResourceNotFoundError
    with pytest.raises(FileNotFoundError):
        fs.remove(fs.path("/missing.txt"))


# ── rename ────────────────────────────────────────────────────────────────────


def test_rename_copies_then_deletes() -> None:
    fs, mock_client = _make_fs()
    src_blob_client = MagicMock()
    dst_blob_client = MagicMock()
    src_blob_client.url = "https://test.blob.core.windows.net/testcontainer/old.txt"

    def _get_client(name: str) -> MagicMock:
        return src_blob_client if name == "old.txt" else dst_blob_client

    mock_client.get_blob_client.side_effect = _get_client
    fs.rename(fs.path("/old.txt"), fs.path("/new.txt"))
    dst_blob_client.start_copy_from_url.assert_called_once_with(src_blob_client.url)
    src_blob_client.delete_blob.assert_called_once()


def test_rename_missing_raises_file_not_found() -> None:
    fs, mock_client = _make_fs()
    mock_client.get_blob_client.return_value.start_copy_from_url.side_effect = ResourceNotFoundError
    with pytest.raises(FileNotFoundError):
        fs.rename(fs.path("/missing.txt"), fs.path("/other.txt"))


# ── mkdir ─────────────────────────────────────────────────────────────────────


def test_mkdir_uploads_keep_marker() -> None:
    fs, mock_client = _make_fs()
    fs.mkdir(fs.path("/newdir"))
    mock_client.get_blob_client.assert_called_with("newdir/.keep")
    mock_client.get_blob_client.return_value.upload_blob.assert_called_once_with(b"", overwrite=False)


def test_mkdir_raises_if_exists() -> None:
    fs, mock_client = _make_fs()
    mock_client.get_blob_client.return_value.upload_blob.side_effect = ResourceExistsError
    with pytest.raises(FileExistsError):
        fs.mkdir(fs.path("/existing"))


# ── rmdir ─────────────────────────────────────────────────────────────────────


def test_rmdir_deletes_keep_marker() -> None:
    fs, mock_client = _make_fs()
    mock_client.list_blobs.return_value = []
    fs.rmdir(fs.path("/emptydir"))
    mock_client.get_blob_client.assert_called_with("emptydir/.keep")
    mock_client.get_blob_client.return_value.delete_blob.assert_called_once()


def test_rmdir_raises_enotempty_when_blobs_exist() -> None:
    fs, mock_client = _make_fs()
    extra = _make_blob_properties("mydir/extra.txt")
    mock_client.list_blobs.return_value = [extra]
    with pytest.raises(OSError, match="Directory not empty") as exc_info:
        fs.rmdir(fs.path("/mydir"))
    assert exc_info.value.errno == errno.ENOTEMPTY


def test_rmdir_raises_file_not_found_when_marker_absent() -> None:
    fs, mock_client = _make_fs()
    mock_client.list_blobs.return_value = []
    mock_client.get_blob_client.return_value.delete_blob.side_effect = ResourceNotFoundError
    with pytest.raises(FileNotFoundError):
        fs.rmdir(fs.path("/ghostdir"))


# ── readlink ──────────────────────────────────────────────────────────────────


def test_readlink_raises_einval() -> None:
    fs, _ = _make_fs()
    with pytest.raises(OSError, match="Not a symbolic link") as exc_info:
        fs.readlink(fs.path("/file.txt"))
    assert exc_info.value.errno == errno.EINVAL
