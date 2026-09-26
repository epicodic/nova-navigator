"""Safe local replacement and conflict handling."""

import hashlib
import os
from pathlib import Path
from typing import BinaryIO, cast
from unittest.mock import MagicMock

import pytest
from azure.core import MatchConditions
from azure.core.exceptions import HttpResponseError
from azure.storage.blob import ContainerClient

from nova_navigator.editing import commit
from nova_navigator.editing.commit import commit_local
from nova_navigator.editing.model import SourceConflictError, SourceVersion
from nova_navigator.vfs.filesystems.azure import AzureFilesystem
from nova_navigator.vfs.filesystems.remote import RemoteFilesystem


def _version(data: bytes) -> SourceVersion:
    return SourceVersion("digest", hashlib.sha256(data).hexdigest(), len(data))


def _azure_source() -> tuple[AzureFilesystem, MagicMock]:
    client = MagicMock(spec=ContainerClient)
    filesystem = AzureFilesystem("https://test.blob.core.windows.net", "container", client=client)
    return filesystem, client.get_blob_client.return_value


def test_azure_upload_streams_mirror_with_captured_etag(tmp_path: Path) -> None:
    filesystem, blob = _azure_source()
    mirror = tmp_path / "mirror"
    mirror.write_bytes(b"edited contents")
    streams: list[BinaryIO] = []

    def upload(stream: BinaryIO, **kwargs: object) -> None:
        streams.append(stream)
        assert stream.read(6) == b"edited"
        assert stream.read() == b" contents"
        assert kwargs == {
            "overwrite": True,
            "etag": '"original"',
            "match_condition": MatchConditions.IfNotModified,
        }

    blob.upload_blob.side_effect = upload
    source = filesystem.path("/folder/file.txt")
    commit.commit_azure(source, mirror, SourceVersion("etag", '"original"', 3), False)
    cast("MagicMock", filesystem._client).get_blob_client.assert_called_once_with("folder/file.txt")
    assert streams[0].closed
    assert mirror.read_bytes() == b"edited contents"
    blob.get_blob_properties.assert_not_called()


def test_azure_412_preserves_mirror_and_reports_conflict(tmp_path: Path) -> None:
    filesystem, blob = _azure_source()
    mirror = tmp_path / "mirror"
    mirror.write_bytes(b"edited")
    streams: list[BinaryIO] = []

    def fail_upload(stream: BinaryIO, **_kwargs: object) -> None:
        streams.append(stream)
        raise HttpResponseError(message="changed", response=MagicMock(status_code=412))

    blob.upload_blob.side_effect = fail_upload
    with pytest.raises(SourceConflictError):
        commit.commit_azure(filesystem.path("/file.txt"), mirror, SourceVersion("etag", '"old"', 3), False)
    assert streams[0].closed
    assert mirror.read_bytes() == b"edited"


def test_azure_force_refreshes_etag_and_rejects_intervening_change(tmp_path: Path) -> None:
    filesystem, blob = _azure_source()
    mirror = tmp_path / "mirror"
    mirror.write_bytes(b"edited")
    blob.get_blob_properties.return_value.etag = '"current"'
    blob.upload_blob.side_effect = HttpResponseError(message="raced", response=MagicMock(status_code=412))
    wrapped = RemoteFilesystem("saved", filesystem)
    with pytest.raises(SourceConflictError):
        commit.commit_azure(wrapped.path("/file.txt"), mirror, SourceVersion("etag", '"old"', 3), True)
    assert blob.upload_blob.call_args.kwargs == {
        "overwrite": True,
        "etag": '"current"',
        "match_condition": MatchConditions.IfNotModified,
    }
    assert mirror.read_bytes() == b"edited"


def test_azure_upload_failure_closes_stream_and_preserves_mirror(tmp_path: Path) -> None:
    filesystem, blob = _azure_source()
    mirror = tmp_path / "mirror"
    mirror.write_bytes(b"edited")
    streams: list[BinaryIO] = []

    def fail_upload(stream: BinaryIO, **_kwargs: object) -> None:
        streams.append(stream)
        raise OSError("upload failed")

    blob.upload_blob.side_effect = fail_upload
    with pytest.raises(OSError, match="upload failed"):
        commit.commit_azure(filesystem.path("/file.txt"), mirror, SourceVersion("etag", '"old"', 3), False)
    assert streams[0].closed
    assert mirror.read_bytes() == b"edited"


@pytest.mark.parametrize("force", [False, True])
def test_local_replaces_source_and_preserves_mirror(tmp_path: Path, force: bool) -> None:
    source = tmp_path / "source"
    mirror = tmp_path / "mirror"
    source.write_bytes(b"old" if not force else b"changed")
    mirror.write_bytes(b"edited")
    source.chmod(0o640)
    with source.open("rb") as old_reader:
        commit_local(source, mirror, _version(b"old"), force)
        assert old_reader.read() == (b"old" if not force else b"changed")
    assert source.read_bytes() == mirror.read_bytes() == b"edited"
    assert source.stat().st_mode & 0o777 == 0o640
    assert sorted(p.name for p in tmp_path.iterdir()) == ["mirror", "source"]


def test_local_conflict_preserves_files(tmp_path: Path) -> None:
    source = tmp_path / "source"
    mirror = tmp_path / "mirror"
    source.write_bytes(b"new")
    mirror.write_bytes(b"edited")
    with pytest.raises(SourceConflictError):
        commit_local(source, mirror, _version(b"old"), False)
    assert source.read_bytes() == b"new"
    assert mirror.read_bytes() == b"edited"
    assert len(list(tmp_path.iterdir())) == 2


def test_local_failed_replace_cleans_stage(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    source = tmp_path / "source"
    mirror = tmp_path / "mirror"
    source.write_bytes(b"old")
    mirror.write_bytes(b"edited")

    def fail_replace(src: str | Path, dst: str | Path) -> None:
        assert Path(src).parent == source.parent
        assert Path(dst) == source
        raise OSError("replacement failed")

    monkeypatch.setattr(os, "replace", fail_replace)
    with pytest.raises(OSError, match="replacement failed"):
        commit_local(source, mirror, _version(b"old"), False)
    assert source.read_bytes() == b"old"
    assert mirror.read_bytes() == b"edited"
    assert len(list(tmp_path.iterdir())) == 2


def test_local_rejects_symlink_source(tmp_path: Path) -> None:
    target = tmp_path / "target"
    source = tmp_path / "source"
    mirror = tmp_path / "mirror"
    target.write_bytes(b"old")
    source.symlink_to(target)
    mirror.write_bytes(b"edited")
    with pytest.raises(ValueError, match="symlink"):
        commit_local(source, mirror, _version(b"old"), False)
    assert source.is_symlink()
    assert target.read_bytes() == b"old"
    assert mirror.read_bytes() == b"edited"
    assert len(list(tmp_path.iterdir())) == 3


def test_local_staging_failure_preserves_source(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    import shutil
    from typing import BinaryIO

    source = tmp_path / "source"
    mirror = tmp_path / "mirror"
    source.write_bytes(b"old")
    mirror.write_bytes(b"edited")

    def fail_copy(_reader: BinaryIO, output: BinaryIO, _length: int) -> None:
        output.write(b"partial")
        raise OSError("staging failed")

    monkeypatch.setattr(shutil, "copyfileobj", fail_copy)
    with pytest.raises(OSError, match="staging failed"):
        commit_local(source, mirror, _version(b"old"), False)
    assert source.read_bytes() == b"old"
    assert mirror.read_bytes() == b"edited"
    assert len(list(tmp_path.iterdir())) == 2


def test_local_rejects_symlink_created_during_staging(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    import shutil
    from typing import BinaryIO

    target = tmp_path / "target"
    source = tmp_path / "source"
    mirror = tmp_path / "mirror"
    target.write_bytes(b"old")
    source.write_bytes(b"old")
    mirror.write_bytes(b"edited")
    original_copy = shutil.copyfileobj

    def replace_with_link(reader: BinaryIO, output: BinaryIO, length: int) -> None:
        original_copy(reader, output, length)
        source.unlink()
        source.symlink_to(target)

    monkeypatch.setattr(shutil, "copyfileobj", replace_with_link)
    with pytest.raises(ValueError, match="symlink"):
        commit_local(source, mirror, _version(b"old"), False)
    assert source.is_symlink()
    assert target.read_bytes() == b"old"
    assert mirror.read_bytes() == b"edited"
    assert len(list(tmp_path.iterdir())) == 3
