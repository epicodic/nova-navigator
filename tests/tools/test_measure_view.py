"""Smoke tests of the view benchmark harness on a small synthetic file (never a file above 1 MiB)."""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any

import pytest

from tests.nova_editor.helpers_view import make_mixed
from tools._view_app import LOWERED
from tools._view_procmem import median, percentile
from tools.measure_view import main

MAX_TEST_FILE_BYTES = 1 << 20


@pytest.fixture(scope="module")
def mixed_file(tmp_path_factory: pytest.TempPathFactory) -> Path:
    path = make_mixed(tmp_path_factory.mktemp("view") / "m.txt", long_chars=5000)
    assert path.stat().st_size <= MAX_TEST_FILE_BYTES
    return path


def _rows(path: Path) -> list[dict[str, Any]]:
    return [json.loads(line) for line in path.read_text().splitlines()]


def test_smoke_first_screen_and_latency(mixed_file: Path, tmp_path: Path) -> None:
    out = tmp_path / "out.jsonl"
    assert main(["first-screen", "--file", str(mixed_file), "--runs", "1", "--out", str(out)]) == 0
    assert main(["latency", "--file", str(mixed_file), "--wrap", "off", "--state", "indexed", "--op", "down", "--steps", "5", "--out", str(out)]) == 0
    rows = _rows(out)
    assert {"first_screen_ms", "latency_ms"} <= {key for row in rows for key in row}
    for row in rows:
        assert {"case", "file", "wrap", "state", "op", "run"} <= set(row)
    first = next(row for row in rows if row["case"] == "first-screen")
    assert first["first_screen_ms"] > 0
    assert first["status"] == "ok"


def test_latency_records_direct_and_pilot_columns_and_the_idle_floor(mixed_file: Path, tmp_path: Path) -> None:
    out = tmp_path / "out.jsonl"
    assert main(["latency", "--file", str(mixed_file), "--wrap", "on", "--state", "indexing", "--op", "down", "--steps", "5", "--out", str(out)]) == 0
    rows = _rows(out)
    steps = [row for row in rows if row["op"] == "down"]
    direct = [row for row in steps if row["phase"] == "direct"]
    pilot = [row for row in steps if row["phase"] == "pilot"]
    assert len(direct) == 5
    assert len(pilot) == 5
    assert all(row["latency_ms"] is not None and isinstance(row["scan_running"], bool) and "scan_done_at_step" in row for row in direct)
    assert all(isinstance(row["scan_completed_during_step"], bool) and row["busy_at_step"] == row["scan_running"] for row in direct)
    assert all(not row["scan_completed_during_step"] or row["scan_running"] for row in direct)
    assert all(row["pilot_ms"] is not None for row in pilot)
    phases = [row["phase"] for row in rows]
    assert phases == ["direct"] * 5 + ["pilot"] * (len(phases) - 5)  # the direct burst comes first, every Pilot row after it
    assert any(row["op"] == "f24" and row["pilot_ms"] > 0 for row in rows)


def test_latency_profile_writes_the_sibling_file(mixed_file: Path, tmp_path: Path) -> None:
    out = tmp_path / "out.jsonl"
    assert main(["latency", "--file", str(mixed_file), "--state", "indexed", "--op", "right", "--steps", "3", "--profile", "--out", str(out)]) == 0
    text = Path(f"{out}.profile.txt").read_text()
    assert "cumulative" in text
    assert "step=2" in text


def test_latency_hscroll_and_farjump(mixed_file: Path, tmp_path: Path) -> None:
    out = tmp_path / "out.jsonl"
    for op in ("hscroll", "farjump"):
        assert main(["latency", "--file", str(mixed_file), "--state", "indexed", "--op", op, "--steps", "3", "--out", str(out)]) == 0
    assert {row["op"] for row in _rows(out)} >= {"hscroll", "farjump"}


def test_memory_reports_rss_anon_first(mixed_file: Path, tmp_path: Path) -> None:
    out = tmp_path / "out.jsonl"
    assert main(["memory", "--file", str(mixed_file), "--wrap", "off", "--runs", "1", "--out", str(out)]) == 0
    session = next(row for row in _rows(out) if row["state"] == "session")
    assert session["rss_anon_kb_max"] > 0
    assert session["rss_anon_mib_max"] > 0
    assert session["vm_rss_kb_max"] >= session["rss_anon_kb_max"]
    assert "rss_file_kb_max" in session
    assert session["phases"] == ["open", "indexing", "pagedown", "end", "pageup"]


def test_jump_records_state_transitions(mixed_file: Path, tmp_path: Path) -> None:
    out = tmp_path / "out.jsonl"
    assert main(["jump", "--file", str(mixed_file), "--wrap", "on", "--column", "1000", "--runs", "1", "--out", str(out)]) == 0
    rows = _rows(out)
    assert {(row["state"], row["op"]) for row in rows} == {("before_scan", "end"), ("before_scan", "farjump"), ("after_scan", "end"), ("after_scan", "farjump")}
    assert all(row["repaint_ms"] is not None and row["transitions"] for row in rows)


def test_oracle_finds_no_mismatch(mixed_file: Path, tmp_path: Path) -> None:
    out = tmp_path / "out.jsonl"
    assert main(["oracle", "--file", str(mixed_file), "--column", "1000", "--out", str(out)]) == 0
    row = _rows(out)[0]
    assert row["mismatches"] == 0
    assert row["checks"] > 50


def test_calllog_passes_on_the_synthetic_file(mixed_file: Path, tmp_path: Path) -> None:
    out = tmp_path / "out.jsonl"
    assert main(["calllog", "--file", str(mixed_file), "--out", str(out)]) == 0
    row = _rows(out)[0]
    assert row["ok"] is True
    assert row["max_decoded_chars"] <= 8192
    assert row["calls"] > 0


def test_calllog_fails_when_a_call_decodes_too_much(tmp_path: Path) -> None:
    path = make_mixed(tmp_path / "wide.txt", long_chars=20_000)
    out = tmp_path / "out.jsonl"
    assert main(["calllog", "--file", str(path), "--config", "default", "--out", str(out)]) == 1  # the default thresholds decode a 20,000 character row whole
    assert _rows(out)[0]["max_decoded_chars"] > 8192


def test_sweep_yield_records_scan_completion(mixed_file: Path, tmp_path: Path) -> None:
    out = tmp_path / "out.jsonl"
    assert main(["sweep-yield", "--file", str(mixed_file), "--values", "0.001", "--steps", "3", "--runs", "1", "--out", str(out)]) == 0
    rows = _rows(out)
    assert {row["op"] for row in rows} == {"pagedown", "pageup", "scan"}
    assert all(row["phase"] == "direct" for row in rows if row["op"] != "scan")
    scan = next(row for row in rows if row["op"] == "scan")
    assert scan["scan_complete_s"] > 0
    assert scan["yield_seconds"] == 0.001
    assert scan["scan_block"] == 1 << 20


def test_thresholds_highlight_and_wordwrap_on_tiny_sizes(tmp_path: Path) -> None:
    out = tmp_path / "out.jsonl"
    assert main(["thresholds", "--kind", "highlight", "--sizes", "4096", "--out", str(out)]) == 0
    assert main(["thresholds", "--kind", "wordwrap", "--row-bytes", "1024,2048", "--width", "113", "--out", str(out)]) == 0
    rows = _rows(out)
    assert any(row["op"] == "first_screen" and row["first_screen_ms"] is not None for row in rows)
    assert any(row["op"] == "down" and row["latency_ms"] is not None for row in rows)
    assert sorted(row["size"] for row in rows if row["op"] == "wrap") == [1024, 2048]
    assert all(row["wrap_ms"] > 0 for row in rows if row["op"] == "wrap")


def test_thresholds_longrow_and_eager_on_tiny_sizes(tmp_path: Path) -> None:
    out = tmp_path / "out.jsonl"
    assert main(["thresholds", "--kind", "longrow", "--row-bytes", "4096", "--out", str(out)]) == 0
    assert main(["thresholds", "--kind", "eager", "--sizes", "4096", "--out", str(out)]) == 0
    rows = _rows(out)
    assert {row["op"] for row in rows if row["case"] == "threshold-longrow"} == {"whole", "window"}
    assert {row["op"] for row in rows if row["case"] == "threshold-eager"} == {"stock", "lazy"}


def test_percentile_is_nearest_rank() -> None:
    assert percentile([10, 20, 30, 60], 0.95) == 60
    assert percentile([10, 20, 30, 60], 0.5) == 20
    assert median([5, 1, 3]) == 3
    assert percentile(list(range(1, 101)), 0.95) == 95
    assert percentile([7], 0.95) == 7


def test_summarise_prints_percentiles(tmp_path: Path, capsys: pytest.CaptureFixture[str]) -> None:
    out = tmp_path / "out.jsonl"
    out.write_text("\n".join(json.dumps({"case": "c", "latency_ms": v}) for v in (10, 20, 30, 60)))
    assert main(["summarise", str(out)]) == 0
    text = capsys.readouterr().out
    assert "median" in text
    assert "p95" in text
    assert "over 50" in text
    assert "| 4 | 20.00 | 60.00 | 60.00 | 1 |" in text
    assert "[out.jsonl]" in text


def test_summarise_groups_by_case_file_wrap_state_and_op(tmp_path: Path, capsys: pytest.CaptureFixture[str]) -> None:
    out = tmp_path / "rows.jsonl"
    rows = [
        {"case": "latency", "file": "a", "wrap": "off", "state": "indexed", "op": "down", "latency_ms": 5, "pilot_ms": 80},
        {"case": "latency", "file": "a", "wrap": "on", "state": "indexed", "op": "down", "latency_ms": 7},
        {"case": "memory", "file": "a", "wrap": "off", "state": "session", "op": "session", "rss_anon_mib_max": 100.5},
    ]
    out.write_text("\n".join(json.dumps(row) for row in rows))
    assert main(["summarise", str(out)]) == 0
    text = capsys.readouterr().out
    assert "### latency" in text
    assert "### memory" in text
    assert "| a | off | indexed | down | - | latency_ms | 1 | 5.00 |" in text
    assert "| a | on | indexed | down | - | latency_ms | 1 | 7.00 |" in text
    assert "pilot_ms" in text
    assert "rss_anon_mib_max" in text


def test_summarise_counts_direct_steps_issued_during_a_scan(tmp_path: Path, capsys: pytest.CaptureFixture[str]) -> None:
    out = tmp_path / "rows.jsonl"
    base = {"case": "latency", "file": "a", "wrap": "off", "state": "indexing", "op": "pagedown"}
    rows = [
        {**base, "phase": "direct", "latency_ms": 5, "scan_running": True, "scan_completed_during_step": False},
        {**base, "phase": "direct", "latency_ms": 6, "scan_running": True, "scan_completed_during_step": True},
        {**base, "phase": "direct", "latency_ms": 7, "scan_running": False},
        {**base, "phase": "pilot", "pilot_ms": 3000},
    ]
    out.write_text("\n".join(json.dumps(row) for row in rows))
    assert main(["summarise", str(out)]) == 0
    text = capsys.readouterr().out
    assert "n scan_running" in text
    assert "n completing" in text
    assert "p95 is the nearest-rank value of the n samples" in text
    assert "| a | off | indexing | pagedown | - | latency_ms | 3 | 6.00 | 7.00 | 7.00 | 0 | 2 | 1 |" in text
    assert "| a | off | indexing | pagedown | - | pilot_ms | 1 | 3000.00 | 3000.00 | 3000.00 | 1 | - | - |" in text


def test_summarise_reports_failed_and_unverified_first_screen_runs(tmp_path: Path, capsys: pytest.CaptureFixture[str]) -> None:
    out = tmp_path / "rows.jsonl"
    base = {"case": "first-screen", "file": "a", "wrap": "off", "state": "cold", "op": "first_screen"}
    rows = [
        {**base, "first_screen_ms": 40.0, "cold_verified": True},
        {**base, "first_screen_ms": 50.0, "cold_verified": False},
        {**base, "first_screen_ms": None, "cold_verified": True, "status": "timeout"},
    ]
    out.write_text("\n".join(json.dumps(row) for row in rows))
    assert main(["summarise", str(out)]) == 0
    text = capsys.readouterr().out
    assert "first-screen runs: 3, failed: 1, cold_verified=false: 1" in text


def test_probing_the_view_creates_no_index(mixed_file: Path) -> None:
    from tools._view_app import _view_state, lazy_document, open_probe, scan_busy

    area = open_probe(mixed_file, wrap=False, config=LOWERED)
    document = lazy_document(area)
    assert document is not None
    document.wait_indexed(10.0)
    row = next(r for r in range(document.line_count) if document.is_long(r))
    area.move_cursor((row, 3), record_width=False)
    before = list(document._long)
    scan_busy(area)
    _view_state(area)
    assert list(document._long) == before
    area.close()


def test_first_screen_closes_the_pty_when_the_window_size_cannot_be_set(mixed_file: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    import os

    from tools import measure_view

    def fail(*_args: object) -> None:
        raise OSError("ioctl")

    before = len(os.listdir("/proc/self/fd"))
    monkeypatch.setattr(measure_view.fcntl, "ioctl", fail)
    with pytest.raises(OSError, match="ioctl"):
        measure_view.first_screen_once(mixed_file, cold=False, timeout=1.0)
    assert len(os.listdir("/proc/self/fd")) == before
