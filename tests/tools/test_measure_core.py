"""Smoke tests for the core measurement script."""

from __future__ import annotations

import json
import subprocess
import sys
from pathlib import Path
from typing import Any


def run(*args: str) -> dict[str, Any]:
    result = subprocess.run([sys.executable, "-m", "tools.measure_core", *args], capture_output=True, text=True, check=True)
    return json.loads(result.stdout.strip().splitlines()[-1])


def test_index_mode_reports_rss_and_time(tmp_path: Path) -> None:
    path = tmp_path / "f.txt"
    path.write_bytes(b"line\n" * 20_000)
    out = run("index", str(path), "--stride", "64", "--cache-blocks", "128")
    assert out["kind"] == "index"
    assert out["rows"] == 20_001
    assert out["stride"] == 64
    assert isinstance(out["rss_anon_kb_after"], int)
    assert float(out["scan_seconds"]) >= 0


def test_longline_mode(tmp_path: Path) -> None:
    path = tmp_path / "long.txt"
    path.write_bytes(("xé\t" * 50_000).encode())
    out = run("longline", str(path))
    assert out["kind"] == "longline"
    assert out["total_chars"] == 150_000


def test_calls_mode_reports_worst_call(tmp_path: Path) -> None:
    path = tmp_path / "m.txt"
    path.write_bytes((b"x" * 15_000 + b"\n") * 500)
    out = run("calls", str(path), "--samples", "50")
    assert out["kind"] == "calls"
    assert float(out["worst_ms"]) < 1000
    assert int(out["worst_bytes"]) <= 4 * 1024 * 1024
