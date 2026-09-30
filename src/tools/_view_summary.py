"""`summarise` subcommand of the view benchmark harness: markdown tables from raw JSON lines.

Percentile method: nearest rank (`tools._view_procmem.percentile`), the value at 1-based rank `ceil(p * n)` of the sorted sample; nothing is interpolated.
"""

from __future__ import annotations

import json
from collections import defaultdict
from collections.abc import Sequence
from pathlib import Path
from typing import Any

from tools._view_procmem import median, percentile

METRIC_KEYS = (
    "first_screen_ms",
    "latency_ms",
    "pilot_ms",
    "repaint_ms",
    "provisional_ms",
    "resolved_after_ms",
    "render_ms",
    "wrap_ms",
    "scan_complete_s",
    "rss_anon_mib_max",
    "vm_rss_mib_max",
    "rss_file_mib_max",
    "mismatches",
)
"""Numeric fields that `summarise` turns into table rows, in this order."""
SLOW_MS = 50.0
GROUP_KEYS = ("case", "file", "wrap", "state", "op", "variant")


def load_rows(path: Path) -> list[dict[str, Any]]:
    """Read a JSON lines file; blank lines are skipped."""
    return [json.loads(line) for line in path.read_text(encoding="utf-8").splitlines() if line.strip()]


def _number(value: object) -> float | None:
    if isinstance(value, bool) or not isinstance(value, int | float):
        return None
    return float(value)


def _format(value: float) -> str:
    return f"{value:.2f}"


def summarise_rows(rows: Sequence[dict[str, Any]], source: str) -> str:
    """Return markdown tables for `rows`: one table per `case`, one line per file, wrap, state, op, variant and metric.

    Columns: n, median, p95, max, the count over 50 ms (`_ms` metrics only),
    `n scan_running` (`latency_ms` rows that carry `scan_running`: the steps issued during a scan) and `n completing` (steps during which the scan completed).
    A first-screen table also states the runs, the failed runs (no `first_screen_ms`) and the runs labelled `cold_verified=false`.
    The source name is printed under each table as `[name]`.
    """
    groups: dict[str, dict[tuple[str, ...], dict[str, list[float]]]] = defaultdict(lambda: defaultdict(lambda: defaultdict(list)))
    scanning: dict[str, dict[tuple[str, ...], int]] = defaultdict(lambda: defaultdict(int))
    completing: dict[str, dict[tuple[str, ...], int]] = defaultdict(lambda: defaultdict(int))
    for row in rows:
        key = tuple(str(row.get(name, "-")) for name in GROUP_KEYS[1:])
        for metric in METRIC_KEYS:
            value = _number(row.get(metric))
            if value is not None:
                groups[str(row.get("case", "-"))][key][metric].append(value)
                if metric == "latency_ms" and row.get("scan_running") is True:
                    scanning[str(row.get("case", "-"))][key] += 1
                if metric == "latency_ms" and row.get("scan_completed_during_step") is True:
                    completing[str(row.get("case", "-"))][key] += 1
    with_flag: dict[str, set[tuple[str, ...]]] = defaultdict(set)
    for row in rows:
        if isinstance(row.get("scan_running"), bool) and _number(row.get("latency_ms")) is not None:
            with_flag[str(row.get("case", "-"))].add(tuple(str(row.get(name, "-")) for name in GROUP_KEYS[1:]))
    lines: list[str] = []
    first_screen = [row for row in rows if row.get("case") == "first-screen"]
    for case in dict.fromkeys([*groups, *(["first-screen"] if first_screen else [])]):
        by_key = groups.get(case, {})
        lines.append(f"### {case}")
        lines.append("")
        if case == "first-screen":
            failed = sum(1 for row in first_screen if _number(row.get("first_screen_ms")) is None)
            unverified = sum(1 for row in first_screen if row.get("cold_verified") is False)
            lines.append(f"first-screen runs: {len(first_screen)}, failed: {failed}, cold_verified=false: {unverified} (failed runs are not in the table)")
            lines.append("")
        lines.append("p95 is the nearest-rank value of the n samples of the row (column n); with n of at most 20 it equals the max.")
        lines.append("")
        lines.append("| file | wrap | state | op | variant | metric | n | median | p95 | max | count over 50 ms | n scan_running | n completing |")
        lines.append("|---|---|---|---|---|---|---|---|---|---|---|---|---|")
        for key, metrics in by_key.items():
            for metric in METRIC_KEYS:
                values = metrics.get(metric)
                if not values:
                    continue
                over = str(sum(1 for v in values if v > SLOW_MS)) if metric.endswith("_ms") else "-"
                running = str(scanning[case][key]) if metric == "latency_ms" and key in with_flag[case] else "-"
                done = str(completing[case][key]) if metric == "latency_ms" and key in with_flag[case] else "-"
                cells = [*key, metric, str(len(values)), _format(median(values)), _format(percentile(values, 0.95)), _format(max(values)), over, running, done]
                lines.append("| " + " | ".join(cells) + " |")
        lines.append("")
        lines.append(f"[{source}]")
        lines.append("")
    return "\n".join(lines)


def summarise_files(paths: Sequence[Path]) -> str:
    """Return the tables of every file, each with its own `[filename]` source tag."""
    return "\n".join(summarise_rows(load_rows(path), path.name) for path in paths)
