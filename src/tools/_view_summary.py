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

    Columns: n, median, p95, max and the count over 50 ms (`_ms` metrics only). The source name is printed under each table as `[name]`.
    """
    groups: dict[str, dict[tuple[str, ...], dict[str, list[float]]]] = defaultdict(lambda: defaultdict(lambda: defaultdict(list)))
    for row in rows:
        key = tuple(str(row.get(name, "-")) for name in GROUP_KEYS[1:])
        for metric in METRIC_KEYS:
            value = _number(row.get(metric))
            if value is not None:
                groups[str(row.get("case", "-"))][key][metric].append(value)
    lines: list[str] = []
    for case, by_key in groups.items():
        lines.append(f"### {case}")
        lines.append("")
        lines.append("| file | wrap | state | op | variant | metric | n | median | p95 | max | count over 50 ms |")
        lines.append("|---|---|---|---|---|---|---|---|---|---|---|")
        for key, metrics in by_key.items():
            for metric in METRIC_KEYS:
                values = metrics.get(metric)
                if not values:
                    continue
                over = str(sum(1 for v in values if v > SLOW_MS)) if metric.endswith("_ms") else "-"
                cells = [*key, metric, str(len(values)), _format(median(values)), _format(percentile(values, 0.95)), _format(max(values)), over]
                lines.append("| " + " | ".join(cells) + " |")
        lines.append("")
        lines.append(f"[{source}]")
        lines.append("")
    return "\n".join(lines)


def summarise_files(paths: Sequence[Path]) -> str:
    """Return the tables of every file, each with its own `[filename]` source tag."""
    return "\n".join(summarise_rows(load_rows(path), path.name) for path in paths)
