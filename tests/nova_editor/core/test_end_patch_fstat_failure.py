"""An `os.fstat` failure inside `PreadSource.end_patch` closes the patch window and fails the source for good (source level and job level)."""

from __future__ import annotations

import errno
import os
from pathlib import Path

import pytest

from nova_editor.core import ChangeKind, FileIdentity, PreadSource, SourceChanged
from nova_editor.core.save import SaveFailed, SaveIo, SaveSettings
from nova_editor.document import LazyDocument
from tests.nova_editor.core.test_inplace_job import DATA, RecordingHost, open_doc, overwrite
from tests.nova_editor.core.test_source_overlay import identity_of, make_file, overlay_of
from tests.nova_editor.document.helpers_save import run_in_place

CHUNKY = SaveSettings(chunk=4, fsync_every=64)


def fail_fstat_of(monkeypatch: pytest.MonkeyPatch, armed: list[bool]) -> None:
    """Make `os.fstat` raise `EIO` while `armed[0]` is set."""
    real = os.fstat

    def fstat(fd: int) -> os.stat_result:
        if armed[0]:
            raise OSError(errno.EIO, "injected fstat failure")
        return real(fd)

    monkeypatch.setattr(os, "fstat", fstat)


def test_end_patch_with_a_failing_fstat_closes_the_window_and_fails_the_source(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    path, old = make_file(tmp_path)
    source = PreadSource(path, block_size=8, cache_blocks=4)
    source.begin_patch([overlay_of(old, 5, 21)])
    armed = [True]
    fail_fstat_of(monkeypatch, armed)
    with pytest.raises(SourceChanged) as raised:
        source.end_patch(identity_of(path))
    armed[0] = False
    assert raised.value.kind is ChangeKind.UNREADABLE
    assert source._patching is False
    with pytest.raises(SourceChanged):  # sticky
        source.read(0, 10)
    source.close()


def test_a_closed_window_after_a_failed_end_patch_detects_a_touch(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    path, old = make_file(tmp_path)
    source = PreadSource(path, block_size=8, cache_blocks=4)
    source.begin_patch([overlay_of(old, 5, 21)])
    armed = [True]
    fail_fstat_of(monkeypatch, armed)
    with pytest.raises(SourceChanged):
        source.end_patch(identity_of(path))
    armed[0] = False
    os.utime(path, ns=(1_500_000_000_000_000_000, 1_500_000_000_000_000_000))  # mtime only, same size
    with pytest.raises(SourceChanged):
        source.read(0, 10)
    source.close()


class FailingEndHost(RecordingHost):
    """Arms the `os.fstat` failure for the duration of `end_patch`."""

    def __init__(self, doc: LazyDocument, events: list[str], armed: list[bool]) -> None:
        super().__init__(doc, events)
        self.armed = armed

    def end_patch(self, identity: FileIdentity | None) -> None:
        self.armed[0] = True
        try:
            super().end_patch(identity)
        finally:
            self.armed[0] = False


def test_a_job_whose_end_patch_cannot_fstat_reports_a_partial_failure(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    doc, path = open_doc(tmp_path)
    try:
        overwrite(doc, 10, b"#")
        armed = [False]
        fail_fstat_of(monkeypatch, armed)
        with pytest.raises(SaveFailed) as raised:
            run_in_place(doc, path, host=FailingEndHost(doc, [], armed))
        assert raised.value.partial is True
        assert raised.value.change is ChangeKind.UNREADABLE
        assert isinstance(doc._source, PreadSource)
        assert doc._source._patching is False
        os.utime(path, ns=(1_500_000_000_000_000_000, 1_500_000_000_000_000_000))
        with pytest.raises(SourceChanged):
            doc.read_bytes(0, doc.length)
    finally:
        doc.close()


def test_a_partial_failure_with_a_failing_fstat_fails_the_source_instead_of_adopting_the_stale_identity(tmp_path: Path) -> None:
    doc, path = open_doc(tmp_path)
    try:
        overwrite(doc, 10, b"ABCDEFGH")  # two chunks
        io = SaveIo()
        real_pwrite = io.pwrite
        writes = 0
        broken = False

        def pwrite(fd: int, data: bytes | memoryview, offset: int) -> int:
            nonlocal writes, broken
            writes += 1
            if writes == 2:
                broken = True
                raise OSError(errno.EIO, "injected write failure")
            return real_pwrite(fd, data, offset)

        def fstat(fd: int) -> os.stat_result:
            if broken:
                raise OSError(errno.EIO, "injected fstat failure")
            return os.fstat(fd)

        io.pwrite = pwrite
        io.fstat = fstat
        with pytest.raises(SaveFailed) as raised:
            run_in_place(doc, path, io=io, settings=CHUNKY)
        assert raised.value.partial is True
        assert raised.value.identity is None  # unknown, not the stale held identity
        assert isinstance(doc._source, PreadSource)
        assert doc._source._failed is not None
        assert doc._source._patching is False
        with pytest.raises(SourceChanged):
            doc.read_bytes(0, doc.length)
    finally:
        doc.close()
    assert path.read_bytes()[:10] == DATA[:10]
