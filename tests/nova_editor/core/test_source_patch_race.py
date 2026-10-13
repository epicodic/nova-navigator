"""The patch window of `PreadSource` against concurrent verified reads: our own write is never an external change (review finding F1)."""

from __future__ import annotations

import os
import threading
from pathlib import Path

import pytest

from nova_editor.core.byte_source import FileIdentity, OverlayRange, PreadSource, SourceChanged

WAIT = 10.0
OLD_TIME_NS = 1_000_000_000_000_000_000


def make_source(tmp_path: Path) -> tuple[Path, PreadSource, bytes]:
    old = bytes(33 + i % 90 for i in range(100))
    path = tmp_path / "data.bin"
    path.write_bytes(old)
    os.utime(path, ns=(OLD_TIME_NS, OLD_TIME_NS))
    return path, PreadSource(path, block_size=8, cache_blocks=4), old


def overlay_of(old: bytes, a: int, b: int) -> OverlayRange:
    return OverlayRange(a, b, lambda x, y: old[x:y])


def write_with_new_mtime(path: Path, offset: int, data: bytes, iteration: int) -> FileIdentity:
    """Write `data` and give the file an mtime that differs from every earlier one; return the identity afterwards."""
    fd = os.open(path, os.O_WRONLY)
    try:
        assert os.pwrite(fd, data, offset) == len(data)
        stamp = OLD_TIME_NS + (iteration + 1) * 1_000_000_000
        os.utime(fd, ns=(stamp, stamp))
        return FileIdentity.from_stat(os.fstat(fd))
    finally:
        os.close(fd)


def test_a_stat_taken_before_the_whole_patch_is_not_an_external_change(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    path, source, old = make_source(tmp_path)
    reader_id: list[int] = []
    stat_taken = threading.Event()
    patch_done = threading.Event()
    real_fstat = os.fstat
    outcome: list[SourceChanged | None] = []

    def hooked(fd: int) -> os.stat_result:
        info = real_fstat(fd)
        if reader_id and threading.get_ident() == reader_id[0] and not stat_taken.is_set():
            stat_taken.set()  # the reader holds the stat of the unpatched file ...
            assert patch_done.wait(WAIT)  # ... and stalls until the whole patch ended
        return info

    def reader() -> None:
        reader_id.append(threading.get_ident())
        try:
            source.check()
            outcome.append(None)
        except SourceChanged as error:
            outcome.append(error)

    monkeypatch.setattr(os, "fstat", hooked)
    thread = threading.Thread(target=reader)
    thread.start()
    assert stat_taken.wait(WAIT)
    source.begin_patch([overlay_of(old, 5, 6)])
    identity = write_with_new_mtime(path, 5, b"#", 0)
    source.end_patch(identity)
    patch_done.set()
    thread.join(WAIT)
    assert outcome == [None]
    source.check()
    source.close()


def test_a_read_during_end_patch_is_not_an_external_change(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    path, source, old = make_source(tmp_path)
    source.begin_patch([overlay_of(old, 5, 6)])
    identity = write_with_new_mtime(path, 5, b"#", 0)
    real_fstat = os.fstat
    inside = threading.local()
    seen: list[SourceChanged | None] = []

    def hooked(fd: int) -> os.stat_result:
        info = real_fstat(fd)
        if not getattr(inside, "active", False):
            inside.active = True  # a verified read at the moment end_patch checks the descriptor
            try:
                source.check()
                seen.append(None)
            except SourceChanged as error:
                seen.append(error)
            finally:
                inside.active = False
        return info

    monkeypatch.setattr(os, "fstat", hooked)
    source.end_patch(identity)
    monkeypatch.setattr(os, "fstat", real_fstat)
    assert seen
    assert all(item is None for item in seen), seen
    source.check()
    source.close()


def test_hammering_readers_never_see_our_own_write(tmp_path: Path) -> None:
    path, source, old = make_source(tmp_path)
    reader = source.unverified_reader()
    stop = threading.Event()
    errors: list[Exception] = []

    def verified() -> None:
        while not stop.is_set():
            try:
                source.check()
                source.read(0, 100)
                source.read(0, 100, cache=False)
                stop.wait(0.0002)
            except (SourceChanged, ValueError) as error:
                errors.append(error)
                return

    def scanning() -> None:
        while not stop.is_set():
            try:
                assert reader.read(0, 100) == old
                stop.wait(0.0002)
            except (SourceChanged, ValueError, AssertionError) as error:
                errors.append(error)
                return

    threads = [threading.Thread(target=verified) for _ in range(3)] + [threading.Thread(target=scanning)]
    for thread in threads:
        thread.start()
    try:
        for iteration in range(200):
            offset = (iteration * 7) % 90
            source.begin_patch([overlay_of(old, offset, offset + 1)] if iteration < 90 else [])
            identity = write_with_new_mtime(path, offset, b"#", iteration)
            source.end_patch(identity)
            assert not errors, errors
    finally:
        stop.set()
        for thread in threads:
            thread.join(WAIT)
    assert not errors, errors
    source.close()
