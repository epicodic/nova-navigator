"""Streaming mirror and digest tests."""

from __future__ import annotations

import hashlib
from pathlib import Path
from unittest.mock import MagicMock

import pytest
from azure.storage.blob import ContainerClient

from nova_navigator.editing.transfer import CHUNK_SIZE, digest_file, digest_vpath, mirror_file, mirror_file_with_version
from nova_navigator.vfs.filesystem import StreamReaderLike
from nova_navigator.vfs.filesystems.azure import AzureFilesystem
from nova_navigator.vfs.vpath import VPath
from tests._utils.mock_filesystem import MockFilesystem


def test_mirror_file_streams_multiple_chunks_and_returns_digest(tmp_path: Path) -> None:
    data = b"a" * CHUNK_SIZE + b"b" * 17
    fs = MockFilesystem({"/source.bin": data})
    destination = tmp_path / "mirror.bin"

    digest, size = mirror_file(fs.path("/source.bin"), destination, lambda: False)

    assert destination.read_bytes() == data
    assert (digest, size) == (hashlib.sha256(data).hexdigest(), len(data))
    assert fs.readers[0].close_count == 1


def test_digest_file_and_vpath_match_sha256(tmp_path: Path) -> None:
    data = b"a" * CHUNK_SIZE + b"c" * 9
    local = tmp_path / "data.bin"
    local.write_bytes(data)
    fs = MockFilesystem({"/data.bin": data})

    assert digest_file(local, lambda: False) == hashlib.sha256(data).hexdigest()
    assert digest_vpath(fs.path("/data.bin"), lambda: False) == hashlib.sha256(data).hexdigest()
    assert fs.readers[0].close_count == 1


def test_mirror_file_cancel_removes_partial_destination(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    fs = MockFilesystem({"/source.bin": b"a" * (CHUNK_SIZE * 2)})
    destination = tmp_path / "mirror.bin"
    calls = 0
    read_calls = 0
    original_read = fs.read

    def recording_read(path: VPath) -> StreamReaderLike:
        reader = original_read(path)
        original_chunk_read = reader.read

        def recorded_chunk_read(size: int) -> bytes:
            nonlocal read_calls
            read_calls += 1
            return original_chunk_read(size)

        monkeypatch.setattr(reader, "read", recorded_chunk_read)
        return reader

    monkeypatch.setattr(fs, "read", recording_read)

    def should_cancel() -> bool:
        nonlocal calls
        calls += 1
        return calls >= 3

    with pytest.raises(InterruptedError):
        mirror_file(fs.path("/source.bin"), destination, should_cancel)

    assert read_calls == 1
    assert not destination.exists()
    assert fs.readers[0].close_count == 1


def test_mirror_file_read_failure_closes_stream_and_removes_partial_destination(tmp_path: Path) -> None:
    fs = MockFilesystem({"/source.bin": b"data"}, read_errors={"/source.bin": OSError("read failed")})
    destination = tmp_path / "mirror.bin"

    with pytest.raises(OSError, match="read failed"):
        mirror_file(fs.path("/source.bin"), destination, lambda: False)

    assert not destination.exists()
    assert fs.readers[0].close_count == 1


def test_version_uses_downloaded_bytes_and_source_stat() -> None:
    fs = MockFilesystem({"/source.bin": b"old"})
    source = fs.path("/source.bin")
    source_stat = source.stat

    from tempfile import TemporaryDirectory

    with TemporaryDirectory() as directory:
        digest, size, version = mirror_file_with_version(source, Path(directory) / "mirror.bin", lambda: False)

    assert version.kind == "digest"
    assert version.value == digest == hashlib.sha256(b"old").hexdigest()
    assert version.size == size == 3
    assert version.modified == source_stat.modified


def test_azure_version_uses_etag_from_download_response(tmp_path: Path) -> None:
    client = MagicMock(spec=ContainerClient)
    downloader = client.get_blob_client.return_value.download_blob.return_value
    downloader.properties.etag = '"download-version"'
    downloader.read.side_effect = [b"data", b""]
    fs = AzureFilesystem("https://example.blob.core.windows.net", "files", client=client)

    digest, size, version = mirror_file_with_version(fs.path("/source.bin"), tmp_path / "mirror.bin", lambda: False)

    assert digest == hashlib.sha256(b"data").hexdigest()
    assert size == 4
    assert version.kind == "etag"
    assert version.value == '"download-version"'
    client.get_blob_client.return_value.get_blob_properties.assert_not_called()


def test_mirror_file_close_failure_removes_destination(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    fs = MockFilesystem({"/source.bin": b"data"})
    original_read = fs.read

    def failing_read(path: VPath) -> StreamReaderLike:
        reader = original_read(path)

        def failing_close() -> None:
            reader.close_count += 1
            raise OSError("close failed")

        monkeypatch.setattr(reader, "close", failing_close)
        return reader

    monkeypatch.setattr(fs, "read", failing_read)
    destination = tmp_path / "mirror.bin"

    with pytest.raises(OSError, match="close failed"):
        mirror_file(fs.path("/source.bin"), destination, lambda: False)

    assert not destination.exists()
    assert fs.readers[0].close_count == 1
