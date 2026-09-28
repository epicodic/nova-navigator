"""Replace or add one ZIP member while copying every other entry byte for byte.

Rewriting every entry through :mod:`zipfile` cannot reproduce a local-header extra field that
differs from its central-directory counterpart, which real-world ZIP archives sometimes have.
This module instead copies every unchanged entry's bytes verbatim (from its local-header offset
up to the next entry's offset, or the central directory for the last entry) and only patches the
32-bit (or Zip64) offset field of its central directory record. Only the replaced (or appended)
entry gets a freshly written local header and central directory record.
"""

from __future__ import annotations

import bz2
import struct
import time
import zlib
from dataclasses import dataclass
from pathlib import Path
from typing import IO, Protocol

_EOCD_SIGNATURE = b"PK\x05\x06"
_ZIP64_EOCD_SIGNATURE = b"PK\x06\x06"
_ZIP64_LOCATOR_SIGNATURE = b"PK\x06\x07"
_CENTRAL_SIGNATURE = b"PK\x01\x02"
_LOCAL_SIGNATURE = b"PK\x03\x04"

_U16_MAX = 0xFFFF
_U32_MAX = 0xFFFFFFFF

_STORED, _DEFLATED, _BZIP2 = 0, 8, 12
_SUPPORTED_REPLACE_METHODS = frozenset({_STORED, _DEFLATED, _BZIP2})

_FLAG_ENCRYPTED = 0x1
_FLAG_DATA_DESCRIPTOR = 0x8
_UTF8_FLAG = 0x800

_ZIP64_EXTRA_ID = 0x0001

_CHUNK = 1024 * 1024

# End-of-central-directory record.
_EOCD_STRUCT = "<4sHHHHIIH"
_EOCD_FIXED_SIZE = struct.calcsize(_EOCD_STRUCT)
_EOCD_MAX_COMMENT = _U16_MAX
_EOCD_SEARCH_WINDOW = _EOCD_FIXED_SIZE + _EOCD_MAX_COMMENT

# Zip64 end-of-central-directory locator and record.
_ZIP64_LOCATOR_STRUCT = "<4sIQI"
_ZIP64_LOCATOR_SIZE = struct.calcsize(_ZIP64_LOCATOR_STRUCT)
_ZIP64_LOCATOR_TOTAL_DISKS = 1
_ZIP64_EOCD_STRUCT = "<4sQHHIIQQQQ"
_ZIP64_EOCD_SIZE_FIELD = 44  # Fixed record size (minus the leading signature and size field).
_ZIP64_VERSION = 45

# Central directory record layout (fixed 46-byte prefix).
_CENTRAL_FIXED_SIZE = 46
_CENTRAL_VERSION_NEEDED_OFFSET = 6
_CENTRAL_FLAGS_OFFSET = 8
_CENTRAL_CRC_OFFSET = 16
_CENTRAL_CSIZE_OFFSET = 20
_CENTRAL_USIZE_OFFSET = 24
_CENTRAL_NAME_LEN_OFFSET = 28
_CENTRAL_EXTRA_LEN_OFFSET = 30
_CENTRAL_OFFSET_OFFSET = 42

# Local file header layout (fixed 30-byte prefix).
_LOCAL_HEADER_SIZE = 30
_LOCAL_TIME_OFFSET = 10
_LOCAL_SIZES_OFFSET = 14
_LOCAL_NAME_LEN_OFFSET = 26

# Metadata used for a freshly appended member.
_APPENDED_VERSION_MADE_BY = 0x031E  # Unix, ZIP spec 3.0.
_APPENDED_VERSION_NEEDED = 20
_APPENDED_EXTERNAL_ATTR = 0o100644 << 16


@dataclass
class _Entry:
    """One central directory record, parsed enough to copy or patch it."""

    central: bytes  # Full central directory record (fixed prefix + name + extra + comment).
    name: bytes
    flags: int
    method: int
    offset: int
    offset_in_zip64: int | None  # Byte index of the offset inside `central`, when stored there.

    def decoded_name(self) -> str:
        """Decode `name` using the encoding implied by the UTF-8 flag bit."""
        return self.name.decode("utf-8" if self.flags & _UTF8_FLAG else "cp437")


@dataclass
class _Directory:
    """The parsed end-of-central-directory record and central directory entries."""

    entries: list[_Entry]
    central_offset: int
    comment: bytes
    zip64: bool


class _Compressor(Protocol):
    """The subset of `zlib.compressobj`/`bz2.BZ2Compressor` used for streaming compression."""

    def compress(self, data: bytes, /) -> bytes: ...
    def flush(self) -> bytes: ...


def rebuild_zip(source: Path, member: str, replacement: Path, output: Path) -> None:
    """Write *output* as *source* with *member* replaced by (or added from) *replacement*.

    Every entry other than *member* is copied byte for byte, including its local header, name,
    extra field and compressed data, so metadata this module does not understand (such as a
    local-header extra field differing from its central-directory counterpart) survives intact.

    Args:
        source: Path to the existing ZIP archive to read.
        member: Archive-relative name of the member to replace, or add if absent.
        replacement: Path to a local file whose bytes become *member*'s new content.
        output: Path to write the rebuilt archive to; created exclusively ("xb+").

    Raises:
        ValueError: If the archive layout cannot be copied safely (multi-volume, unsupported
            trailing data, an ambiguous duplicate member, an encrypted or LZMA-compressed
            *member*, or a replacement too large to fit a 32-bit ZIP field).
    """
    # output.open("xb+") is deliberately outside the try/finally below: if it fails (e.g. because
    # output already exists), that failure must propagate without deleting a file we never created.
    with source.open("rb") as raw, output.open("xb+") as out:
        completed = False
        try:
            directory = _read_directory(raw)
            selected = [entry for entry in directory.entries if entry.decoded_name() == member]
            if len(selected) > 1:
                raise ValueError(f"Ambiguous duplicate archive member: {member}")
            if directory.entries and min(entry.offset for entry in directory.entries) != 0:
                raise ValueError("Unsupported ZIP archive prefix")
            target = selected[0] if selected else None
            if target is not None:
                if target.flags & _FLAG_ENCRYPTED:
                    raise ValueError("Cannot replace an encrypted ZIP member")
                if target.method not in _SUPPORTED_REPLACE_METHODS:
                    raise ValueError(f"Cannot replace ZIP member with method {target.method} (e.g. LZMA)")
            new_offsets = _copy_entries(raw, out, directory, target, replacement, member)
            _write_central(out, directory, target, new_offsets, member)
            completed = True
        finally:
            if not completed:
                output.unlink(missing_ok=True)


def _read_directory(raw: IO[bytes]) -> _Directory:
    """Parse the end-of-central-directory record (and its Zip64 variant) plus all entries."""
    tail, eocd_pos, eocd_abs = _locate_eocd(raw)
    disk, cd_disk, disk_entries, total_entries, cd_size, cd_offset, comment = _parse_eocd(tail, eocd_pos)
    if disk or cd_disk or disk_entries != total_entries:
        raise ValueError("Unsupported multi-volume ZIP")

    zip64 = False
    end_record_location = eocd_abs
    if total_entries == _U16_MAX or _U32_MAX in (cd_size, cd_offset):
        total_entries, cd_size, cd_offset, end_record_location = _read_zip64_eocd(raw, eocd_abs)
        zip64 = True

    # A prefix (e.g. a self-extracting stub) shifts every recorded offset by the same amount:
    # the gap between where the central directory should end (right before the end records)
    # and where its recorded offset plus size says it ends.
    concat = end_record_location - cd_size - cd_offset
    cd_offset += concat

    entries = _parse_central_directory(raw, cd_offset, cd_size, concat)
    return _Directory(entries=entries, central_offset=cd_offset, comment=bytes(comment), zip64=zip64)


def _locate_eocd(raw: IO[bytes]) -> tuple[bytes, int, int]:
    """Find the end-of-central-directory record.

    Returns (the tail bytes it was found in, its offset within that tail, its absolute offset).
    """
    raw.seek(0, 2)
    file_size = raw.tell()
    window = min(file_size, _EOCD_SEARCH_WINDOW)
    raw.seek(file_size - window)
    tail = raw.read(window)
    eocd_pos = tail.rfind(_EOCD_SIGNATURE)
    if eocd_pos == -1:
        raise ValueError("Unsupported ZIP archive: end of central directory record not found")
    return tail, eocd_pos, file_size - window + eocd_pos


def _parse_eocd(tail: bytes, eocd_pos: int) -> tuple[int, int, int, int, int, int, bytes]:
    """Unpack the fixed EOCD fields; reject a comment that doesn't reach exactly to EOF."""
    _signature, disk, cd_disk, disk_entries, total_entries, cd_size, cd_offset, comment_len = struct.unpack_from(_EOCD_STRUCT, tail, eocd_pos)
    comment_start = eocd_pos + _EOCD_FIXED_SIZE
    comment = tail[comment_start : comment_start + comment_len]
    if comment_start + comment_len != len(tail):
        raise ValueError("Unsupported ZIP trailing data")
    return disk, cd_disk, disk_entries, total_entries, cd_size, cd_offset, comment


def _read_zip64_eocd(raw: IO[bytes], eocd_abs: int) -> tuple[int, int, int, int]:
    """Parse the Zip64 locator and end-of-central-directory record preceding the classic EOCD.

    Returns (total_entries, cd_size, cd_offset, absolute offset of the Zip64 EOCD record).
    """
    raw.seek(eocd_abs - _ZIP64_LOCATOR_SIZE)
    loc_signature, loc_disk, zip64_eocd_offset, total_disks = struct.unpack(_ZIP64_LOCATOR_STRUCT, raw.read(_ZIP64_LOCATOR_SIZE))
    if loc_signature != _ZIP64_LOCATOR_SIGNATURE:
        raise ValueError("Unsupported ZIP64 end of central directory locator")
    if loc_disk or total_disks != _ZIP64_LOCATOR_TOTAL_DISKS:
        raise ValueError("Unsupported multi-volume ZIP")

    raw.seek(zip64_eocd_offset)
    zip64_record = raw.read(struct.calcsize(_ZIP64_EOCD_STRUCT))
    z_signature, _size, _version_made, _version_needed, z_disk, z_cd_disk, _disk_entries, total_entries, cd_size, cd_offset = struct.unpack(_ZIP64_EOCD_STRUCT, zip64_record)
    if z_signature != _ZIP64_EOCD_SIGNATURE:
        raise ValueError("Unsupported ZIP64 end of central directory record")
    if z_disk or z_cd_disk:
        raise ValueError("Unsupported multi-volume ZIP")
    return total_entries, cd_size, cd_offset, zip64_eocd_offset


def _parse_central_directory(raw: IO[bytes], cd_offset: int, cd_size: int, concat: int) -> list[_Entry]:
    """Parse every central directory record, resolving Zip64 offsets and applying *concat*."""
    raw.seek(cd_offset)
    cd_bytes = raw.read(cd_size)
    entries: list[_Entry] = []
    pos = 0
    while pos < len(cd_bytes):
        if cd_bytes[pos : pos + len(_CENTRAL_SIGNATURE)] != _CENTRAL_SIGNATURE:
            raise ValueError("Corrupt ZIP central directory")
        flags, method = struct.unpack_from("<HH", cd_bytes, pos + _CENTRAL_FLAGS_OFFSET)
        name_len, extra_len, comment_len_entry = struct.unpack_from("<HHH", cd_bytes, pos + _CENTRAL_NAME_LEN_OFFSET)
        record_len = _CENTRAL_FIXED_SIZE + name_len + extra_len + comment_len_entry
        record = cd_bytes[pos : pos + record_len]
        name = record[_CENTRAL_FIXED_SIZE : _CENTRAL_FIXED_SIZE + name_len]
        offset = struct.unpack_from("<I", record, _CENTRAL_OFFSET_OFFSET)[0]
        offset_in_zip64: int | None = None
        if offset == _U32_MAX:
            offset, offset_in_zip64 = _find_zip64_offset(record, name_len, extra_len)
        entries.append(_Entry(central=record, name=name, flags=flags, method=method, offset=offset + concat, offset_in_zip64=offset_in_zip64))
        pos += record_len
    return entries


def _find_zip64_offset(record: bytes, name_len: int, extra_len: int) -> tuple[int, int]:
    """Locate the 8-byte header-offset value inside a central record's Zip64 extra field.

    Returns the resolved 64-bit offset and the absolute byte index of that value within
    *record*, so it can later be patched with `struct.pack_into`.
    """
    csize, usize = struct.unpack_from("<II", record, _CENTRAL_CSIZE_OFFSET)
    extra_start = _CENTRAL_FIXED_SIZE + name_len
    extra = record[extra_start : extra_start + extra_len]
    pos = 0
    while pos + 4 <= len(extra):
        field_id, field_len = struct.unpack_from("<HH", extra, pos)
        data_start = pos + 4
        if field_id == _ZIP64_EXTRA_ID:
            skip = (8 if usize == _U32_MAX else 0) + (8 if csize == _U32_MAX else 0)
            value_offset = extra_start + data_start + skip
            return struct.unpack_from("<Q", record, value_offset)[0], value_offset
        pos = data_start + field_len
    raise ValueError("Corrupt ZIP64 extra field: header offset not found")


def _strip_zip64_extra(extra: bytes) -> bytes:
    """Return *extra* with any Zip64 (id `0x0001`) field removed."""
    result = bytearray()
    pos = 0
    while pos + 4 <= len(extra):
        field_id, field_len = struct.unpack_from("<HH", extra, pos)
        field_total = 4 + field_len
        if field_id != _ZIP64_EXTRA_ID:
            result += extra[pos : pos + field_total]
        pos += field_total
    return bytes(result)


def _encode_member_name(member: str) -> tuple[bytes, int]:
    """Encode *member* as ASCII when possible, else UTF-8 with the UTF-8 name flag set."""
    if member.isascii():
        return member.encode("ascii"), 0
    return member.encode("utf-8"), _UTF8_FLAG


def _copy_span(raw: IO[bytes], out: IO[bytes], start: int, end: int) -> None:
    """Copy the byte range [*start*, *end*) from *raw* to *out*."""
    raw.seek(start)
    remaining = end - start
    while remaining > 0:
        chunk = raw.read(min(_CHUNK, remaining))
        if not chunk:
            raise ValueError("Unexpected end of ZIP archive while copying an entry")
        out.write(chunk)
        remaining -= len(chunk)


def _read_original_local_extra(raw: IO[bytes], offset: int) -> bytes:
    """Read the local-header extra field of the entry whose local header starts at *offset*."""
    raw.seek(offset)
    header = raw.read(_LOCAL_HEADER_SIZE)
    name_len, extra_len = struct.unpack_from("<HH", header, _LOCAL_NAME_LEN_OFFSET)
    raw.seek(offset + _LOCAL_HEADER_SIZE + name_len)
    return raw.read(extra_len)


def _copy_entries(
    raw: IO[bytes],
    out: IO[bytes],
    directory: _Directory,
    target: _Entry | None,
    replacement: Path,
    member: str,
) -> dict[int, int]:
    """Copy every entry's byte span to *out*, replacing *target* (or appending *member*).

    Returns a mapping from `id(entry)` to its new offset in *out*; an appended member's offset
    is recorded under key `0`.
    """
    new_offsets: dict[int, int] = {}
    ordered = sorted(directory.entries, key=lambda entry: entry.offset)
    for index, entry in enumerate(ordered):
        span_end = ordered[index + 1].offset if index + 1 < len(ordered) else directory.central_offset
        new_offsets[id(entry)] = out.tell()
        if entry is target:
            _write_target_member(raw, out, entry, replacement)
        else:
            _copy_span(raw, out, entry.offset, span_end)
    if target is None:
        new_offsets[0] = out.tell()
        _write_appended_member(out, member, replacement)
    return new_offsets


def _write_target_member(raw: IO[bytes], out: IO[bytes], target: _Entry, replacement: Path) -> None:
    """Write the freshly compressed *replacement* using metadata kept from *target*'s original."""
    version_needed, flags, method, dos_time, dos_date = struct.unpack_from("<HHHHH", target.central, _CENTRAL_VERSION_NEEDED_OFFSET)
    local_extra = _strip_zip64_extra(_read_original_local_extra(raw, target.offset))
    _write_local_member(
        out,
        name=target.name,
        flags=flags,
        method=method,
        dos_time=dos_time,
        dos_date=dos_date,
        version_needed=version_needed,
        local_extra=local_extra,
        replacement=replacement,
    )


def _write_appended_member(out: IO[bytes], member: str, replacement: Path) -> None:
    """Write a brand-new deflated entry named *member* with *replacement*'s content."""
    name, flags = _encode_member_name(member)
    now = time.localtime()
    dos_date = ((now.tm_year - 1980) << 9) | (now.tm_mon << 5) | now.tm_mday
    dos_time = (now.tm_hour << 11) | (now.tm_min << 5) | (now.tm_sec // 2)
    _write_local_member(
        out,
        name=name,
        flags=flags,
        method=_DEFLATED,
        dos_time=dos_time,
        dos_date=dos_date,
        version_needed=_APPENDED_VERSION_NEEDED,
        local_extra=b"",
        replacement=replacement,
    )


def _make_compressor(method: int) -> _Compressor | None:
    """Return a streaming compressor for *method*, or `None` for stored (uncompressed) data."""
    if method == _DEFLATED:
        return zlib.compressobj(9, zlib.DEFLATED, -15)
    if method == _BZIP2:
        return bz2.BZ2Compressor()
    return None


def _write_local_member(
    out: IO[bytes],
    *,
    name: bytes,
    flags: int,
    method: int,
    dos_time: int,
    dos_date: int,
    version_needed: int,
    local_extra: bytes,
    replacement: Path,
) -> tuple[int, int, int]:
    """Write a local header for *name* plus the compressed contents of *replacement*.

    The CRC-32 and both sizes are unknown up front, so they are written as zero and then
    patched in place once the compressed size is known (*out* is a regular, seekable file).

    Returns:
        The (crc32, compressed_size, uncompressed_size) written for this entry.
    """
    start = out.tell()
    header_flags = flags & ~_FLAG_DATA_DESCRIPTOR
    out.write(struct.pack("<4sHHHHHIIIHH", _LOCAL_SIGNATURE, version_needed, header_flags, method, dos_time, dos_date, 0, 0, 0, len(name), len(local_extra)))
    out.write(name)
    out.write(local_extra)

    compressor = _make_compressor(method)
    crc = 0
    compressed_size = 0
    uncompressed_size = 0
    with replacement.open("rb") as source:
        while True:
            chunk = source.read(_CHUNK)
            if not chunk:
                break
            crc = zlib.crc32(chunk, crc)
            uncompressed_size += len(chunk)
            compressed = chunk if compressor is None else compressor.compress(chunk)
            if compressed:
                out.write(compressed)
                compressed_size += len(compressed)
    if compressor is not None:
        tail = compressor.flush()
        if tail:
            out.write(tail)
            compressed_size += len(tail)

    if compressed_size > _U32_MAX or uncompressed_size > _U32_MAX:
        raise ValueError("Replacement too large for ZIP entry")

    end = out.tell()
    out.seek(start + _LOCAL_SIZES_OFFSET)
    out.write(struct.pack("<III", crc, compressed_size, uncompressed_size))
    out.seek(end)
    return crc, compressed_size, uncompressed_size


def _read_local_sizes(out: IO[bytes], local_offset: int) -> tuple[int, int, int]:
    """Read back the (crc32, compressed_size, uncompressed_size) patched into a local header."""
    current = out.tell()
    out.seek(local_offset + _LOCAL_SIZES_OFFSET)
    crc, compressed_size, uncompressed_size = struct.unpack("<III", out.read(12))
    out.seek(current)
    return crc, compressed_size, uncompressed_size


def _patch_unchanged_central_record(entry: _Entry, new_offset: int) -> bytes:
    """Return *entry*'s central record with only its header-offset field patched."""
    record = bytearray(entry.central)
    if entry.offset_in_zip64 is not None:
        struct.pack_into("<Q", record, entry.offset_in_zip64, new_offset)
    else:
        if new_offset > _U32_MAX:
            raise ValueError("Rebuilt ZIP needs Zip64 offsets not present in the original")
        struct.pack_into("<I", record, _CENTRAL_OFFSET_OFFSET, new_offset)
    return bytes(record)


def _require_32bit_offset(new_offset: int) -> None:
    """Raise if *new_offset* cannot be stored in a plain 32-bit central-record offset field.

    Replaced and appended entries never carry a Zip64 extra field for their offset (any such
    field is stripped from a replaced entry's extra, and an appended entry never gets one), so
    there is nowhere to store a 64-bit offset for either.
    """
    if new_offset > _U32_MAX:
        raise ValueError("Rebuilt ZIP entry offset needs Zip64, which is not supported for replaced or appended entries")


def _build_replaced_central_record(out: IO[bytes], entry: _Entry, new_offset: int) -> bytes:
    """Rebuild *entry*'s central record around the freshly written replacement data."""
    _require_32bit_offset(new_offset)
    crc, compressed_size, uncompressed_size = _read_local_sizes(out, new_offset)
    record = bytearray(entry.central)

    flags = struct.unpack_from("<H", record, _CENTRAL_FLAGS_OFFSET)[0]
    struct.pack_into("<H", record, _CENTRAL_FLAGS_OFFSET, flags & ~_FLAG_DATA_DESCRIPTOR)
    struct.pack_into("<I", record, _CENTRAL_CRC_OFFSET, crc)
    struct.pack_into("<I", record, _CENTRAL_CSIZE_OFFSET, compressed_size)
    struct.pack_into("<I", record, _CENTRAL_USIZE_OFFSET, uncompressed_size)

    name_len, extra_len = struct.unpack_from("<HH", record, _CENTRAL_NAME_LEN_OFFSET)
    extra_start = _CENTRAL_FIXED_SIZE + name_len
    extra = bytes(record[extra_start : extra_start + extra_len])
    stripped_extra = _strip_zip64_extra(extra)
    if len(stripped_extra) != len(extra):
        tail = bytes(record[extra_start + extra_len :])
        record = bytearray(record[:extra_start]) + bytearray(stripped_extra) + bytearray(tail)
        struct.pack_into("<H", record, _CENTRAL_EXTRA_LEN_OFFSET, len(stripped_extra))

    struct.pack_into("<I", record, _CENTRAL_OFFSET_OFFSET, new_offset)
    return bytes(record)


def _build_appended_central_record(out: IO[bytes], member: str, new_offset: int) -> bytes:
    """Build a central record for the newly appended *member*, matching its local header."""
    _require_32bit_offset(new_offset)
    crc, compressed_size, uncompressed_size = _read_local_sizes(out, new_offset)
    current = out.tell()
    out.seek(new_offset + _LOCAL_TIME_OFFSET)
    dos_time, dos_date = struct.unpack("<HH", out.read(4))
    out.seek(current)
    name, flags = _encode_member_name(member)
    # Field order: sig, version_made_by, version_needed, flags, method, time, date, crc, csize,
    # usize, name_len, extra_len, comment_len, disk_start, internal_attr, external_attr, offset.
    record = struct.pack(
        "<4sHHHHHHIIIHHHHHII",
        _CENTRAL_SIGNATURE,
        _APPENDED_VERSION_MADE_BY,
        _APPENDED_VERSION_NEEDED,
        flags,
        _DEFLATED,
        dos_time,
        dos_date,
        crc,
        compressed_size,
        uncompressed_size,
        len(name),
        0,
        0,
        0,
        0,
        _APPENDED_EXTERNAL_ATTR,
        new_offset,
    )
    return record + name


def _write_central(
    out: IO[bytes],
    directory: _Directory,
    target: _Entry | None,
    new_offsets: dict[int, int],
    member: str,
) -> None:
    """Write the central directory (in original order) and the end-of-archive records."""
    central_start = out.tell()
    for entry in directory.entries:
        new_offset = new_offsets[id(entry)]
        if entry is target:
            out.write(_build_replaced_central_record(out, entry, new_offset))
        else:
            out.write(_patch_unchanged_central_record(entry, new_offset))
    if target is None:
        out.write(_build_appended_central_record(out, member, new_offsets[0]))

    central_size = out.tell() - central_start
    entry_count = len(directory.entries) + (0 if target is not None else 1)
    _write_end_records(out, directory, entry_count, central_size, central_start)


def _write_end_records(
    out: IO[bytes],
    directory: _Directory,
    entry_count: int,
    central_size: int,
    central_offset: int,
) -> None:
    """Write the (optional) Zip64 end records followed by the classic end-of-archive record."""
    needs_zip64 = directory.zip64 or entry_count >= _U16_MAX or central_size >= _U32_MAX or central_offset >= _U32_MAX

    eocd_entry_count = entry_count
    eocd_central_size = central_size
    eocd_central_offset = central_offset
    if needs_zip64:
        zip64_eocd_offset = out.tell()
        # Field order: sig, size, version_made_by, version_needed, disk, cd_disk, disk_entries,
        # total_entries, cd_size, cd_offset.
        out.write(struct.pack(_ZIP64_EOCD_STRUCT, _ZIP64_EOCD_SIGNATURE, _ZIP64_EOCD_SIZE_FIELD, _ZIP64_VERSION, _ZIP64_VERSION, 0, 0, entry_count, entry_count, central_size, central_offset))
        # Field order: sig, disk, zip64_eocd_offset, total_disks.
        out.write(struct.pack(_ZIP64_LOCATOR_STRUCT, _ZIP64_LOCATOR_SIGNATURE, 0, zip64_eocd_offset, _ZIP64_LOCATOR_TOTAL_DISKS))
        eocd_entry_count = _U16_MAX
        eocd_central_size = _U32_MAX
        eocd_central_offset = _U32_MAX

    # Field order: sig, disk, cd_disk, disk_entries, total_entries, cd_size, cd_offset, comment_len.
    out.write(struct.pack(_EOCD_STRUCT, _EOCD_SIGNATURE, 0, 0, eocd_entry_count, eocd_entry_count, eocd_central_size, eocd_central_offset, len(directory.comment)))
    out.write(directory.comment)
