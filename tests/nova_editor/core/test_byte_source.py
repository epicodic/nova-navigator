"""Tests for the pread byte source (DEC-9, ADR-3, REQ-14)."""

from __future__ import annotations

import os
import threading
from pathlib import Path

import pytest

from nova_editor.core import byte_source
from nova_editor.core.byte_source import ChangeKind, FileIdentity, PreadSource, SourceChanged

BLOCK = 1024


def make_file(tmp_path: Path, size: int = 10 * BLOCK + 17) -> tuple[Path, bytes]:
    data = bytes((i * 7 + i // 251) % 256 for i in range(size))
    path = tmp_path / "data.bin"
    path.write_bytes(data)
    return path, data


class PreadSpy:
    """Records (offset, size) of every os.pread call."""

    def __init__(self, monkeypatch: pytest.MonkeyPatch) -> None:
        self.calls: list[tuple[int, int]] = []
        real = os.pread

        def spy(fd: int, size: int, offset: int) -> bytes:
            self.calls.append((offset, size))
            return real(fd, size, offset)

        monkeypatch.setattr(byte_source.os, "pread", spy)


def test_reads_match_slices(tmp_path: Path) -> None:
    path, data = make_file(tmp_path)
    source = PreadSource(path, block_size=BLOCK, cache_blocks=4)
    assert source.length() == len(data)
    cases = [(0, 10), (1000, 100), (1023, 2), (1024, 1024), (5000, 20000), (len(data) - 5, 100), (len(data), 1), (len(data) + 10, 1), (0, 0)]
    for offset, size in cases:
        assert source.read(offset, size) == data[offset : offset + size]
        assert source.read(offset, size, cache=False) == data[offset : offset + size]
    source.close()


def test_empty_file(tmp_path: Path) -> None:
    path = tmp_path / "empty"
    path.write_bytes(b"")
    source = PreadSource(path)
    assert source.length() == 0
    assert source.read(0, 10) == b""


def test_negative_arguments_rejected(tmp_path: Path) -> None:
    path, _ = make_file(tmp_path)
    source = PreadSource(path)
    with pytest.raises(ValueError, match="must not be negative"):
        source.read(-1, 5)
    with pytest.raises(ValueError, match="must not be negative"):
        source.read(0, -5)


def test_lru_eviction(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    path, _ = make_file(tmp_path)
    source = PreadSource(path, block_size=BLOCK, cache_blocks=2)
    spy = PreadSpy(monkeypatch)
    source.read(0, 10)  # block 0 loaded
    source.read(BLOCK, 10)  # block 1 loaded
    source.read(0, 10)  # hit, block 0 most recent
    assert len(spy.calls) == 2
    source.read(2 * BLOCK, 10)  # block 2 loaded, evicts block 1
    source.read(0, 10)  # still cached
    assert len(spy.calls) == 3
    source.read(BLOCK, 10)  # block 1 must be loaded again
    assert len(spy.calls) == 4


def test_uncached_reads_do_not_populate_cache(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    path, _ = make_file(tmp_path)
    source = PreadSource(path, block_size=BLOCK, cache_blocks=4)
    spy = PreadSpy(monkeypatch)
    source.read(0, 10, cache=False)
    source.read(0, 10)  # loads block 0 for the first time
    source.read(0, 10)  # hit
    assert len(spy.calls) == 2


def test_read_larger_than_cache_goes_direct(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    path, data = make_file(tmp_path)
    source = PreadSource(path, block_size=BLOCK, cache_blocks=2)
    spy = PreadSpy(monkeypatch)
    assert source.read(3, 5 * BLOCK) == data[3 : 3 + 5 * BLOCK]
    assert spy.calls == [(3, 5 * BLOCK)]


def test_truncation_detected_by_stat(tmp_path: Path) -> None:
    path, _ = make_file(tmp_path)
    source = PreadSource(path, block_size=BLOCK, cache_blocks=4)
    source.read(0, 10)
    os.truncate(path, 100)
    with pytest.raises(SourceChanged):
        source.read(0, 10)  # even a cached block is refused once the file changed


def test_mtime_change_detected(tmp_path: Path) -> None:
    path, _ = make_file(tmp_path)
    source = PreadSource(path)
    stat = path.stat()
    os.utime(path, ns=(stat.st_atime_ns, stat.st_mtime_ns + 1_000_000_000))
    with pytest.raises(SourceChanged):
        source.read(0, 10)


def test_failed_source_stays_failed(tmp_path: Path) -> None:
    path, data = make_file(tmp_path)
    source = PreadSource(path)
    os.truncate(path, 1)
    with pytest.raises(SourceChanged):
        source.read(0, 1)
    path.write_bytes(data)  # size restored, mtime differs anyway
    with pytest.raises(SourceChanged):
        source.read(0, 1)


class NoStatSource(PreadSource):
    """Bypasses the fstat check so that only the short-read rule can catch a truncation."""

    def _check_stat(self) -> None:
        return


def test_short_read_rule_alone_detects_truncation(tmp_path: Path) -> None:
    path, _ = make_file(tmp_path)
    source = NoStatSource(path, block_size=BLOCK, cache_blocks=4)
    os.truncate(path, 100)
    with pytest.raises(SourceChanged):
        source.read(5 * BLOCK, 10)
    assert not source._cache  # short data is never cached
    with pytest.raises(SourceChanged):
        source.read(0, 10)  # failed for good, even for the still-existing prefix


def test_short_pread_result_is_an_error(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    path, _ = make_file(tmp_path)
    source = PreadSource(path, block_size=BLOCK, cache_blocks=4)
    real = os.pread
    monkeypatch.setattr(byte_source.os, "pread", lambda fd, size, offset: real(fd, size, offset)[:-1])
    with pytest.raises(SourceChanged):
        source.read(0, 10)


def test_oserror_becomes_source_changed(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    path, _ = make_file(tmp_path)
    source = PreadSource(path)

    def boom(*_args: int) -> bytes:
        raise OSError(5, "EIO")

    monkeypatch.setattr(byte_source.os, "pread", boom)
    with pytest.raises(SourceChanged) as info:
        source.read(0, 10)
    assert isinstance(info.value.__cause__, OSError)


def test_closed_source_raises_value_error(tmp_path: Path) -> None:
    path, _ = make_file(tmp_path)
    source = PreadSource(path)
    source.close()
    with pytest.raises(ValueError, match="closed source"):
        source.read(0, 1)
    source.close()  # idempotent


def test_concurrent_reads(tmp_path: Path) -> None:
    path, data = make_file(tmp_path, size=64 * BLOCK)
    source = PreadSource(path, block_size=BLOCK, cache_blocks=8)
    errors: list[str] = []

    def work(seed: int) -> None:
        for i in range(400):
            offset = (seed * 7919 + i * 104729) % len(data)
            size = 1 + (i * 37) % (3 * BLOCK)
            if source.read(offset, size, cache=bool(i % 2)) != data[offset : offset + size]:
                errors.append(f"mismatch {seed} {i}")

    threads = [threading.Thread(target=work, args=(n,)) for n in range(8)]
    for thread in threads:
        thread.start()
    for thread in threads:
        thread.join()
    assert not errors


def test_close_waits_for_an_in_flight_read(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    path, data = make_file(tmp_path)
    source = PreadSource(path, block_size=BLOCK, cache_blocks=4)
    entered = threading.Event()
    release = threading.Event()
    real = os.pread

    def slow(fd: int, size: int, offset: int) -> bytes:
        entered.set()
        release.wait()
        return real(fd, size, offset)

    monkeypatch.setattr(byte_source.os, "pread", slow)
    results: list[bytes] = []
    errors: list[BaseException] = []

    def reader() -> None:
        try:
            results.append(source.read(10, 20))
        except (ValueError, SourceChanged, OSError) as error:
            errors.append(error)

    reading = threading.Thread(target=reader)
    closing = threading.Thread(target=source.close)
    try:
        reading.start()
        assert entered.wait(5)
        closing.start()
        closing.join(0.2)
        assert closing.is_alive()  # close blocks while the read is in flight
        with pytest.raises(ValueError, match="closed source"):
            source.read(0, 1)  # a read that starts after close began is refused
    finally:
        release.set()
        reading.join(5)
        closing.join(5)
    assert not closing.is_alive()
    assert errors == []
    assert results == [data[10:30]]
    with pytest.raises(ValueError, match="closed source"):
        source.read(0, 1)


def test_close_survives_raising_os_close(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    path = tmp_path / "f.bin"
    path.write_bytes(b"abc")
    source = PreadSource(path)
    real_close = os.close
    calls: list[int] = []

    def failing_close(fd: int) -> None:
        calls.append(fd)
        real_close(fd)
        msg = "boom"
        raise OSError(msg)

    monkeypatch.setattr(os, "close", failing_close)
    with pytest.raises(OSError, match="boom"):
        source.close()
    # A second close must return at once instead of waiting for `_closed` forever.
    finished = threading.Event()

    def second_close() -> None:
        source.close()
        finished.set()

    thread = threading.Thread(target=second_close, daemon=True)
    thread.start()
    assert finished.wait(2.0), "second close() hung after os.close raised"
    with pytest.raises(ValueError, match="closed"):
        source.read(0, 1)


def test_source_changed_kinds(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    path, _ = make_file(tmp_path)
    source = PreadSource(path, block_size=BLOCK, cache_blocks=4)
    os.truncate(path, 100)
    with pytest.raises(SourceChanged) as truncated:
        source.read(0, 10)
    assert truncated.value.kind is ChangeKind.TRUNCATED
    assert str(truncated.value).startswith("file changed: size ")

    path, _ = make_file(tmp_path)
    source = PreadSource(path)
    stat = path.stat()
    os.utime(path, ns=(stat.st_atime_ns, stat.st_mtime_ns + 1_000_000_000))
    with pytest.raises(SourceChanged) as modified:
        source.read(0, 10)
    assert modified.value.kind is ChangeKind.MODIFIED

    path, _ = make_file(tmp_path)
    source = PreadSource(path)

    def fail(fd: int, size: int, offset: int) -> bytes:
        del fd, size, offset
        raise OSError(5, "Input/output error")

    monkeypatch.setattr(byte_source.os, "pread", fail)
    with pytest.raises(SourceChanged, match="read failed at 0") as unreadable:
        source.read(0, 10)
    assert unreadable.value.kind is ChangeKind.UNREADABLE


def test_short_read_is_truncated(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    path, _ = make_file(tmp_path)
    source = PreadSource(path, block_size=BLOCK, cache_blocks=4)

    def empty(fd: int, size: int, offset: int) -> bytes:
        del fd, size, offset
        return b""

    monkeypatch.setattr(byte_source.os, "pread", empty)
    with pytest.raises(SourceChanged, match="short read at 0") as error:
        source.read(0, 10)
    assert error.value.kind is ChangeKind.TRUNCATED


def test_from_fd_adopts_descriptor(tmp_path: Path) -> None:
    path, data = make_file(tmp_path)
    fd = os.open(path, os.O_RDONLY)
    source = PreadSource.from_fd(fd, block_size=BLOCK)
    assert source.length() == len(data)
    assert source.identity() == FileIdentity.from_stat(os.stat(path))
    assert source.read(5, 20) == data[5:25]
    source.close()
    with pytest.raises(OSError, match="Bad file descriptor"):
        os.fstat(fd)


def test_from_fd_uses_given_identity(tmp_path: Path) -> None:
    path, data = make_file(tmp_path)
    held = FileIdentity.from_stat(os.stat(path))
    fd = os.open(path, os.O_RDONLY)
    source = PreadSource.from_fd(fd, identity=held)
    assert source.identity() is held
    assert source.read(0, 4) == data[:4]
    source.close()


def test_open_by_path_has_identity(tmp_path: Path) -> None:
    path, _ = make_file(tmp_path)
    source = PreadSource(path)
    assert source.identity() == FileIdentity.from_stat(os.stat(path))
    source.close()


def test_unverified_reader_skips_stat_check(tmp_path: Path) -> None:
    path, data = make_file(tmp_path)
    source = PreadSource(path, block_size=BLOCK, cache_blocks=4)
    reader = source.unverified_reader()
    stat = path.stat()
    os.utime(path, ns=(stat.st_atime_ns, stat.st_mtime_ns + 1_000_000_000))
    with open(path, "ab") as handle:
        handle.write(b"tail")
    assert reader.length() == len(data)
    assert reader.read(10, 100) == data[10:110]
    assert reader.read(len(data) - 5, 50) == data[-5:]
    assert reader.read(len(data), 1) == b""
    with pytest.raises(SourceChanged):
        source.read(0, 10)  # the verified path still notices


def test_unverified_reader_fails_only_on_short_read(tmp_path: Path) -> None:
    path, data = make_file(tmp_path)
    source = PreadSource(path, block_size=BLOCK, cache_blocks=4)
    reader = source.unverified_reader()
    os.truncate(path, 5 * BLOCK)
    assert reader.read(0, 100) == data[:100]  # the part that is still there reads fine
    with pytest.raises(SourceChanged, match="short read") as error:
        reader.read(4 * BLOCK, 3 * BLOCK)
    assert error.value.kind is ChangeKind.TRUNCATED
    with pytest.raises(SourceChanged):
        source.read(0, 10)  # the verified path sees the size change, the reader never recorded a failure


def test_unverified_reader_does_not_populate_cache(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    path, _ = make_file(tmp_path)
    source = PreadSource(path, block_size=BLOCK, cache_blocks=4)
    reader = source.unverified_reader()
    reader.read(0, 10)
    reader.read(0, 10, cache=True)
    spy = PreadSpy(monkeypatch)
    source.read(0, 10)  # loads block 0 now: it was not cached by the reader
    source.read(0, 10)
    assert spy.calls == [(0, BLOCK)]


def test_close_waits_for_unverified_reader(tmp_path: Path) -> None:
    path, _ = make_file(tmp_path)
    source = PreadSource(path)
    reader = source.unverified_reader()
    reader.close()  # a no-op: the source still works
    assert source.read(0, 4)
    source.close()
    with pytest.raises(ValueError, match="closed source"):
        reader.read(0, 4)
