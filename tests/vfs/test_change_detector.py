"""Tests for ChangeDetector save-pattern detection."""

import asyncio
import os
from pathlib import Path

import pytest

from nova_navigator.vfs.change_detector import ChangeDetector, file_digest

_FAST_POLL_INTERVAL = 0.05
_FAST_SETTLE_TIME = 0.15


async def _detector(
    path: Path,
    *,
    poll_interval: float = _FAST_POLL_INTERVAL,
    settle_time: float = _FAST_SETTLE_TIME,
    use_watcher: bool = True,
) -> tuple[ChangeDetector, asyncio.Queue[str]]:
    queue: asyncio.Queue[str] = asyncio.Queue()

    async def on_change(digest: str) -> None:
        await queue.put(digest)

    detector = ChangeDetector(
        path,
        on_change,
        baseline_digest=file_digest(path),
        poll_interval=poll_interval,
        settle_time=settle_time,
        use_watcher=use_watcher,
    )
    await detector.start()
    return detector, queue


@pytest.fixture
def target(tmp_path: Path) -> Path:
    path = tmp_path / "doc.txt"
    path.write_bytes(b"original\n")
    return path


@pytest.mark.asyncio
async def test_in_place_write_is_reported(target: Path) -> None:
    detector, queue = await _detector(target)
    try:
        target.write_bytes(b"changed\n")
        digest = await asyncio.wait_for(queue.get(), 3)
        assert digest == file_digest(target)
    finally:
        await detector.stop()


@pytest.mark.asyncio
async def test_rename_over_save_is_reported_repeatedly(target: Path) -> None:
    detector, queue = await _detector(target)
    try:
        for text in (b"one\n", b"two\n"):
            tmp = target.with_name(".doc.txt.swp")
            tmp.write_bytes(text)
            os.replace(tmp, target)
            assert await asyncio.wait_for(queue.get(), 3) == file_digest(target)
    finally:
        await detector.stop()


@pytest.mark.asyncio
async def test_delete_then_recreate_is_reported(target: Path) -> None:
    detector, queue = await _detector(target)
    try:
        target.unlink()
        await asyncio.sleep(0.05)
        target.write_bytes(b"recreated\n")
        assert await asyncio.wait_for(queue.get(), 3) == file_digest(target)
    finally:
        await detector.stop()


@pytest.mark.asyncio
async def test_truncate_then_delayed_write_reports_final_content_once(target: Path) -> None:
    detector, queue = await _detector(target, settle_time=0.3)
    try:
        target.write_bytes(b"")
        await asyncio.sleep(0.1)
        target.write_bytes(b"final\n")
        assert await asyncio.wait_for(queue.get(), 3) == file_digest(target)
        await asyncio.sleep(0.5)
        assert queue.empty()
    finally:
        await detector.stop()


@pytest.mark.asyncio
async def test_touch_without_content_change_is_not_reported(target: Path) -> None:
    detector, queue = await _detector(target)
    try:
        os.utime(target)
        await asyncio.sleep(0.5)
        assert queue.empty()
    finally:
        await detector.stop()


@pytest.mark.asyncio
async def test_other_files_in_directory_are_ignored(target: Path) -> None:
    detector, queue = await _detector(target)
    try:
        (target.parent / "other.txt").write_bytes(b"noise")
        await asyncio.sleep(0.5)
        assert queue.empty()
    finally:
        await detector.stop()


@pytest.mark.asyncio
async def test_poll_detects_change_without_watcher(target: Path) -> None:
    detector, queue = await _detector(target, use_watcher=False)
    try:
        target.write_bytes(b"polled\n")
        assert await asyncio.wait_for(queue.get(), 3) == file_digest(target)
    finally:
        await detector.stop()


@pytest.mark.asyncio
async def test_check_now_reports_pending_change(target: Path) -> None:
    detector, queue = await _detector(target, poll_interval=60, use_watcher=False)
    try:
        target.write_bytes(b"after exit\n")
        await detector.check_now()
        assert await asyncio.wait_for(queue.get(), 3) == file_digest(target)
    finally:
        await detector.stop()


@pytest.mark.asyncio
async def test_set_baseline_suppresses_known_digest(target: Path) -> None:
    detector, queue = await _detector(target)
    try:
        detector.set_baseline(file_digest(target))
        target.write_bytes(target.read_bytes())
        await asyncio.sleep(0.5)
        assert queue.empty()
    finally:
        await detector.stop()
