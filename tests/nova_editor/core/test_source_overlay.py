"""Tests for the read overlay and the patch window of `PreadSource` (ADR-3 rev 2, ACT3 design 5.3)."""

from __future__ import annotations

import os
import threading
from pathlib import Path

import pytest

from nova_editor.core.byte_source import ChangeKind, FileIdentity, OverlayRange, PreadSource, SourceChanged
from nova_editor.core.line_index import LineIndex
from nova_editor.core.memory_source import BytesSource

BLOCK = 8


def make_file(tmp_path: Path, size: int = 100) -> tuple[Path, bytes]:
    data = bytes(33 + i % 90 for i in range(size))
    path = tmp_path / "data.bin"
    path.write_bytes(data)
    return path, data


def overlay_of(old: bytes, a: int, b: int) -> OverlayRange:
    return OverlayRange(a, b, lambda x, y: old[x:y])


def identity_of(path: Path) -> FileIdentity:
    return FileIdentity.from_stat(os.stat(path))


def pwrite(path: Path, offset: int, data: bytes) -> None:
    fd = os.open(path, os.O_WRONLY)
    try:
        assert os.pwrite(fd, data, offset) == len(data)
    finally:
        os.close(fd)


def all_reads(source: PreadSource, model: bytes) -> None:
    """Assert every window of the model, cached and uncached, and through the unverified reader."""
    reader = source.unverified_reader()
    for offset in range(0, len(model) + 1, 3):
        for size in (0, 1, 5, 9, 17, len(model)):
            expected = model[offset : offset + size]
            assert source.read(offset, size) == expected, (offset, size)
            assert source.read(offset, size, cache=False) == expected, (offset, size, "nocache")
            assert reader.read(offset, size) == expected, (offset, size, "unverified")


def test_overlay_masks_before_between_and_after_writes(tmp_path: Path) -> None:
    path, old = make_file(tmp_path)
    source = PreadSource(path, block_size=BLOCK, cache_blocks=4)
    assert source.read(0, len(old)) == old  # warm the cache with the old bytes
    ranges = [overlay_of(old, 5, 21), overlay_of(old, 40, 41), overlay_of(old, 90, 100)]
    source.begin_patch(ranges)
    all_reads(source, old)  # before the first write
    new = bytearray(old)
    for a, b in ((5, 21), (40, 41), (90, 100)):
        pwrite(path, a, b"#" * (b - a))
        new[a:b] = b"#" * (b - a)
        all_reads(source, old)  # between two writes
    assert bytes(new) != old
    source.end_patch(identity_of(path))
    all_reads(source, old)  # after end_patch the overlay stays
    source.close()


def test_overlay_masks_a_cold_cache_and_uncached_reads(tmp_path: Path) -> None:
    path, old = make_file(tmp_path)
    source = PreadSource(path, block_size=BLOCK, cache_blocks=2)
    source.begin_patch([overlay_of(old, 3, 50)])
    pwrite(path, 3, b"#" * 47)  # the file already differs before any read fills the cache
    assert source.read(0, 100) == old
    assert source.read(0, 100, cache=False) == old
    assert source.unverified_reader().read(0, 100) == old
    source.end_patch(identity_of(path))
    assert source.read(2, 3) == old[2:5]
    source.close()


@pytest.mark.parametrize(("offset", "size"), [(0, 10), (10, 30), (20, 4), (25, 40), (44, 2), (49, 1), (50, 5), (0, 100), (30, 70)])
def test_reads_starting_inside_ending_inside_and_spanning_a_range(tmp_path: Path, offset: int, size: int) -> None:
    path, old = make_file(tmp_path)
    source = PreadSource(path, block_size=BLOCK, cache_blocks=4)
    source.begin_patch([overlay_of(old, 20, 45)])
    pwrite(path, 20, b"#" * 25)
    for cache in (True, False):
        assert source.read(offset, size, cache=cache) == old[offset : offset + size]
    assert source.unverified_reader().read(offset, size) == old[offset : offset + size]
    source.end_patch(identity_of(path))
    source.close()


def test_unmasked_bytes_after_a_patch_come_from_the_file(tmp_path: Path) -> None:
    path, old = make_file(tmp_path)
    source = PreadSource(path, block_size=BLOCK, cache_blocks=4)
    source.begin_patch([overlay_of(old, 20, 30)])
    pwrite(path, 20, b"#" * 10)
    pwrite(path, 50, b"#" * 5)  # outside the overlay: the read must see the file
    source.end_patch(identity_of(path))
    assert source.read(0, 100, cache=False) == old[:50] + b"#" * 5 + old[55:]
    source.close()


def test_a_concurrent_reader_never_sees_source_changed(tmp_path: Path) -> None:
    path, old = make_file(tmp_path, 4000)
    source = PreadSource(path, block_size=BLOCK, cache_blocks=4)
    errors: list[BaseException] = []
    stop = threading.Event()
    started = threading.Event()

    def reader() -> None:
        reader_view = source.unverified_reader()
        offset = 0
        try:
            while not stop.is_set():
                got = source.read(offset, 61, cache=bool(offset % 2))
                if got != old[offset : offset + 61]:
                    errors.append(AssertionError(f"mismatch at {offset}"))
                    return
                if reader_view.read(offset, 61) != old[offset : offset + 61]:
                    errors.append(AssertionError(f"unverified mismatch at {offset}"))
                    return
                offset = (offset + 37) % 4000
                started.set()
        except (SourceChanged, ValueError, OSError) as error:
            errors.append(error)

    thread = threading.Thread(target=reader)
    thread.start()
    assert started.wait(5)
    try:
        source.begin_patch([overlay_of(old, 100, 3900)])
        for position in range(100, 3900, 250):
            pwrite(path, position, b"#" * min(250, 3900 - position))
        source.end_patch(identity_of(path))
    finally:
        stop.set()
        thread.join(10)
    assert not thread.is_alive()
    assert errors == []
    assert source.read(0, 4000) == old
    source.close()


def test_after_end_patch_an_external_touch_still_fails_the_source(tmp_path: Path) -> None:
    path, old = make_file(tmp_path)
    source = PreadSource(path, block_size=BLOCK, cache_blocks=4)
    source.begin_patch([overlay_of(old, 5, 10)])
    pwrite(path, 5, b"#####")
    source.end_patch(identity_of(path))
    assert source.read(0, 20) == old[:20]
    stat = os.stat(path)
    os.utime(path, ns=(stat.st_atime_ns, stat.st_mtime_ns + 5_000_000_000))
    with pytest.raises(SourceChanged) as info:
        source.read(0, 20)
    assert info.value.kind is ChangeKind.MODIFIED
    with pytest.raises(SourceChanged):  # sticky
        source.read(0, 1, cache=False)
    source.close()


def test_the_mtime_of_our_own_write_is_tolerated_only_while_patching(tmp_path: Path) -> None:
    path, old = make_file(tmp_path)
    source = PreadSource(path, block_size=BLOCK, cache_blocks=4)
    source.begin_patch([overlay_of(old, 5, 10)])
    stat = os.stat(path)
    os.utime(path, ns=(stat.st_atime_ns, stat.st_mtime_ns + 5_000_000_000))
    assert source.read(0, 20) == old[:20]  # size only while patching
    source.abort_patch()
    with pytest.raises(SourceChanged):
        source.read(0, 20)
    source.close()


def test_growth_during_a_patch_raises_and_fails_the_source(tmp_path: Path) -> None:
    path, old = make_file(tmp_path)
    source = PreadSource(path, block_size=BLOCK, cache_blocks=4)
    source.begin_patch([overlay_of(old, 5, 10)])
    with open(path, "ab") as handle:
        handle.write(b"more")
    with pytest.raises(SourceChanged) as info:
        source.read(0, 20)
    assert info.value.kind is ChangeKind.MODIFIED
    with pytest.raises(SourceChanged):
        source.read(0, 3, cache=False)
    with pytest.raises(SourceChanged):
        source.end_patch(identity_of(path))
    source.close()


def test_truncation_during_a_patch_raises_even_inside_the_overlay(tmp_path: Path) -> None:
    path, old = make_file(tmp_path)
    source = PreadSource(path, block_size=BLOCK, cache_blocks=4)
    source.begin_patch([overlay_of(old, 5, 10)])
    os.truncate(path, 60)
    with pytest.raises(SourceChanged) as info:
        source.read(5, 3)  # fully inside the overlay: still no silent success
    assert info.value.kind is ChangeKind.TRUNCATED
    with pytest.raises(SourceChanged):
        source.read(0, 1, cache=False)
    source.close()


def test_the_unverified_reader_still_reports_a_short_read_inside_the_overlay_window(tmp_path: Path) -> None:
    path, old = make_file(tmp_path)
    source = PreadSource(path, block_size=BLOCK, cache_blocks=4)
    reader = source.unverified_reader()
    source.begin_patch([overlay_of(old, 5, 10)])
    os.truncate(path, 60)
    with pytest.raises(SourceChanged) as info:
        reader.read(0, 100)
    assert info.value.kind is ChangeKind.TRUNCATED
    source.close()


def test_end_patch_with_a_size_mismatch_raises(tmp_path: Path) -> None:
    path, old = make_file(tmp_path)
    source = PreadSource(path, block_size=BLOCK, cache_blocks=4)
    source.begin_patch([overlay_of(old, 5, 10)])
    held = identity_of(path)
    bigger = FileIdentity(held.dev, held.ino, held.size + 1, held.mtime_ns)
    with pytest.raises(SourceChanged):
        source.end_patch(bigger)
    source.close()


def test_end_patch_detects_a_file_that_changed_size_behind_the_identity(tmp_path: Path) -> None:
    path, old = make_file(tmp_path)
    source = PreadSource(path, block_size=BLOCK, cache_blocks=4)
    source.begin_patch([overlay_of(old, 5, 10)])
    held = identity_of(path)
    os.truncate(path, 50)
    with pytest.raises(SourceChanged) as info:
        source.end_patch(held)  # the caller's identity is stale; the descriptor says otherwise
    assert info.value.kind is ChangeKind.TRUNCATED
    source.close()


def test_end_patch_adopts_the_new_identity(tmp_path: Path) -> None:
    path, old = make_file(tmp_path)
    source = PreadSource(path, block_size=BLOCK, cache_blocks=4)
    source.begin_patch([overlay_of(old, 5, 10)])
    pwrite(path, 5, b"#####")
    new = identity_of(path)
    source.end_patch(new)
    assert source.identity() == new
    source.check()
    source.close()


def test_begin_patch_is_additive_and_overlap_is_rejected(tmp_path: Path) -> None:
    path, old = make_file(tmp_path)
    source = PreadSource(path, block_size=BLOCK, cache_blocks=4)
    source.begin_patch([overlay_of(old, 50, 60), overlay_of(old, 10, 20)])
    source.begin_patch([overlay_of(old, 30, 40)])
    pwrite(path, 10, b"#" * 10)
    pwrite(path, 30, b"#" * 10)
    pwrite(path, 50, b"#" * 10)
    assert source.read(0, 100) == old
    for bad in (overlay_of(old, 15, 25), overlay_of(old, 55, 70), overlay_of(old, 0, 11)):
        with pytest.raises(ValueError, match="overlap"):
            source.begin_patch([bad])
    with pytest.raises(ValueError, match="overlap"):
        source.begin_patch([overlay_of(old, 70, 80), overlay_of(old, 75, 85)])
    assert source.read(0, 100) == old  # a rejected call changed nothing
    source.begin_patch([overlay_of(old, 20, 30)])  # touching ranges are not overlapping
    source.end_patch(identity_of(path))
    source.close()


@pytest.mark.parametrize("bad", [(-1, 5), (5, 5), (6, 5), (90, 101)])
def test_begin_patch_rejects_ranges_outside_the_file_or_empty(tmp_path: Path, bad: tuple[int, int]) -> None:
    path, old = make_file(tmp_path)
    source = PreadSource(path, block_size=BLOCK, cache_blocks=4)
    with pytest.raises(ValueError, match="empty or outside"):
        source.begin_patch([overlay_of(old, *bad)])
    source.close()


def test_overlay_keeps_working_when_the_cache_holds_torn_blocks(tmp_path: Path) -> None:
    path, old = make_file(tmp_path)
    source = PreadSource(path, block_size=BLOCK, cache_blocks=64)
    source.begin_patch([overlay_of(old, 10, 30)])
    pwrite(path, 10, b"#" * 7)  # torn: only part of the range written
    assert source.read(0, 40) == old[:40]  # fills the cache with a torn block
    pwrite(path, 17, b"#" * 13)
    assert source.read(0, 40) == old[:40]
    source.end_patch(identity_of(path))
    source.close()


def test_abort_patch_keeps_the_overlay_and_restores_the_full_check(tmp_path: Path) -> None:
    path, old = make_file(tmp_path)
    source = PreadSource(path, block_size=BLOCK, cache_blocks=4)
    source.begin_patch([overlay_of(old, 10, 20)])
    source.abort_patch()
    assert source.read(0, 100) == old
    source.close()


def row_model(data: bytes, stride: int) -> LineIndex:
    index = LineIndex(BytesSource(data), stride=stride, scan_block=16)
    index.scan_now()
    return index


@pytest.mark.parametrize("scan", ["complete", "not_started"])
@pytest.mark.parametrize(("position", "replacement"), [(7, b"x"), (9, b"\n"), (20, b"x\r"), (13, b"\r\n")])
def test_line_index_stays_on_the_old_rows_after_a_break_changing_patch(tmp_path: Path, scan: str, position: int, replacement: bytes) -> None:
    old = b"abcdef\nghijkl\r\nmnopqr\rstuvwx\nyz0123\n" * 3
    path = tmp_path / "rows.txt"
    path.write_bytes(old)
    source = PreadSource(path, block_size=BLOCK, cache_blocks=4)
    index = LineIndex(source, stride=2, scan_block=16, read_budget=1 << 20)
    if scan == "complete":
        index.scan_now()
    source.begin_patch([overlay_of(old, 0, len(old))])
    pwrite(path, position, replacement)
    pwrite(path, 40, b"\n\nx\n")
    source.end_patch(identity_of(path))
    if scan == "not_started":
        index.scan_now()  # the whole scan runs after the patch and must still count the old rows
    model = row_model(old, 2)
    assert index.snapshot().count == model.snapshot().count
    for offset in range(len(old)):
        assert index.row_at_offset(offset) == model.row_at_offset(offset), offset
    for row in range(model.snapshot().count):
        assert index.row_range(row) == model.row_range(row), row
    source.close()
