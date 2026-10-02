"""Smoke tests of the view benchmark harness on a small synthetic file (never a file above 1 MiB)."""

from __future__ import annotations

import gc
import hashlib
import json
from pathlib import Path
from typing import Any

import pytest
from hypothesis import HealthCheck, given, settings
from hypothesis import strategies as st

from tests.nova_editor.helpers_view import make_mixed
from tools._view_app import LOWERED
from tools._view_edit_latency import GcTimer
from tools._view_procmem import median, percentile
from tools._view_save import ByteModel, literal, verify_file
from tools.measure_view import main

MAX_TEST_FILE_BYTES = 1 << 20
MODEL_EXAMPLES = 80


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
    assert percentile(list(range(1, 20)), 0.95) == 19
    assert percentile(list(range(1, 21)), 0.95) == 19  # n = 20: the second largest, not the max


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
    assert "with n of at most 19 it equals the max" in text
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


# -- ACT4 edit subcommands -----------------------------------------------------------------------------------------------------------
@pytest.fixture(scope="module")
def long_file(tmp_path_factory: pytest.TempPathFactory) -> Path:
    path = tmp_path_factory.mktemp("view_long") / "long.txt"
    path.write_bytes(("abc\t日é😀 " * 600).encode())
    assert path.stat().st_size <= MAX_TEST_FILE_BYTES
    return path


def _verify_rows(rows: list[dict[str, Any]]) -> list[dict[str, Any]]:
    return [row for row in rows if row["case"] == "verify"]


@pytest.mark.parametrize(("name", "wrap"), [("mixed_file", "off"), ("long_file", "on")])
def test_edit_memory_script_matches_the_file_after_every_step(name: str, wrap: str, request: pytest.FixtureRequest, tmp_path: Path) -> None:
    path: Path = request.getfixturevalue(name)
    out = tmp_path / "out.jsonl"
    assert main(["edit-memory", "--file", str(path), "--wrap", wrap, "--runs", "1", "--out", str(out)]) == 0
    rows = _rows(out)
    steps = ["select", "delete", "undo", "redo", "select_copy", "copy", "goto_paste", "paste", "undo_paste"]
    done = [row for row in rows if row["case"] == "edit-memory" and row["op"] == row["state"] != "session"]
    assert [row["state"] for row in done] == steps
    assert [row["state"] for row in _verify_rows(rows)] == ["indexing", *steps]
    assert all(row["mismatches"] == 0 and row["windows"] > 0 for row in _verify_rows(rows))
    lengths = {row["state"]: row["doc_length"] for row in done}
    assert lengths["delete"] < lengths["undo"] == path.stat().st_size
    assert lengths["redo"] == lengths["delete"]
    assert lengths["paste"] > lengths["redo"] == lengths["undo_paste"]
    phases = [row["state"] for row in rows if row["op"] == "phase"]
    assert phases == ["open", "indexing", *steps]
    session = next(row for row in rows if row["state"] == "session")
    assert session["rss_anon_kb_max"] > 0
    assert session["phases"] == phases
    samples = _rows(Path(f"{out}.samples.jsonl"))
    assert samples
    assert {sample["phase"] for sample in samples} <= set(phases)


def test_verify_subcommand_reports_zero_mismatches(long_file: Path, tmp_path: Path, capsys: pytest.CaptureFixture[str]) -> None:
    out = tmp_path / "out.jsonl"
    assert main(["verify", "--file", str(long_file), "--wrap", "off", "--variant", "longline", "--select-bytes", "1000", "--out", str(out)]) == 0
    assert "mismatches=0 (expected 0)" in capsys.readouterr().out
    rows = _rows(out)
    assert len(rows) == 10
    assert {row["case"] for row in rows} == {"verify"}
    assert next(row for row in rows if row["state"] == "delete")["doc_length"] in range(long_file.stat().st_size - 1010, long_file.stat().st_size - 989)


def test_verify_detects_a_document_that_differs_from_the_model(tmp_path: Path) -> None:
    from nova_editor.document._lazy_document import LazyDocument
    from tools._view_edit import FileModel, verify_row

    path = tmp_path / "a.txt"
    path.write_bytes(b"alpha\nbeta\ngamma\n" * 400)
    document = LazyDocument.from_path(path)
    model = FileModel(path)
    try:
        spec = {"file": str(path), "wrap": "off", "run": 1}
        assert verify_row(spec, "same", document, model, [100])["mismatches"] == 0
        model.delete(10, 7)
        bad = verify_row(spec, "different", document, model, [100])
        assert bad["mismatches"] > 0
        assert bad["doc_length"] == bad["model_length"] + 7
    finally:
        model.close()
        document.close()


def test_file_model_follows_the_offset_arithmetic(tmp_path: Path) -> None:
    from tools._view_edit import FileModel

    data = bytes(range(256)) * 8
    path = tmp_path / "b.bin"
    path.write_bytes(data)
    model = FileModel(path)
    try:
        removed = model.delete(100, 300)
        expected = data[:100] + data[400:]
        assert model.length == len(expected)
        assert model.read(0, model.length) == expected
        model.insert(100, removed)
        assert model.read(0, model.length) == data
        model.insert(50, model.slice(1000, 64))
        grown = data[:50] + data[1000:1064] + data[50:]
        assert model.read(0, model.length) == grown
        assert model.read(model.length - 10, 100) == grown[-10:]
    finally:
        model.close()


def test_edit_latency_in_both_wrap_modes_and_both_states(mixed_file: Path, tmp_path: Path) -> None:
    out = tmp_path / "out.jsonl"
    common = ["--file", str(mixed_file), "--wrap", "both", "--ops", "type,backspace,delete,enter", "--steps", "3", "--runs", "1", "--out", str(out)]
    assert main(["edit-latency", *common, "--state", "indexed"]) == 0
    assert main(["edit-latency", *common, "--state", "indexing"]) == 0
    rows = _rows(out)
    assert {(row["wrap"], row["state"], row["op"]) for row in rows} == {
        (wrap, state, op) for wrap in ("off", "on") for state in ("indexed", "indexing") for op in ("type", "backspace", "delete", "enter")
    }
    assert all(row["phase"] == "direct" and row["case"] == "edit-latency" for row in rows)
    assert all(row["changed"] and row["latency_ms"] is not None for row in rows)
    deltas = {op: {row["doc_delta"] for row in rows if row["op"] == op} for op in ("type", "backspace", "delete", "enter")}
    assert deltas == {"type": {1}, "backspace": {-1}, "delete": {-1}, "enter": {1}}
    assert {row["place"] for row in rows if row["state"] == "indexed"} == {"end"}
    assert {row["place"] for row in rows if row["state"] == "indexing"} == {"start"}


def test_edit_latency_at_the_far_column_of_a_long_row(long_file: Path, tmp_path: Path) -> None:
    out = tmp_path / "out.jsonl"
    assert main(["edit-latency", "--file", str(long_file), "--wrap", "on", "--ops", "type,delete", "--steps", "3", "--runs", "1", "--out", str(out)]) == 0
    rows = _rows(out)
    assert {row["place"] for row in rows} == {"far"}
    assert all(row["changed"] for row in rows)


def test_edit_scatter_builds_the_pieces_before_the_steps(long_file: Path, tmp_path: Path) -> None:
    out = tmp_path / "out.jsonl"
    assert main(["edit-scatter", "--file", str(long_file), "--wrap", "off", "--scatter", "25", "--ops", "type,right", "--steps", "2", "--runs", "1", "--out", str(out)]) == 0
    rows = _rows(out)
    build = [row for row in rows if row["op"] == "scatter"]
    assert len(build) == 25
    assert all(row["action_ms"] > 0 for row in build)
    assert [row["pieces"] for row in build] == sorted(row["pieces"] for row in build)
    steps = [row for row in rows if row["op"] != "scatter"]
    assert {row["variant"] for row in steps} == {"scatter-25"}
    assert min(row["pieces"] for row in steps) >= build[-1]["pieces"]
    assert all(row["rss_anon_kb"] > 0 for row in rows)
    assert all(row["gc_ms"] >= 0 and row["gc_longest_ms"] >= 0 and row["gc_longest_ms"] <= row["gc_ms"] for row in rows)


def test_gc_timer_counts_the_collections_since_a_snapshot() -> None:
    timer = GcTimer()
    timer.install()
    try:
        before = timer.snapshot()
        assert timer.since(before) == {"gc_ms": 0.0, "gc_longest_ms": 0.0}
        gc.collect()
        gc.collect()
        fields = timer.since(before)
        assert fields["gc_ms"] > 0
        assert 0 < fields["gc_longest_ms"] <= fields["gc_ms"]
    finally:
        timer.uninstall()
    assert timer.since(timer.snapshot()) == {"gc_ms": 0.0, "gc_longest_ms": 0.0}


def test_segments_logs_the_three_segment_times_beside_every_step(long_file: Path, tmp_path: Path) -> None:
    out = tmp_path / "out.jsonl"
    assert main(["segments", "--file", str(long_file), "--ops", "type,enter", "--cursor-ops", "left", "--scatter", "10", "--steps", "3", "--runs", "1", "--out", str(out)]) == 0
    rows = _rows(out)
    steps = [row for row in rows if row["op"] != "scatter"]
    assert {row["variant"] for row in steps} == {"clean", "typed", "scattered-10"}
    assert all({"reestimate_ms", "reconcile_ms", "measure_ms", "reestimate_calls", "measure_calls"} <= set(row) for row in steps)
    typed = [row for row in steps if row["variant"] == "typed" and row["op"] == "type"]
    assert all(row["reestimate_calls"] >= 1 and row["reestimate_ms"] > 0 for row in typed)
    assert all(row["wrap_on"] is True and row["long_row"] is True for row in steps)


def test_segments_install_no_production_code(long_file: Path) -> None:
    from nova_editor.document._lazy_wrapped_document import LazyWrappedDocument
    from tools._view_app import ProbeTextArea, open_probe
    from tools._view_edit_latency import install_clock

    area = open_probe(long_file, wrap=True, config=LOWERED)
    try:
        before = (ProbeTextArea._reestimate, LazyWrappedDocument._measure)
        clock = install_clock(area)
        assert (ProbeTextArea._reestimate, LazyWrappedDocument._measure) == before
        area._reestimate(area.document)
        assert clock.calls["reestimate"] == 1
    finally:
        area.close()


def test_pieces_measures_cost_and_memory_per_piece(tmp_path: Path) -> None:
    out = tmp_path / "out.jsonl"
    assert main(["pieces", "--sizes", "200,400", "--calls", "5", "--memory-size", "2000", "--runs", "1", "--out", str(out)]) == 0
    rows = _rows(out)
    for size in (200, 400):
        for op, metric in (("splice_insert", "splice_ms"), ("splice_delete", "splice_ms"), ("row_range", "row_range_ms")):
            samples = [row[metric] for row in rows if row["state"] == f"pieces={size}" and row["op"] == op]
            assert len(samples) == 5
            assert all(value > 0 for value in samples)
    assert all(row["resolved"] for row in rows if row["op"] == "row_range")
    memory = {row["method"]: row for row in rows if row["op"] == "memory"}
    assert memory["tracemalloc"]["pieces"] == 2000
    assert 20 < memory["tracemalloc"]["bytes_per_piece_tracemalloc"] < 1000
    assert memory["rss"]["bytes_per_piece_rss"] > 0


def test_undo_record_reports_bytes_per_record_for_every_kind(tmp_path: Path) -> None:
    out = tmp_path / "out.jsonl"
    assert main(["undo-record", "--count", "30", "--out", str(out)]) == 0
    rows = {row["state"]: row for row in _rows(out)}
    assert set(rows) == {"typing", "backspace", "paste", "delete"}
    assert all(row["operations"] == 30 and row["bytes_per_record"] > 0 for row in rows.values())
    assert rows["typing"]["records"] < 30  # typing is coalesced
    assert rows["paste"]["records"] == 30
    assert rows["delete"]["records"] > 1


def test_clipboard_times_the_copy_with_and_without_the_terminal_write(tmp_path: Path, capsys: pytest.CaptureFixture[str]) -> None:
    out = tmp_path / "out.jsonl"
    assert main(["clipboard", "--sizes", "2048,8192", "--calls", "4", "--out", str(out)]) == 0
    rows = _rows(out)
    for size in (2048, 8192):
        for op in ("copy_only", "copy_write"):
            samples = [row for row in rows if row["state"] == str(size) and row["op"] == op]
            assert len(samples) == 4
            assert all(row["clipboard_ms"] > 0 and row["size_bytes"] == size for row in samples)
    text = capsys.readouterr().out
    assert "clipboard: 2048 bytes copy_write: n=4 p50=" in text
    assert "p95=" in text


def test_edit_output_goes_under_the_results_directory_by_default(mixed_file: Path, tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("RESULTS", str(tmp_path / "results"))
    assert main(["edit-latency", "--file", str(mixed_file), "--wrap", "off", "--ops", "type", "--steps", "2", "--runs", "1"]) == 0
    assert (tmp_path / "results" / "edit-latency-indexed-m-off.jsonl").exists()
    other = tmp_path / "elsewhere"
    assert main(["edit-latency", "--file", str(mixed_file), "--wrap", "off", "--ops", "type", "--steps", "2", "--runs", "1", "--results", str(other)]) == 0
    assert (other / "edit-latency-indexed-m-off.jsonl").exists()


def test_edit_ops_are_validated(mixed_file: Path, tmp_path: Path) -> None:
    with pytest.raises(SystemExit):
        main(["edit-latency", "--file", str(mixed_file), "--ops", "type,nonsense", "--out", str(tmp_path / "o.jsonl")])


def test_summarise_tables_of_the_edit_metrics(tmp_path: Path, capsys: pytest.CaptureFixture[str]) -> None:
    out = tmp_path / "out.jsonl"
    rows = [
        {"case": "segments", "file": "a", "wrap": "on", "state": "indexed", "op": "type", "variant": "clean", "latency_ms": 10, "reestimate_ms": 3, "reconcile_ms": 1, "measure_ms": 2},
        {"case": "segments", "file": "a", "wrap": "on", "state": "indexed", "op": "type", "variant": "clean", "latency_ms": 12, "reestimate_ms": 25, "reconcile_ms": 2, "measure_ms": 4},
        {"case": "pieces", "file": "s", "wrap": "off", "state": "pieces=1000", "op": "memory", "bytes_per_piece_rss": 101.5},
        {"case": "clipboard", "file": "s", "wrap": "off", "state": "65536", "op": "copy_write", "clipboard_ms": 0.5},
    ]
    out.write_text("\n".join(json.dumps(row) for row in rows))
    assert main(["summarise", str(out)]) == 0
    text = capsys.readouterr().out
    assert "| a | on | indexed | type | clean | reestimate_ms | 2 | 3.00 | 25.00 | 25.00 | 0 | - | - |" in text
    assert "bytes_per_piece_rss" in text
    assert "clipboard_ms" in text


SAVE_SMALL = ["--chunk", "512", "--fsync-every", "4096", "--scatter", "20", "--delete-bytes", "3000", "--paste-bytes", "1500", "--min-free-gib", "0"]
SAVE_PHASES = {"writing", "flushing", "finishing", "history"}


def _digest(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def _run_save(name: str, mixed_file: Path, tmp_path: Path, *extra: str) -> list[dict[str, Any]]:
    out = tmp_path / f"{name}.jsonl"
    assert main([name, "--file", str(mixed_file), "--out", str(out), "--runs", "1", *extra]) == 0
    return _rows(out)


def test_save_5g_saves_the_edited_document_and_verifies_the_output(mixed_file: Path, tmp_path: Path) -> None:
    before = _digest(mixed_file)
    target = tmp_path / "out-5g.txt"
    rows = _run_save("save-5g", mixed_file, tmp_path, "--target", str(target), *SAVE_SMALL)
    save = next(row for row in rows if row["case"] == "save-5g" and row["op"] == "save")
    assert save["terminal"] == "saved"
    assert save["ok"] is True
    assert save["progress_messages"] >= 1
    assert save["plan_calls"] > 1
    verify = [row for row in rows if row["case"] == "verify"]
    assert {row["state"] for row in verify} >= {"output", "after_rebase"}
    assert all(row["mismatches"] == 0 for row in verify)
    assert target.stat().st_size == next(row for row in verify if row["state"] == "output")["model_length"]
    assert {row["state"] for row in rows if row["op"] == "phase"} >= SAVE_PHASES
    assert all(row["rss_anon_kb_max"] > 0 for row in rows if row["op"] == "phase" and row["state"] in SAVE_PHASES and "rss_anon_kb_max" in row)
    assert Path(f"{tmp_path / 'save-5g.jsonl'}.samples.jsonl").exists()
    assert _digest(mixed_file) == before
    assert main(["summarise", str(tmp_path / "save-5g.jsonl")]) == 0


def test_save_commands_refuse_a_target_that_is_a_reference_file(mixed_file: Path, tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    out = str(tmp_path / "o.jsonl")
    before = _digest(mixed_file)
    with pytest.raises(SystemExit, match="refusing"):
        main(["save-5g", "--file", str(mixed_file), "--target", str(mixed_file), "--out", out, *SAVE_SMALL])
    refs = tmp_path / "refs"
    (refs / "results" / "act5").mkdir(parents=True)
    monkeypatch.setenv("REFS", str(refs))
    with pytest.raises(SystemExit, match="refusing"):
        main(["save-5g", "--file", str(mixed_file), "--target", str(refs / "other.txt"), "--out", out, *SAVE_SMALL])
    assert _digest(mixed_file) == before
    assert not (refs / "other.txt").exists()


def test_save_latency_steps_and_times_the_plan_calls(mixed_file: Path, tmp_path: Path) -> None:
    rows = _run_save("save-latency", mixed_file, tmp_path, "--target", str(tmp_path / "t.txt"), "--wrap", "both", "--steps", "3", *SAVE_SMALL)
    steps = [row for row in rows if row["op"] not in ("plan", "summary", "scatter", "delete", "paste", "save")]
    assert {row["wrap"] for row in steps} == {"off", "on"}
    assert {"down", "pagedown", "end", "ctrl+end", "vscroll", "x-refused"} <= {row["op"] for row in steps}
    assert any(row["state"] == "saving" for row in steps)
    refused = [row for row in steps if row["op"] == "x-refused"]
    assert refused
    assert all(row["state"] == "saving" for row in refused)
    plans = [row for row in rows if row["op"] == "plan"]
    assert len(plans) > 10
    assert all(row["action_ms"] >= 0 for row in plans)
    summary = [row for row in rows if row["op"] == "summary"]
    assert len(summary) == 2
    assert all(row["longest_step_ms"] is not None and row["plan_calls"] == row["plan_calls_expected"] for row in summary)
    assert main(["summarise", str(tmp_path / "save-latency.jsonl")]) == 0


def test_save_sweep_runs_every_combination_and_the_no_index_baseline(mixed_file: Path, tmp_path: Path) -> None:
    grid = ["--chunks", "512,2048", "--fsync-every", "4096,65536", "--scatter", "5", "--delete-bytes", "2000", "--paste-bytes", "500", "--min-free-gib", "0"]
    rows = _run_save("save-sweep", mixed_file, tmp_path, "--target", str(tmp_path / "t.txt"), *grid)
    saves = [row for row in rows if row["op"] == "save"]
    assert {(row["chunk"], row["fsync_every"]) for row in saves} == {(512, 4096), (512, 65536), (2048, 4096), (2048, 65536)}
    assert all(row["terminal"] == "saved" and row["build_index"] is True and row["index_after_rebase"] is True for row in saves)
    (tmp_path / "base").mkdir()
    base = _run_save("save-sweep", mixed_file, tmp_path / "base", "--target", str(tmp_path / "t.txt"), "--no-index", *grid)
    baseline = [row for row in base if row["op"] == "save"]
    assert len(baseline) == 4
    assert all(row["build_index"] is False and row["index_after_rebase"] is False and row["ok"] is True for row in baseline)
    assert all(row["mismatches"] == 0 for row in base if row["case"] == "verify")


def test_save_longline_saves_a_copy_in_place_and_types_at_the_column(mixed_file: Path, tmp_path: Path) -> None:
    before = _digest(mixed_file)
    copy = tmp_path / "copy.txt"
    rows = _run_save("save-longline", mixed_file, tmp_path, "--copy", str(copy), "--column", "3000", "--min-free-gib", "0", "--chunk", "512")
    assert _digest(mixed_file) == before
    assert not copy.exists()
    save = next(row for row in rows if row["op"] == "save")
    assert save["terminal"] == "saved"
    assert save["apply_rebase_ms"] > 0
    typed = next(row for row in rows if row["op"] == "type")
    assert typed["changed"] is True
    assert typed["doc_delta"] == 1
    assert {"cursor_state_before", "cursor_state_after", "cursor_location_after"} <= set(typed)
    assert all(row["mismatches"] == 0 for row in rows if row["case"] == "verify")


def test_save_retention_runs_both_sequences_with_window_hashes(mixed_file: Path, tmp_path: Path) -> None:
    rows = _run_save("save-retention", mixed_file, tmp_path, "--copy", str(tmp_path / "copy.txt"), "--delete-bytes", "4000", "--min-free-gib", "0", "--chunk", "512")
    assert {row["sequence"] for row in rows if row["op"] == "save"} == {"undo-save", "save-undo"}
    assert all(row["terminal"] == "saved" for row in rows if row["op"] == "save")
    verify = [row for row in rows if row["case"] == "verify"]
    assert {"undo", "output", "redo"} <= {row["state"] for row in verify}
    assert all(row["mismatches"] == 0 for row in verify)
    disk = [row for row in rows if row["op"] == "disk"]
    assert len(disk) == 2
    assert all({"old_file_bytes", "new_file_bytes", "disk_used_delta", "retained_inode_bytes", "extra_disk_bytes"} <= set(row) for row in disk)


def test_save_records_times_prepare_and_apply_rebase(tmp_path: Path) -> None:
    out = tmp_path / "records.jsonl"
    assert main(["save-records", "--records", "1,30,200", "--long-indexes", "8", "--runs", "1", "--out", str(out)]) == 0
    rows = [row for row in _rows(out) if row["op"] == "rebase"]
    assert [row["records_requested"] for row in rows] == [1, 30, 200]
    assert all(row["long_indexes"] == 8 and row["terminal"] == "saved" for row in rows)
    assert all(row["prepare_rebase_ms"] > 0 and row["apply_rebase_ms"] > 0 for row in rows)
    assert [row["contents"] for row in rows] == sorted(row["contents"] for row in rows)
    assert rows[-1]["contents"] >= 200
    assert main(["summarise", str(out)]) == 0


def test_save_cancel_stops_at_half_and_removes_the_temp_file(mixed_file: Path, tmp_path: Path) -> None:
    target = tmp_path / "out" / "t.txt"
    rows = _run_save("save-cancel", mixed_file, tmp_path, "--target", str(target), *SAVE_SMALL)
    save = next(row for row in rows if row["op"] == "save")
    assert save["terminal"] == "cancelled"
    assert save["ok"] is True
    assert save["cancel_latency_ms"] >= 0
    assert save["temp_files_left"] == 0
    assert save["target_unchanged"] is True
    assert not target.exists()
    assert not list(target.parent.glob(".*"))


def test_save_fulldisk_injects_enospc_when_no_tmpfs_can_be_mounted(mixed_file: Path, tmp_path: Path, capsys: pytest.CaptureFixture[str]) -> None:
    target = tmp_path / "out" / "t.txt"
    rows = _run_save("save-fulldisk", mixed_file, tmp_path, "--target", str(target), "--no-mount", *SAVE_SMALL)
    save = next(row for row in rows if row["op"] == "save")
    assert save["terminal"] == "failed"
    assert save["stage"] == "write"
    assert save["errno"] == 28
    assert save["method"] == "injection"
    assert save["ok"] is True
    assert save["temp_files_left"] == 0
    assert save["target_unchanged"] is True
    assert "injection" in capsys.readouterr().out


MODEL_BYTES = bytes((i * 37 + 11) % 256 for i in range(400))
MODEL_OPS = st.lists(st.tuples(st.sampled_from(["insert", "delete", "paste"]), st.integers(0, 10_000), st.integers(0, 10_000), st.binary(min_size=1, max_size=8)), max_size=40)


@pytest.fixture(scope="module")
def model_file(tmp_path_factory: pytest.TempPathFactory) -> Path:
    path = tmp_path_factory.mktemp("model") / "orig.bin"
    path.write_bytes(MODEL_BYTES)
    return path


@given(ops=MODEL_OPS)
@settings(deadline=None, max_examples=MODEL_EXAMPLES, derandomize=True, suppress_health_check=[HealthCheck.function_scoped_fixture])
def test_byte_model_follows_a_plain_bytes_reference(model_file: Path, ops: list[tuple[str, int, int, bytes]]) -> None:
    model = ByteModel(model_file)
    expected = bytearray(MODEL_BYTES)
    try:
        for kind, first, second, data in ops:
            at = first % (len(expected) + 1)
            if kind == "insert":
                model.insert(at, literal(data))
                expected[at:at] = data
            elif kind == "delete":
                model.delete(at, second % 40)
                del expected[at : at + second % 40]
            else:
                start = second % (len(expected) + 1)
                size = len(data) * 5
                model.insert(at, model.slice(start, size))
                expected[at:at] = bytes(expected[start : start + size])
            assert model.length == len(expected)
            assert model.read(0, len(expected)) == bytes(expected)
            assert model.read(first % (len(expected) + 1), 17) == bytes(expected[first % (len(expected) + 1) :][:17])
    finally:
        model.close()


def test_verify_file_reports_a_differing_window_and_a_differing_length(tmp_path: Path) -> None:
    original = tmp_path / "orig.bin"
    original.write_bytes(bytes(range(256)) * 40)
    model = ByteModel(original)
    spec = {"file": str(original), "wrap": "off", "run": 1}
    try:
        assert verify_file(spec, "same", original, model, [])["mismatches"] == 0
        damaged = tmp_path / "damaged.bin"
        data = bytearray(original.read_bytes())
        data[5000] ^= 1
        damaged.write_bytes(data)
        row = verify_file(spec, "flipped", damaged, model, [5000])
        assert row["mismatches"] >= 1  # windows overlap, so a flipped byte shows in several
        assert row["mismatch_offsets"]
        short = tmp_path / "short.bin"
        short.write_bytes(original.read_bytes()[:-1])
        assert verify_file(spec, "short", short, model, [])["mismatches"] >= 1
    finally:
        model.close()
