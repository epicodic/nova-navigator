"""Stream an archive into a new file, substituting one regular member."""

import copy
import hashlib
import shutil
import stat
import struct
import tarfile
import zipfile
from pathlib import Path, PurePosixPath
from typing import IO, Literal

from nova_navigator.archive.archives import is_archive_writable

_ZIP_LOCAL_HEADER_SIZE = 30
_COPY_BUFFER_SIZE = 1024 * 1024
_ZIP_COMPRESSION = {zipfile.ZIP_STORED, zipfile.ZIP_DEFLATED, zipfile.ZIP_BZIP2, zipfile.ZIP_LZMA}
_ZIP_EXTENDED_TIMESTAMP_FIELD = 0x5455
_ZIP_UNIX_OWNERSHIP_FIELD = 0x7875
# Timestamp and Unix ownership fields do not depend on content or archive offsets.
_ZIP_EXTRA_FIELDS = {_ZIP_EXTENDED_TIMESTAMP_FIELD, _ZIP_UNIX_OWNERSHIP_FIELD}


def rebuild_archive(source: Path, member_path: PurePosixPath, replacement: Path, output: Path) -> None:
    """Build and validate a separate archive, leaving source and replacement intact.

    The output must not already exist, including as a symlink or hard link.
    Unsupported metadata, ambiguous selected names, and nonregular selections
    raise ValueError. A failed rebuild removes its incomplete output.
    """
    if not is_archive_writable(source):
        raise ValueError(f"Archive format is not writable: {source.name}")
    name = member_path.as_posix().lstrip("/")
    # Exclusive creation also protects the source through aliases and hard links.
    with output.open("xb") as destination:
        try:
            if source.suffix == ".zip":
                _rebuild_zip(source, name, replacement, destination)
            else:
                _rebuild_tar(source, name, replacement, destination)
            destination.flush()
            _validate_output(output, name, replacement, source.suffix == ".zip")
        except BaseException:
            output.unlink(missing_ok=True)
            raise


def _check_selected(names: list[str], selected: str) -> None:
    count = names.count(selected)
    if count > 1:
        raise ValueError(f"Ambiguous duplicate archive member: {selected}")
    if not count:
        raise FileNotFoundError(f"Archive member not found: {selected}")


def _check_zip_extra(extra: bytes) -> None:
    offset = 0
    while offset < len(extra):
        if offset + 4 > len(extra):
            raise ValueError("Unsupported malformed ZIP extra metadata")
        field, size = struct.unpack_from("<HH", extra, offset)
        offset += 4 + size
        if field not in _ZIP_EXTRA_FIELDS or offset > len(extra):
            raise ValueError(f"Unsupported ZIP extra metadata: {field:#x}")


def _zip_extra_fields(extra: bytes) -> list[tuple[int, bytes]]:
    """Return the validated extra fields as identifier and payload pairs."""
    _check_zip_extra(extra)
    offset = 0
    fields: list[tuple[int, bytes]] = []
    while offset < len(extra):
        field, size = struct.unpack_from("<HH", extra, offset)
        offset += 4
        fields.append((field, extra[offset : offset + size]))
        offset += size
    return fields


def _check_zip_member(info: zipfile.ZipInfo) -> None:
    if info.flag_bits & 1:
        raise ValueError("Unsupported encrypted ZIP member")
    allowed_flags = 0x808 | (2 if info.compress_type == zipfile.ZIP_LZMA else 0)
    if info.flag_bits & ~allowed_flags or info.volume:
        raise ValueError("Unsupported ZIP flags or multi-volume metadata")
    if info.compress_type not in _ZIP_COMPRESSION:
        raise ValueError(f"Unsupported ZIP compression: {info.compress_type}")
    _check_zip_extra(info.extra)


def _local_zip_extra(source: IO[bytes], info: zipfile.ZipInfo) -> bytes:
    source.seek(info.header_offset)
    header = source.read(_ZIP_LOCAL_HEADER_SIZE)
    if len(header) != _ZIP_LOCAL_HEADER_SIZE or header[:4] != b"PK\x03\x04":
        raise ValueError("Unsupported ZIP local header")
    name_size, extra_size = struct.unpack_from("<HH", header, 26)
    source.seek(name_size, 1)
    extra = source.read(extra_size)
    local_fields = _zip_extra_fields(extra)
    central_fields = _zip_extra_fields(info.extra)
    # ZIP writers commonly store more timestamp values in the local header
    # than in the central directory. Preserve that richer timestamp record by
    # promoting it to the rebuilt central entry. Other conflicting metadata
    # remains rejected because zipfile cannot represent both forms separately.
    local_non_timestamp = sorted(field for field in local_fields if field[0] != _ZIP_EXTENDED_TIMESTAMP_FIELD)
    central_non_timestamp = sorted(field for field in central_fields if field[0] != _ZIP_EXTENDED_TIMESTAMP_FIELD)
    if extra and info.extra and extra != info.extra and local_non_timestamp != central_non_timestamp:
        raise ValueError("Unsupported differing ZIP local and central metadata")
    return extra or info.extra


def _rebuild_zip(source: Path, name: str, replacement: Path, output: IO[bytes]) -> None:
    with zipfile.ZipFile(source) as original, source.open("rb") as raw:
        raw.seek(-22 - len(original.comment), 2)
        ending = raw.read(22)
        if ending[:4] != b"PK\x05\x06" or struct.unpack_from("<H", ending, 20)[0] != len(original.comment):
            raise ValueError("Unsupported ZIP trailing data")
        disk, directory_disk, disk_entries, total_entries = struct.unpack_from("<HHHH", ending, 4)
        if disk or directory_disk or disk_entries != total_entries:
            raise ValueError("Unsupported multi-volume ZIP end record")
        members = original.infolist()
        _check_selected([info.filename for info in members], name)
        if min(info.header_offset for info in members) != 0:
            raise ValueError("Unsupported ZIP archive prefix")
        local_extras: list[bytes] = []
        for info in members:
            _check_zip_member(info)
            local_extras.append(_local_zip_extra(raw, info))
            file_type = stat.S_IFMT(info.external_attr >> 16)
            if info.filename == name and (info.is_dir() or file_type not in {0, stat.S_IFREG}):
                raise ValueError("Selected ZIP member must be a regular file")
        with zipfile.ZipFile(output, "w") as rebuilt:
            rebuilt.comment = original.comment
            for info, local_extra in zip(members, local_extras, strict=True):
                updated = copy.copy(info)
                updated.extra = local_extra
                selected = info.filename == name
                if selected:
                    updated.file_size = replacement.stat().st_size
                with replacement.open("rb") if selected else original.open(info) as reader, rebuilt.open(updated, "w") as writer:
                    shutil.copyfileobj(reader, writer, _COPY_BUFFER_SIZE)
                # zipfile substitutes default permissions when the original value
                # is zero. Restore it before the central directory is written.
                updated.external_attr = info.external_attr


def _tar_write_mode(source: Path) -> Literal["w:", "w:gz", "w:bz2", "w:xz"]:
    if source.name.endswith((".tar.gz", ".tgz")):
        return "w:gz"
    if source.name.endswith((".tar.bz2", ".tbz2")):
        return "w:bz2"
    if source.name.endswith((".tar.xz", ".txz")):
        return "w:xz"
    return "w:"


def _rebuild_tar(source: Path, name: str, replacement: Path, output: IO[bytes]) -> None:
    with tarfile.open(source, "r:*") as original:
        initial_pax_headers = dict(original.pax_headers)
        members = original.getmembers()
        if original.pax_headers != initial_pax_headers:
            raise ValueError("Unsupported changing TAR global PAX metadata")
        _check_selected([info.name for info in members], name)
        for info in members:
            if info.issparse() or any(key.startswith("GNU.sparse") for key in info.pax_headers):
                raise ValueError("Unsupported sparse TAR member")
            if info.type not in {tarfile.REGTYPE, tarfile.AREGTYPE, tarfile.DIRTYPE, tarfile.SYMTYPE, tarfile.LNKTYPE, tarfile.CHRTYPE, tarfile.BLKTYPE, tarfile.FIFOTYPE}:
                raise ValueError("Unsupported TAR member type")
            if not info.isfile() and info.size != 0:
                raise ValueError("Unsupported nonregular TAR member payload")
            if info.name == name and not info.isfile():
                raise ValueError("Selected TAR member must be a regular file")
        with tarfile.open(fileobj=output, mode=_tar_write_mode(source), format=tarfile.PAX_FORMAT, pax_headers=original.pax_headers) as rebuilt:
            for info in members:
                updated = copy.deepcopy(info)
                if info.name == name:
                    updated.size = replacement.stat().st_size
                    if "size" in updated.pax_headers:
                        updated.pax_headers = {**updated.pax_headers, "size": str(updated.size)}
                    with replacement.open("rb") as reader:
                        rebuilt.addfile(updated, reader)
                elif info.isfile():
                    reader = original.extractfile(info)
                    if reader is None:
                        raise ValueError(f"Unreadable TAR member: {info.name}")
                    with reader:
                        rebuilt.addfile(updated, reader)
                else:
                    rebuilt.addfile(updated)


def _digest(stream: IO[bytes]) -> bytes:
    digest = hashlib.sha256()
    while chunk := stream.read(_COPY_BUFFER_SIZE):
        digest.update(chunk)
    return digest.digest()


def _validate_output(output: Path, name: str, replacement: Path, is_zip: bool) -> None:
    with replacement.open("rb") as mirror:
        expected = _digest(mirror)
    if is_zip:
        with zipfile.ZipFile(output) as archive, archive.open(name) as member:
            actual = _digest(member)
    else:
        with tarfile.open(output, "r:*") as archive:
            member = archive.extractfile(name)
            if member is None:
                raise ValueError("Rebuilt TAR member is unreadable")
            with member:
                actual = _digest(member)
    if actual != expected:
        raise ValueError("Rebuilt member does not match replacement")
