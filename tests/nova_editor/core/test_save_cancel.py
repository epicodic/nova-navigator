"""`SaveJob` cancellation: before the commit point nothing changes, after it the result is returned."""

from __future__ import annotations

from pathlib import Path

import pytest

from nova_editor.core.save import SaveCancelled, SaveJob, SaveProgress

from .save_helpers import CHUNK, ORIGINAL, make_job

CHUNKS = -(-len(ORIGINAL) // CHUNK)


@pytest.mark.parametrize("k", range(1, CHUNKS))
def test_cancel_at_report_k_leaves_the_original(tmp_path: Path, k: int) -> None:
    target = tmp_path / "f.txt"
    target.write_bytes(ORIGINAL)
    holder: list[SaveJob] = []
    reports = 0

    def progress(report: SaveProgress) -> None:
        nonlocal reports
        if report.phase != "writing":
            return
        reports += 1
        if reports == k:
            holder[0].cancel()

    job, source = make_job(target, progress=progress)
    holder.append(job)
    with pytest.raises(SaveCancelled):
        job.run()
    source.close()
    assert target.read_bytes() == ORIGINAL
    assert [p.name for p in tmp_path.iterdir()] == ["f.txt"]


def test_cancel_after_the_replace_is_ignored(tmp_path: Path) -> None:
    target = tmp_path / "f.txt"
    target.write_bytes(ORIGINAL)
    holder: list[SaveJob] = []

    def progress(report: SaveProgress) -> None:
        if report.phase == "finishing":
            holder[0].cancel()

    job, source = make_job(target, progress=progress)
    holder.append(job)
    result = job.run()
    assert result.length == len(ORIGINAL)
    assert target.read_bytes() == ORIGINAL
    result.source.close()
    source.close()
