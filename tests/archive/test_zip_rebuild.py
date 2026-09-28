"""Tests for raw-copy ZIP member replacement."""

from __future__ import annotations

import io
import shutil
import struct
import subprocess
import zipfile
from pathlib import Path
from typing import IO

import pytest

from nova_navigator.archive.zip_rebuild import (
    _build_appended_central_record,
    _build_replaced_central_record,
    _Entry,
    rebuild_zip,
)


def _make_zip(path: Path, compression: int = zipfile.ZIP_DEFLATED) -> None:
    with zipfile.ZipFile(path, "w", compression) as archive:
        archive.comment = b"archive comment"
        archive.writestr("keep.txt", b"keep me" * 100)
        archive.writestr("dir/edit.txt", b"old content")
        archive.writestr("tail.bin", bytes(range(256)))


def _raw_entry(path: Path, name: str) -> bytes:
    with zipfile.ZipFile(path) as archive:
        info = archive.getinfo(name)
    data = path.read_bytes()
    header = data[info.header_offset : info.header_offset + 30]
    name_len, extra_len = struct.unpack_from("<HH", header, 26)
    start = info.header_offset + 30 + name_len + extra_len
    return data[start : start + info.compress_size]


class _Unseekable:
    """Binary writer that hides `tell`/`seek` so `zipfile` falls back to data descriptors."""

    def __init__(self, raw: IO[bytes]) -> None:
        self._raw = raw

    def write(self, data: bytes) -> int:
        return self._raw.write(data)

    def flush(self) -> None:
        self._raw.flush()

    def close(self) -> None:
        self._raw.close()

    def tell(self) -> int:
        raise io.UnsupportedOperation("tell")

    def seek(self, offset: int, whence: int = 0) -> int:
        raise io.UnsupportedOperation("seek")


def _central_record_offsets(data: bytes) -> dict[bytes, int]:
    """Map each entry name to the absolute byte offset of its central directory record."""
    eocd = data.rfind(b"PK\x05\x06")
    cd_size, cd_offset = struct.unpack_from("<II", data, eocd + 12)
    offsets: dict[bytes, int] = {}
    pos = cd_offset
    end = cd_offset + cd_size
    while pos < end:
        name_len, extra_len, comment_len = struct.unpack_from("<HHH", data, pos + 28)
        name = data[pos + 46 : pos + 46 + name_len]
        offsets[name] = pos
        pos += 46 + name_len + extra_len + comment_len
    return offsets


def _build_rejected(source: Path, setup: str) -> None:
    if setup == "duplicate":
        with zipfile.ZipFile(source, "w") as archive:
            archive.writestr("edit.txt", b"one")
            with pytest.warns(UserWarning, match="Duplicate name"):
                archive.writestr("edit.txt", b"two")
    elif setup == "encrypted":
        with zipfile.ZipFile(source, "w") as archive:
            archive.writestr("edit.txt", b"secret")
        with zipfile.ZipFile(source) as archive:
            info = archive.getinfo("edit.txt")
        data = bytearray(source.read_bytes())
        data[info.header_offset + 6] |= 1
        central_offset = _central_record_offsets(bytes(data))[b"edit.txt"]
        data[central_offset + 8] |= 1
        source.write_bytes(bytes(data))
    elif setup == "prefix":
        with zipfile.ZipFile(source, "w") as archive:
            archive.writestr("edit.txt", b"content")
        source.write_bytes(b"#!/bin/sh\n" + source.read_bytes())
    elif setup == "lzma":
        with zipfile.ZipFile(source, "w") as archive:
            archive.writestr("edit.txt", b"data", zipfile.ZIP_LZMA)
    elif setup == "multi-volume":
        with zipfile.ZipFile(source, "w") as archive:
            archive.writestr("edit.txt", b"content")
        data = bytearray(source.read_bytes())
        eocd = data.rindex(b"PK\x05\x06")
        data[eocd + 4] = 1  # number of this disk
        source.write_bytes(bytes(data))
    else:
        raise ValueError(setup)


@pytest.mark.parametrize("compression", [zipfile.ZIP_STORED, zipfile.ZIP_DEFLATED, zipfile.ZIP_BZIP2])
def test_replaces_member_and_keeps_others_byte_identical(tmp_path: Path, compression: int) -> None:
    source, output, replacement = tmp_path / "a.zip", tmp_path / "out.zip", tmp_path / "new.txt"
    _make_zip(source, compression)
    replacement.write_bytes(b"new content, longer than before")
    rebuild_zip(source, "dir/edit.txt", replacement, output)
    with zipfile.ZipFile(output) as archive:
        assert archive.read("dir/edit.txt") == replacement.read_bytes()
        assert archive.read("keep.txt") == b"keep me" * 100
        assert archive.getinfo("dir/edit.txt").compress_type == compression
        assert archive.comment == b"archive comment"
        assert archive.testzip() is None
    for name in ("keep.txt", "tail.bin"):
        assert _raw_entry(output, name) == _raw_entry(source, name)


def test_differing_local_and_central_extras_survive(tmp_path: Path) -> None:
    source, output, replacement = tmp_path / "a.zip", tmp_path / "out.zip", tmp_path / "new.txt"
    with zipfile.ZipFile(source, "w") as archive:
        info = zipfile.ZipInfo("keep.txt")
        info.extra = struct.pack("<HHB", 0x5455, 1, 3)  # central extra
        archive.writestr(info, b"keep")
        archive.writestr("edit.txt", b"old")
    data = bytearray(source.read_bytes())
    # Local header of keep.txt: give its extra a different payload (timestamp flags 7 instead of 3).
    local_extra = data.index(struct.pack("<HHB", 0x5455, 1, 3))
    data[local_extra + 4] = 7
    source.write_bytes(bytes(data))
    replacement.write_bytes(b"new")
    rebuild_zip(source, "edit.txt", replacement, output)
    out = output.read_bytes()
    assert struct.pack("<HHB", 0x5455, 1, 7) in out
    assert struct.pack("<HHB", 0x5455, 1, 3) in out
    with zipfile.ZipFile(output) as archive:
        assert archive.read("edit.txt") == b"new"


def test_appends_new_member(tmp_path: Path) -> None:
    source, output, replacement = tmp_path / "a.zip", tmp_path / "out.zip", tmp_path / "new.txt"
    _make_zip(source)
    replacement.write_bytes(b"brand new")
    rebuild_zip(source, "dir/new.txt", replacement, output)
    with zipfile.ZipFile(output) as archive:
        assert archive.read("dir/new.txt") == b"brand new"
        assert len(archive.infolist()) == 4


def test_data_descriptor_entries_are_copied(tmp_path: Path) -> None:
    source, output, replacement = tmp_path / "a.zip", tmp_path / "out.zip", tmp_path / "new.txt"
    # Non-seekable writer forces data descriptors.
    with source.open("wb") as raw, zipfile.ZipFile(_Unseekable(raw), "w", zipfile.ZIP_DEFLATED) as archive:
        archive.writestr("keep.txt", b"streamed")
        archive.writestr("edit.txt", b"old")
    with zipfile.ZipFile(source) as archive:
        keep_flags = archive.getinfo("keep.txt").flag_bits
    assert keep_flags & 0x8, "test setup must produce data-descriptor entries"
    replacement.write_bytes(b"new")
    rebuild_zip(source, "edit.txt", replacement, output)
    with zipfile.ZipFile(output) as archive:
        assert archive.read("keep.txt") == b"streamed"
        assert archive.read("edit.txt") == b"new"
        assert archive.testzip() is None


@pytest.mark.parametrize(
    ("setup", "message"),
    [
        ("duplicate", "Ambiguous"),
        ("encrypted", "encrypted"),
        ("prefix", "prefix"),
        ("lzma", "LZMA"),
        ("multi-volume", "multi-volume"),
    ],
)
def test_rejections(tmp_path: Path, setup: str, message: str) -> None:
    source, output, replacement = tmp_path / "a.zip", tmp_path / "out.zip", tmp_path / "new.txt"
    replacement.write_bytes(b"x")
    _build_rejected(source, setup)
    with pytest.raises(ValueError, match=message):
        rebuild_zip(source, "edit.txt", replacement, output)
    assert not output.exists()


def test_unzip_validates_rebuilt_archive(tmp_path: Path) -> None:
    if shutil.which("unzip") is None:
        pytest.skip("unzip is not installed")
    source, output, replacement = tmp_path / "a.zip", tmp_path / "out.zip", tmp_path / "new.txt"
    _make_zip(source)
    replacement.write_bytes(b"new content")
    rebuild_zip(source, "dir/edit.txt", replacement, output)
    result = subprocess.run(["unzip", "-tq", str(output)], capture_output=True, check=False)
    assert result.returncode == 0, result.stdout + result.stderr


def test_zip64_extras_are_copied(tmp_path: Path) -> None:
    source, output, replacement = tmp_path / "a.zip", tmp_path / "out.zip", tmp_path / "new.txt"
    with zipfile.ZipFile(source, "w", zipfile.ZIP_STORED, allowZip64=True) as archive:
        with archive.open("keep.txt", "w", force_zip64=True) as member:
            member.write(b"zip64 content")
        archive.writestr("edit.txt", b"old")
    replacement.write_bytes(b"new")
    rebuild_zip(source, "edit.txt", replacement, output)
    with zipfile.ZipFile(output) as archive:
        assert archive.read("keep.txt") == b"zip64 content"
        assert archive.read("edit.txt") == b"new"
        assert archive.testzip() is None


_OFFSET_BEYOND_32_BITS = 1 << 32  # 0x1_0000_0000, one past _U32_MAX


def test_build_replaced_central_record_rejects_offset_beyond_32_bits() -> None:
    entry = _Entry(central=bytes(46), name=b"edit.txt", flags=0, method=zipfile.ZIP_STORED, offset=0, offset_in_zip64=None)
    with pytest.raises(ValueError, match="Zip64"):
        _build_replaced_central_record(io.BytesIO(), entry, _OFFSET_BEYOND_32_BITS)


def test_build_appended_central_record_rejects_offset_beyond_32_bits() -> None:
    with pytest.raises(ValueError, match="Zip64"):
        _build_appended_central_record(io.BytesIO(), "new.txt", _OFFSET_BEYOND_32_BITS)


def test_rebuild_zip_does_not_delete_preexisting_output(tmp_path: Path) -> None:
    source, output, replacement = tmp_path / "a.zip", tmp_path / "out.zip", tmp_path / "new.txt"
    _make_zip(source)
    replacement.write_bytes(b"new content")
    output.write_bytes(b"unrelated pre-existing content")
    with pytest.raises(FileExistsError):
        rebuild_zip(source, "dir/edit.txt", replacement, output)
    assert output.read_bytes() == b"unrelated pre-existing content"
