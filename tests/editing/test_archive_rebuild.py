import binascii
import io
import stat
import struct
import tarfile
import zipfile
from pathlib import Path, PurePath, PurePosixPath
from typing import Literal
from unittest.mock import patch

import pytest

from nova_navigator.archive.archives import is_archive_writable
from nova_navigator.editing.archive_rebuild import rebuild_archive

TAR_FORMATS = [(".tar", "w"), (".tar.gz", "w:gz"), (".tgz", "w:gz"), (".tar.bz2", "w:bz2"), (".tbz2", "w:bz2"), (".tar.xz", "w:xz"), (".txz", "w:xz")]


@pytest.mark.parametrize(("suffix", "mode"), TAR_FORMATS)
def test_tar_preserves_members_metadata_and_links(tmp_path: Path, suffix: str, mode: Literal["w", "w:gz", "w:bz2", "w:xz"]) -> None:
    source = tmp_path / f"source{suffix}"
    replacement = tmp_path / "edited"
    replacement.write_bytes(b"replacement content")
    output = tmp_path / "output"
    with tarfile.open(source, mode, pax_headers={"comment": "archive metadata"}) as archive:
        for name, content in [("folder/selected", b"old"), ("unchanged", b"keep")]:
            info = tarfile.TarInfo(name)
            info.size = len(content)
            info.mode = 0o751
            info.uid, info.gid = 123, 456
            info.uname, info.gname = "owner", "group"
            info.mtime = 1234567890.25
            info.pax_headers = {"vendor.custom": "preserve", "size": str(len(content))}
            archive.addfile(info, io.BytesIO(content))
        for kind in [tarfile.SYMTYPE, tarfile.LNKTYPE]:
            info = tarfile.TarInfo(f"link-{kind.decode()}")
            info.type, info.linkname = kind, "unchanged"
            archive.addfile(info)
    before = source.read_bytes()
    rebuild_archive(source, PurePosixPath("/folder/selected"), replacement, output)
    assert source.read_bytes() == before
    with tarfile.open(source) as original, tarfile.open(output) as rebuilt:
        assert rebuilt.pax_headers == original.pax_headers
        assert rebuilt.getnames() == original.getnames()
        for old, new in zip(original.getmembers(), rebuilt.getmembers(), strict=True):
            assert (new.name, new.mode, new.uid, new.gid, new.uname, new.gname, new.mtime, new.type, new.linkname) == (
                old.name,
                old.mode,
                old.uid,
                old.gid,
                old.uname,
                old.gname,
                old.mtime,
                old.type,
                old.linkname,
            )
            assert new.pax_headers["comment"] == "archive metadata"
            if old.isfile():
                stream = rebuilt.extractfile(new)
                assert stream is not None
                with stream:
                    assert stream.read() == (replacement.read_bytes() if old.name == "folder/selected" else b"keep")
                assert new.pax_headers["vendor.custom"] == "preserve"
                assert int(new.pax_headers["size"]) == new.size


@pytest.mark.parametrize("compression", [zipfile.ZIP_STORED, zipfile.ZIP_DEFLATED, zipfile.ZIP_BZIP2, zipfile.ZIP_LZMA])
def test_zip_preserves_metadata_and_compression(tmp_path: Path, compression: int) -> None:
    source, replacement, output = tmp_path / "source.zip", tmp_path / "edited", tmp_path / "output"
    replacement.write_bytes(b"changed and longer")
    with zipfile.ZipFile(source, "w") as archive:
        archive.comment = b"archive comment"
        for name, content in [("selected", b"old"), ("unchanged", b"keep"), ("symlink", b"unchanged")]:
            info = zipfile.ZipInfo(name, (2001, 2, 3, 4, 5, 6))
            info.compress_type = compression
            info.comment = b"entry comment"
            info.create_system = 3
            info.external_attr = ((stat.S_IFLNK if name == "symlink" else stat.S_IFREG) | 0o751) << 16
            info.internal_attr = 1
            info.extra = struct.pack("<HHBI", 0x5455, 5, 1, 1234567890)
            archive.writestr(info, content)
    before = source.read_bytes()
    rebuild_archive(source, PurePosixPath("selected"), replacement, output)
    assert source.read_bytes() == before
    with zipfile.ZipFile(source) as original, zipfile.ZipFile(output) as rebuilt:
        assert rebuilt.comment == original.comment
        assert rebuilt.namelist() == original.namelist()
        for old, new in zip(original.infolist(), rebuilt.infolist(), strict=True):
            for field in ["date_time", "compress_type", "comment", "create_system", "external_attr", "internal_attr", "extra"]:
                assert getattr(new, field) == getattr(old, field)
            assert rebuilt.read(new) == (replacement.read_bytes() if new.filename == "selected" else original.read(old))


def test_zip_allows_local_only_extended_timestamp_metadata(tmp_path: Path) -> None:
    source, replacement, output = tmp_path / "source.zip", tmp_path / "edited", tmp_path / "output.zip"
    replacement.write_bytes(b"new")
    name = b"selected"
    content = b"old"
    local_extra = struct.pack("<HHBI", 0x5455, 5, 1, 1234567890)
    crc = binascii.crc32(content)
    local = (
        struct.pack(
            "<IHHHHHIIIHH",
            0x04034B50,
            20,
            0,
            0,
            0,
            0,
            crc,
            len(content),
            len(content),
            len(name),
            len(local_extra),
        )
        + name
        + local_extra
        + content
    )
    central = (
        struct.pack(
            "<IHHHHHHIIIHHHHHII",
            0x02014B50,
            0x0314,
            20,
            0,
            0,
            0,
            0,
            crc,
            len(content),
            len(content),
            len(name),
            0,
            0,
            0,
            0,
            0,
            0,
        )
        + name
    )
    ending = struct.pack("<IHHHHIIH", 0x06054B50, 0, 0, 1, 1, len(central), len(local), 0)
    source.write_bytes(local + central + ending)

    rebuild_archive(source, PurePosixPath("selected"), replacement, output)

    with zipfile.ZipFile(output) as archive:
        assert archive.read("selected") == b"new"
        assert archive.getinfo("selected").extra == local_extra


def test_zip_allows_different_local_and_central_extended_timestamps(tmp_path: Path) -> None:
    source, replacement, output = tmp_path / "source.zip", tmp_path / "edited", tmp_path / "output.zip"
    replacement.write_bytes(b"new")
    name = b"selected"
    content = b"old"
    ownership_extra = struct.pack("<HHB", 0x7875, 1, 1)
    local_extra = struct.pack("<HHBIII", 0x5455, 13, 7, 1234567890, 1234567891, 1234567892) + ownership_extra
    central_extra = struct.pack("<HHBI", 0x5455, 5, 1, 1234567890) + ownership_extra
    crc = binascii.crc32(content)
    local = struct.pack("<IHHHHHIIIHH", 0x04034B50, 20, 0, 0, 0, 0, crc, len(content), len(content), len(name), len(local_extra)) + name + local_extra + content
    central = struct.pack("<IHHHHHHIIIHHHHHII", 0x02014B50, 0x0314, 20, 0, 0, 0, 0, crc, len(content), len(content), len(name), len(central_extra), 0, 0, 0, 0, 0) + name + central_extra
    ending = struct.pack("<IHHHHIIH", 0x06054B50, 0, 0, 1, 1, len(central), len(local), 0)
    source.write_bytes(local + central + ending)

    rebuild_archive(source, PurePosixPath("selected"), replacement, output)

    with zipfile.ZipFile(output) as archive:
        assert archive.read("selected") == b"new"
        assert archive.getinfo("selected").extra == local_extra


@pytest.mark.parametrize("suffix", [".zip", *[suffix for suffix, _ in TAR_FORMATS]])
def test_writable_formats(suffix: str) -> None:
    assert is_archive_writable(PurePath("archive" + suffix))


@pytest.mark.parametrize("suffix", [".jar", ".war", ".ear", ".apk", ".whl", ".unknown"])
def test_read_only_formats(tmp_path: Path, suffix: str) -> None:
    assert not is_archive_writable(PurePath("archive" + suffix))
    with pytest.raises(ValueError, match=r"writable|supported|read.only"):
        rebuild_archive(tmp_path / ("archive" + suffix), PurePosixPath("selected"), tmp_path / "edit", tmp_path / "out")


def make_archive(source: Path, names: list[str]) -> None:
    if source.suffix == ".zip":
        with zipfile.ZipFile(source, "w") as archive:
            for name in names:
                archive.writestr(name, b"old")
    else:
        with tarfile.open(source, "w") as archive:
            for name in names:
                info = tarfile.TarInfo(name)
                info.size = 3
                archive.addfile(info, io.BytesIO(b"old"))


@pytest.mark.parametrize("suffix", [".zip", ".tar"])
def test_duplicate_selected_name_rejected(tmp_path: Path, suffix: str) -> None:
    source = tmp_path / ("source" + suffix)
    if suffix == ".zip":
        with pytest.warns(UserWarning, match="Duplicate name"):
            make_archive(source, ["selected", "selected"])
    else:
        make_archive(source, ["selected", "selected"])
    replacement = tmp_path / "edit"
    replacement.write_bytes(b"new")
    before = source.read_bytes()
    with pytest.raises(ValueError, match=r"duplicate|ambiguous"):
        rebuild_archive(source, PurePosixPath("selected"), replacement, tmp_path / "out")
    assert source.read_bytes() == before
    assert replacement.read_bytes() == b"new"


@pytest.mark.parametrize("suffix", [".zip", ".tar"])
def test_failure_preserves_source_and_mirror(tmp_path: Path, suffix: str) -> None:
    source, replacement, output = tmp_path / ("source" + suffix), tmp_path / "edit", tmp_path / "out"
    make_archive(source, ["selected", "unchanged"])
    replacement.write_bytes(b"new")
    before = source.read_bytes()
    copy_function = "shutil.copyfileobj" if suffix == ".zip" else "tarfile.copyfileobj"
    with patch(f"nova_navigator.editing.archive_rebuild.{copy_function}", side_effect=OSError("disk full")), pytest.raises(OSError, match="disk full"):
        rebuild_archive(source, PurePosixPath("selected"), replacement, output)
    assert source.read_bytes() == before
    assert replacement.read_bytes() == b"new"
    assert not output.exists()


@pytest.mark.parametrize("case", ["encryption", "compression", "extra"])
def test_unsupported_zip_rejected(tmp_path: Path, case: str) -> None:
    source, replacement = tmp_path / "source.zip", tmp_path / "edit"
    replacement.write_bytes(b"new")
    with zipfile.ZipFile(source, "w") as archive:
        info = zipfile.ZipInfo("selected")
        if case == "extra":
            info.extra = struct.pack("<HH", 0xFFFF, 0)
        archive.writestr(info, b"old")
    data = bytearray(source.read_bytes())
    central = data.index(b"PK\x01\x02")
    if case == "encryption":
        struct.pack_into("<H", data, 6, 1)
        struct.pack_into("<H", data, central + 8, 1)
    elif case == "compression":
        struct.pack_into("<H", data, 8, 99)
        struct.pack_into("<H", data, central + 10, 99)
    source.write_bytes(data)
    with pytest.raises(ValueError, match=r"Unsupported|unsupported|encrypted"):
        rebuild_archive(source, PurePosixPath("selected"), replacement, tmp_path / "out")
    assert source.read_bytes() == data


@pytest.mark.parametrize("suffix", [".zip", ".tar"])
def test_output_cannot_alias_source_or_replacement(tmp_path: Path, suffix: str) -> None:
    source, replacement = tmp_path / ("source" + suffix), tmp_path / "edit"
    make_archive(source, ["selected"])
    replacement.write_bytes(b"new")
    before = source.read_bytes()
    for output in [source, replacement]:
        with pytest.raises(FileExistsError):
            rebuild_archive(source, PurePosixPath("selected"), replacement, output)
    assert source.read_bytes() == before
    assert replacement.read_bytes() == b"new"


@pytest.mark.parametrize("suffix", [".zip", ".tar"])
def test_missing_selected_member(tmp_path: Path, suffix: str) -> None:
    source, replacement, output = tmp_path / ("source" + suffix), tmp_path / "edit", tmp_path / "out"
    make_archive(source, ["unchanged"])
    replacement.write_bytes(b"new")
    with pytest.raises(FileNotFoundError, match="member"):
        rebuild_archive(source, PurePosixPath("selected"), replacement, output)
    assert not output.exists()


@pytest.mark.parametrize("suffix", [".zip", ".tar"])
def test_selected_symlink_rejected(tmp_path: Path, suffix: str) -> None:
    source, replacement = tmp_path / ("source" + suffix), tmp_path / "edit"
    replacement.write_bytes(b"new")
    if suffix == ".zip":
        with zipfile.ZipFile(source, "w") as archive:
            info = zipfile.ZipInfo("selected")
            info.external_attr = (stat.S_IFLNK | 0o777) << 16
            archive.writestr(info, b"target")
    else:
        with tarfile.open(source, "w") as archive:
            info = tarfile.TarInfo("selected")
            info.type, info.linkname = tarfile.SYMTYPE, "target"
            archive.addfile(info)
    with pytest.raises(ValueError, match="regular"):
        rebuild_archive(source, PurePosixPath("selected"), replacement, tmp_path / "out")


def test_sparse_tar_rejected(tmp_path: Path) -> None:
    source, replacement = tmp_path / "source.tar", tmp_path / "edit"
    replacement.write_bytes(b"new")
    with tarfile.open(source, "w") as archive:
        info = tarfile.TarInfo("selected")
        info.pax_headers = {"GNU.sparse.size": "0", "GNU.sparse.map": "0,0"}
        archive.addfile(info)
    with pytest.raises(ValueError, match="sparse"):
        rebuild_archive(source, PurePosixPath("selected"), replacement, tmp_path / "out")


def test_zip_trailing_data_rejected(tmp_path: Path) -> None:
    source, replacement = tmp_path / "source.zip", tmp_path / "edit"
    make_archive(source, ["selected"])
    with source.open("ab") as stream:
        stream.write(b"unsupported trailing data")
    replacement.write_bytes(b"new")
    with pytest.raises(ValueError, match="trailing"):
        rebuild_archive(source, PurePosixPath("selected"), replacement, tmp_path / "out")


def test_zip_zero_permissions_preserved(tmp_path: Path) -> None:
    source, replacement, output = tmp_path / "source.zip", tmp_path / "edit", tmp_path / "out"
    make_archive(source, ["selected"])
    raw = bytearray(source.read_bytes())
    central = raw.index(b"PK\x01\x02")
    struct.pack_into("<I", raw, central + 38, 0)
    source.write_bytes(raw)
    replacement.write_bytes(b"new")
    rebuild_archive(source, PurePosixPath("selected"), replacement, output)
    with zipfile.ZipFile(output) as archive:
        assert archive.getinfo("selected").external_attr == 0


def test_validation_reads_and_rejects_wrong_selected_content(tmp_path: Path) -> None:
    source, replacement, output = tmp_path / "source.zip", tmp_path / "edit", tmp_path / "out"
    make_archive(source, ["selected"])
    replacement.write_bytes(b"new")
    # A broken writer can produce a structurally valid archive; validation must
    # actually consume and compare the selected member's bytes.
    with patch("nova_navigator.editing.archive_rebuild.shutil.copyfileobj"), pytest.raises(ValueError, match="does not match"):
        rebuild_archive(source, PurePosixPath("selected"), replacement, output)
    assert not output.exists()


def test_changing_global_tar_metadata_rejected(tmp_path: Path) -> None:
    source, replacement = tmp_path / "source.tar", tmp_path / "edit"
    replacement.write_bytes(b"new")
    with tarfile.open(source, "w", pax_headers={"comment": "first"}) as archive:
        info = tarfile.TarInfo("selected")
        archive.addfile(info)
        # A second global header applies only to following entries.
        # Let the public header encoder calculate correct PAX record lengths.
        payload = tarfile.TarInfo.create_pax_global_header({"comment": "second"})
        assert archive.fileobj is not None
        archive.fileobj.write(payload)
        archive.offset += len(payload)
        archive.addfile(tarfile.TarInfo("unchanged"))
    with pytest.raises(ValueError, match="global"):
        rebuild_archive(source, PurePosixPath("selected"), replacement, tmp_path / "out")


def test_zip_directory_metadata_preserved(tmp_path: Path) -> None:
    source, replacement, output = tmp_path / "source.zip", tmp_path / "edit", tmp_path / "out"
    replacement.write_bytes(b"new")
    with zipfile.ZipFile(source, "w") as archive:
        directory = zipfile.ZipInfo("folder/", (2001, 2, 3, 4, 5, 6))
        directory.external_attr = ((stat.S_IFDIR | 0o750) << 16) | 0x10
        directory.comment = b"directory comment"
        archive.writestr(directory, b"")
        archive.writestr("folder/selected", b"old")
    rebuild_archive(source, PurePosixPath("folder/selected"), replacement, output)
    with zipfile.ZipFile(output) as archive:
        rebuilt = archive.getinfo("folder/")
        assert rebuilt.is_dir()
        assert rebuilt.external_attr == directory.external_attr
        assert rebuilt.comment == directory.comment
        assert rebuilt.date_time == directory.date_time
        assert archive.read(rebuilt) == b""


@pytest.mark.parametrize("file_type", [stat.S_IFIFO, stat.S_IFSOCK, stat.S_IFCHR, stat.S_IFBLK, stat.S_IFDIR])
def test_selected_zip_nonregular_type_rejected(tmp_path: Path, file_type: int) -> None:
    source, replacement, output = tmp_path / "source.zip", tmp_path / "edit", tmp_path / "out"
    replacement.write_bytes(b"new")
    with zipfile.ZipFile(source, "w") as archive:
        info = zipfile.ZipInfo("selected")
        info.create_system = 3
        info.external_attr = (file_type | 0o600) << 16
        archive.writestr(info, b"old")
    before = source.read_bytes()
    with pytest.raises(ValueError, match="regular"):
        rebuild_archive(source, PurePosixPath("selected"), replacement, output)
    assert source.read_bytes() == before
    assert replacement.read_bytes() == b"new"
    assert not output.exists()


@pytest.mark.parametrize(("disk", "directory_disk", "disk_entries"), [(1, 0, 1), (0, 1, 1), (0, 0, 0)])
def test_zip_multivolume_end_record_rejected(tmp_path: Path, disk: int, directory_disk: int, disk_entries: int) -> None:
    source, replacement, output = tmp_path / "source.zip", tmp_path / "edit", tmp_path / "out"
    make_archive(source, ["selected"])
    replacement.write_bytes(b"new")
    raw = bytearray(source.read_bytes())
    ending = raw.rindex(b"PK\x05\x06")
    struct.pack_into("<HHH", raw, ending + 4, disk, directory_disk, disk_entries)
    source.write_bytes(raw)
    with zipfile.ZipFile(source) as archive:
        assert archive.getinfo("selected").volume == 0
    with pytest.raises(ValueError, match=r"multi.volume"):
        rebuild_archive(source, PurePosixPath("selected"), replacement, output)
    assert source.read_bytes() == raw
    assert replacement.read_bytes() == b"new"
    assert not output.exists()


@pytest.mark.parametrize("member_type", [tarfile.DIRTYPE, tarfile.SYMTYPE, tarfile.LNKTYPE, tarfile.CHRTYPE, tarfile.BLKTYPE, tarfile.FIFOTYPE])
def test_tar_nonregular_payload_rejected(tmp_path: Path, member_type: bytes) -> None:
    source, replacement, output = tmp_path / "source.tar", tmp_path / "edit", tmp_path / "out"
    replacement.write_bytes(b"new")
    with tarfile.open(source, "w") as archive:
        selected = tarfile.TarInfo("selected")
        selected.size = 3
        archive.addfile(selected, io.BytesIO(b"old"))
        unusual = tarfile.TarInfo("nonregular")
        unusual.type = member_type
        unusual.linkname = "selected" if member_type in {tarfile.SYMTYPE, tarfile.LNKTYPE} else ""
        unusual.size = 6
        archive.addfile(unusual, io.BytesIO(b"SECRET"))
    before = source.read_bytes()
    with pytest.raises(ValueError, match=r"nonregular.*payload"):
        rebuild_archive(source, PurePosixPath("selected"), replacement, output)
    assert source.read_bytes() == before
    assert replacement.read_bytes() == b"new"
    assert not output.exists()
