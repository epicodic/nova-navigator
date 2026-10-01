"""Benchmark harness for the lazy view of `nova_editor` (ACT3): first screen, memory, key latency, jumps, correctness and thresholds.

Usage (every subcommand appends JSON lines to `--out`, prints one summary line and exits 0 on success):
    uv run python -m tools.measure_view first-screen --file F [--cold] --runs N --out O
    uv run python -m tools.measure_view memory --file F --wrap {off,on} --runs N --out O
    uv run python -m tools.measure_view latency --file F --wrap {off,on} --state {indexing,indexed} --op OP --steps N --runs N --out O [--profile]
    uv run python -m tools.measure_view jump --file F --wrap {off,on} --column 100000000 --runs N --out O
    uv run python -m tools.measure_view oracle --file F --column 100000000 --out O
    uv run python -m tools.measure_view calllog --file F --out O
    uv run python -m tools.measure_view sweep-yield --file F --values 0,0.0005,0.001 [--scan-block 262144,1048576] --steps N --runs N --out O
    uv run python -m tools.measure_view thresholds --kind {highlight,wordwrap,longrow,eager} (--sizes L | --row-bytes L) [--width W] --out O
    uv run python -m tools.measure_view summarise PATH...
  ACT4 (edit) subcommands; `--out` defaults to `<results>/<subcommand>-<file stem>[-<wrap>].jsonl`, `--results` to `$RESULTS` or `/tmp/scratchpad/s0001-refs/results/act4`:
    uv run python -m tools.measure_view edit-memory --file F --wrap {off,on} [--variant {auto,lines,longline}] [--select-bytes N] --runs N [--out O]
    uv run python -m tools.measure_view verify --file F --wrap {off,on} [--variant V] [--select-bytes N] [--out O]
    uv run python -m tools.measure_view edit-latency --file F --wrap {off,on,both} --state {indexed,indexing} [--ops type,backspace,delete,enter] --steps N --runs N [--out O]
    uv run python -m tools.measure_view edit-scatter --file F --wrap {off,on,both} [--scatter 1000] [--ops ...] --steps N --runs N [--out O]
    uv run python -m tools.measure_view segments --file F --wrap on [--ops ...] [--cursor-ops ...] [--scatter 1000] --steps N --runs N [--out O]
    uv run python -m tools.measure_view pieces [--sizes 1000,10000,100000,1000000] [--calls N] [--memory-size N] [--runs N] [--out O]
    uv run python -m tools.measure_view undo-record [--file F] [--count N] [--out O]
    uv run python -m tools.measure_view clipboard [--file F] [--sizes 65536,262144,1048576,4194304] [--calls 50] [--out O]

OP is one of down, up, pagedown, pageup, left, right, home, end, ctrl+right, hscroll, farjump.

Methods:
    first-screen: the parent takes t0 before `Popen([python, -m, nova_editor.app, F])` on a 120x30 pty; the app writes
        `FIRST_CONTENT <perf_counter_ns>` to the file named by `NOVA_EDIT_TIMING_FILE`. `first_before_index_done` is derived from the bytes
        the child had read at that moment (`/proc/<pid>/io` `rchar` below the file size (minus the start-up reads of a tiny file) below the file size means the line scan had not finished reading).
    memory, latency, jump: one child process per run (`python -m tools.measure_view child ...`), a headless `App.run_test` of 120x30.
        The parent samples `/proc/<pid>/status` every 50 ms; `RssAnon` is the primary memory figure.
    latency: a `textual.events.Key` goes through `app._driver.send_message` (the headless driver has one; `app.post_message` is the fallback
        when it is missing). `latency_ms` is the time until the widget's next `render_line` after the cursor or scroll offset changed;
        `pilot_ms` is `pilot.press` plus `pilot.pause()` for the same operation; op `f24` (an unbound key) is the Pilot idle floor.
        One JSON row per step.
    edit-memory: one child process, `RssAnon` sampled every 50 ms by the parent; the child selects about 1 GB (100 MB of the line for a long row file), deletes,
        undoes, redoes, copies, pastes elsewhere and undoes, printing `PHASE <step> <ns>`; every step is followed by a `verify` row (window hashes of the document against
        windows computed from the file with the offset arithmetic of the scenario). `<out>.samples.jsonl` holds every 50 ms sample with its phase.
    edit-latency, edit-scatter, segments: direct injection of keys (no Pilot), one child per run, `latency_ms` as for `latency`; edits count as a change when the document length changes.
Percentiles in `summarise` use the nearest-rank method.
Files of at most 1 MiB get lowered thresholds (`--config auto`), so a small synthetic file exercises the medium and long row paths.
"""

from __future__ import annotations

import argparse
import asyncio
import contextlib
import faulthandler
import fcntl
import json
import logging
import os
import pty
import select
import struct
import subprocess
import sys
import tempfile
import termios
import time
from collections.abc import Callable, Coroutine, Sequence
from pathlib import Path
from typing import Any

from tools import _view_edit, _view_edit_core, _view_edit_latency, _view_oracle, _view_scenarios, _view_thresholds
from tools._view_app import versions
from tools._view_procmem import COLD_RESIDENCY_LIMIT, KIB_PER_MIB, Supervised, drop_cache, kill_group, median, percentile, read_mem, read_rchar, resident_fraction, supervise
from tools._view_summary import summarise_files
from tools._view_thresholds import run_thresholds

FAR = 100_000_000
DEFAULT_RESULTS = str(Path(tempfile.gettempdir()) / "scratchpad" / "s0001-refs" / "results" / "act4")
EDIT_TIMEOUT = 1800.0
SYNTHETIC_ROWS = 3000
DEFAULT_SCAN_BLOCK = 1 << 20
TERMINAL_SIZE = (30, 120)
_PTY_STEP = 0.005
_PHASE_PARTS = 3
_MEMORY_PHASES = ("open", "indexing", "pagedown", "end", "pageup")
_FIRST_CONTENT = "FIRST_CONTENT "
LOGGER = logging.getLogger(__name__)

Row = dict[str, Any]
Scenario = Callable[[dict[str, Any]], Coroutine[Any, Any, list[Row]]]


def _scenarios() -> dict[str, Scenario]:
    return {
        "latency": _view_scenarios.latency_scenario,
        "jump": _view_scenarios.jump_scenario,
        "sweep": _view_scenarios.sweep_scenario,
        "memory": _view_scenarios.memory_scenario,
        "calllog": _view_scenarios.calllog_scenario,
        "threshold": _view_thresholds.threshold_scenario,
        "oracle": _view_oracle.oracle_scenario,
        "edit-memory": _view_edit.edit_memory_scenario,
        "edit-latency": _view_edit_latency.edit_latency_scenario,
        "segments": _view_edit_latency.segments_scenario,
        "pieces": _view_edit_core.pieces_scenario,
        "undo-record": _view_edit_core.undo_record_scenario,
        "clipboard": _view_edit_core.clipboard_scenario,
    }


def _append(out: Path, rows: Sequence[Row]) -> None:
    out.parent.mkdir(parents=True, exist_ok=True)
    with out.open("a", encoding="utf-8") as handle:
        handle.writelines(json.dumps(row) + "\n" for row in rows)


def _base(case: str, path: Path, *, wrap: str, state: str, op: str, run: int, **extra: object) -> Row:
    return {"case": case, "file": path.name, "wrap": wrap, "state": state, "op": op, "run": run, **versions(), **extra}


def _metric_summary(rows: Sequence[Row], metric: str) -> str:
    values = [float(row[metric]) for row in rows if isinstance(row.get(metric), int | float)]
    if not values:
        return f"no {metric} values"
    return f"{metric}: n={len(values)} median={median(values):.2f} p95={percentile(values, 0.95):.2f} max={max(values):.2f}"


def _finish(name: str, rows: Sequence[Row], out: Path, metric: str, failures: int = 0) -> int:
    _append(out, rows)
    tail = f" FAILED runs: {failures}" if failures else ""
    print(f"{name}: {len(rows)} rows -> {out}; {_metric_summary(rows, metric)}{tail}")
    return 1 if failures else 0


# -- first-screen -------------------------------------------------------------------------------------------------------------------
def _parse_first_content(path: Path) -> int | None:
    try:
        text = path.read_text(encoding="ascii")
    except OSError:
        return None
    if text.startswith(_FIRST_CONTENT) and text.endswith("\n"):
        return int(text[len(_FIRST_CONTENT) :].split()[0])
    return None


def first_screen_once(path: Path, *, cold: bool, timeout: float, baseline: int = 0) -> Row:
    """Start nova_edit on a pty and return the first screen time of one run (one row, `run` not set)."""
    residency = drop_cache(path) if cold else resident_fraction(path)
    size = path.stat().st_size
    with tempfile.TemporaryDirectory() as directory:
        timing_file = Path(directory) / "timing.txt"
        master, slave = pty.openpty()
        try:
            fcntl.ioctl(slave, termios.TIOCSWINSZ, struct.pack("HHHH", *TERMINAL_SIZE, 0, 0))
            env = {**os.environ, "NOVA_EDIT_TIMING_FILE": str(timing_file), "TERM": "xterm-256color", "COLUMNS": str(TERMINAL_SIZE[1]), "LINES": str(TERMINAL_SIZE[0])}
            t0 = time.perf_counter_ns()
            proc = subprocess.Popen([sys.executable, "-m", "nova_editor.app", str(path)], stdin=slave, stdout=slave, stderr=slave, env=env, start_new_session=True, close_fds=True)
        except BaseException:
            os.close(slave)
            os.close(master)
            raise
        os.close(slave)
        first: int | None = None
        rchar: int | None = None
        anon_kb: int | None = None
        tail = b""
        status = "ok"
        deadline = time.perf_counter() + timeout
        try:
            while first is None:
                ready, _, _ = select.select([master], [], [], _PTY_STEP)
                if ready:
                    with contextlib.suppress(OSError):
                        tail = (tail + os.read(master, 65536))[-2000:]
                first = _parse_first_content(timing_file)
                if first is not None:
                    rchar = read_rchar(proc.pid)
                    with contextlib.suppress(OSError, KeyError):
                        anon_kb = read_mem(proc.pid).rss_anon_kb
                elif proc.poll() is not None:
                    status = f"exited_{proc.returncode}"
                    break
                elif time.perf_counter() > deadline:
                    status = "timeout"
                    break
        finally:
            kill_group(proc)
            proc.wait()
            os.close(master)
    fraction = None if rchar is None or size == 0 else max(0, rchar - baseline) / size
    row = _base("first-screen", path, wrap="off", state="cold" if cold else "warm", op="first_screen", run=0)
    row.update(
        first_screen_ms=None if first is None else (first - t0) / 1e6,
        residency_before=residency,
        cold_requested=cold,
        cold_verified=(residency <= COLD_RESIDENCY_LIMIT) if cold else None,
        first_before_index_done=None if fraction is None else fraction < 1.0,
        child_rchar_at_first_content=rchar,
        child_scan_read_fraction_at_first_content=fraction,
        rss_anon_kb_at_first_content=anon_kb,
        status=status,
    )
    if status != "ok":
        row["output_tail"] = tail.decode("utf-8", "replace")
    return row


def _baseline_rchar(timeout: float) -> int:
    """Return the bytes an app reads for imports and start-up (`rchar` at first content on a tiny file), to subtract from the scan progress."""
    with tempfile.TemporaryDirectory() as directory:
        tiny = Path(directory) / "tiny.txt"
        tiny.write_text("baseline line of text\n" * 20, encoding="utf-8")
        row = first_screen_once(tiny, cold=False, timeout=timeout)
    return int(row.get("child_rchar_at_first_content") or 0)


def cmd_first_screen(args: argparse.Namespace) -> int:
    path = Path(args.file)
    baseline = _baseline_rchar(args.timeout)
    rows = []
    for run in range(1, args.runs + 1):
        row = first_screen_once(path, cold=args.cold, timeout=args.timeout, baseline=baseline)
        row["run"] = run
        rows.append(row)
    failures = sum(1 for row in rows if row["first_screen_ms"] is None)
    unverified = sum(1 for row in rows if row["cold_verified"] is False)
    code = _finish("first-screen", rows, Path(args.out), "first_screen_ms", failures)
    if unverified:
        print(f"first-screen: {unverified} run(s) labelled cold_verified=false (residency above {COLD_RESIDENCY_LIMIT})")
    return code


# -- child processes ----------------------------------------------------------------------------------------------------------------
def _child_command(kind: str, spec: dict[str, Any], rows_file: Path) -> list[str]:
    return [sys.executable, "-m", "tools.measure_view", "child", kind, "--spec", json.dumps(spec), "--rows", str(rows_file), "--dump-after", str(max(5.0, float(spec.get("child_timeout", 900)) - 20))]


def run_child(kind: str, spec: dict[str, Any], timeout: float) -> tuple[list[Row], list[str], Supervised]:
    """Run one scenario in a fresh process; return its rows, its protocol lines and the `Supervised` record (samples, status)."""
    with tempfile.TemporaryDirectory() as directory:
        rows_file = Path(directory) / "rows.jsonl"
        result = supervise(_child_command(kind, {**spec, "child_timeout": timeout}, rows_file), timeout)
        rows = [json.loads(line) for line in rows_file.read_text(encoding="utf-8").splitlines() if line.strip()] if rows_file.exists() else []
    if result.returncode != 0 or result.status != "exit":
        print(f"child {kind} run {spec.get('run')}: status={result.status} returncode={result.returncode}\n{result.stderr_tail}", file=sys.stderr)
    return rows, result.lines, result


def cmd_child(args: argparse.Namespace) -> int:
    """Hidden subcommand: run one scenario in this process and write its rows to `--rows`."""
    if args.dump_after:
        faulthandler.enable(all_threads=True)
        faulthandler.dump_traceback_later(args.dump_after, repeat=False, file=sys.stderr)
    spec = json.loads(args.spec)
    try:
        rows = asyncio.run(_scenarios()[args.kind](spec))
    except Exception:
        LOGGER.exception("scenario %s failed", args.kind)
        return 1
    Path(args.rows).write_text("".join(json.dumps(row) + "\n" for row in rows), encoding="utf-8")
    faulthandler.cancel_dump_traceback_later()
    return 0


def _spec(args: argparse.Namespace, run: int, **extra: object) -> dict[str, Any]:
    return {"file": str(Path(args.file).resolve()), "wrap": getattr(args, "wrap", "off"), "run": run, "config": args.config, "timeout": args.timeout, **extra}


# -- memory -------------------------------------------------------------------------------------------------------------------------
def _marks(lines: Sequence[str]) -> list[tuple[str, int]]:
    """The `PHASE <name> <ns>` marks of a child's protocol lines."""
    marks: list[tuple[str, int]] = []
    for line in lines:
        parts = line.split()
        if len(parts) == _PHASE_PARTS and parts[0] == "PHASE":
            marks.append((parts[1], int(parts[2])))
    return marks


def _figures(maxima: dict[str, int]) -> Row:
    if not maxima:
        return {"n_samples": 0}
    return {
        **maxima,
        "rss_anon_mib_max": maxima["rss_anon_kb_max"] / KIB_PER_MIB,
        "vm_rss_mib_max": maxima["vm_rss_kb_max"] / KIB_PER_MIB,
        "rss_file_mib_max": maxima["rss_file_kb_max"] / KIB_PER_MIB,
    }


def _memory_rows(args: argparse.Namespace, run: int) -> tuple[list[Row], bool]:
    path = Path(args.file)
    _rows, lines, result = run_child("memory", _spec(args, run), args.timeout)
    done = "DONE" in lines
    marks = _marks(lines)
    common = {"status": result.status if result.status != "exit" or done else "exit_incomplete", "returncode": result.returncode}
    rows: list[Row] = []
    begin = result.t0_ns
    for name, end in marks:
        rows.append(_base("memory", path, wrap=args.wrap, state=name, op="session", run=run, **common, phase_ms=(end - begin) / 1e6, **_figures(result.maxima(begin, end))))
        begin = end
    last = result.samples[-1] if result.samples else None
    final = {} if last is None else {"rss_anon_kb": last.rss_anon_kb, "vm_rss_kb": last.vm_rss_kb, "rss_file_kb": last.rss_file_kb}
    rows.append(
        _base(
            "memory",
            path,
            wrap=args.wrap,
            state="session",
            op="session",
            run=run,
            **common,
            phases=[name for name, _ in marks],
            stderr_tail=result.stderr_tail if not done else "",
            **final,
            **_figures(result.maxima()),
        )
    )
    return rows, done


def cmd_memory(args: argparse.Namespace) -> int:
    rows: list[Row] = []
    failures = 0
    for run in range(1, args.runs + 1):
        run_rows, done = _memory_rows(args, run)
        rows += run_rows
        failures += 0 if done else 1
    _append(Path(args.out), [row for row in rows if row["state"] != "session"])
    return _finish("memory", [row for row in rows if row["state"] == "session"], Path(args.out), "rss_anon_mib_max", failures)


# -- latency, jump, sweep-yield ------------------------------------------------------------------------------------------------------
def _run_many(name: str, kind: str, args: argparse.Namespace, specs: Sequence[dict[str, Any]], metric: str, out: Path | None = None) -> int:
    rows: list[Row] = []
    failures = 0
    for spec in specs:
        run_rows, _lines, result = run_child(kind, spec, args.timeout)
        rows += run_rows
        failures += 0 if result.returncode == 0 and result.status == "exit" else 1
    return _finish(name, rows, Path(args.out) if out is None else out, metric, failures)


def cmd_latency(args: argparse.Namespace) -> int:
    profile_out = f"{args.out}.profile.txt" if args.profile else None
    specs = [_spec(args, run, state=args.state, op=args.op, steps=args.steps, far=FAR, profile_out=profile_out) for run in range(1, args.runs + 1)]
    return _run_many("latency", "latency", args, specs, "latency_ms")


def cmd_jump(args: argparse.Namespace) -> int:
    specs = [_spec(args, run, column=args.column, resolve_timeout=args.resolve_timeout) for run in range(1, args.runs + 1)]
    return _run_many("jump", "jump", args, specs, "repaint_ms")


def cmd_sweep(args: argparse.Namespace) -> int:
    values = [float(item) for item in args.values.split(",")]
    blocks = [int(item) for item in args.scan_block.split(",")]
    specs = [_spec(args, run, steps=args.steps, yield_seconds=value, scan_block=block, far=FAR) for value in values for block in blocks for run in range(1, args.runs + 1)]
    return _run_many("sweep-yield", "sweep", args, specs, "latency_ms")


# -- edit subcommands (ACT4) --------------------------------------------------------------------------------------------------------
def _out_path(args: argparse.Namespace, name: str) -> Path:
    """`--out` when given, otherwise `<results>/<name>-<file stem>[-<wrap>].jsonl` with the results directory from `--results`, `$RESULTS` or the ACT4 default."""
    if args.out:
        return Path(args.out)
    results = Path(args.results or os.environ.get("RESULTS") or DEFAULT_RESULTS)
    parts = [name, Path(args.file).stem if getattr(args, "file", None) else "synthetic"]
    wrap = getattr(args, "wrap", None)
    if wrap:
        parts.append(wrap)
    return results / ("-".join(parts) + ".jsonl")


def _wraps(args: argparse.Namespace) -> list[str]:
    return ["off", "on"] if args.wrap == "both" else [args.wrap]


def _ops(text: str) -> list[str]:
    ops = [item.strip() for item in text.split(",") if item.strip()]
    unknown = [op for op in ops if op not in _view_edit_latency.ALL_OPS]
    if unknown:
        msg = f"unknown ops {unknown}; choose from {', '.join(_view_edit_latency.ALL_OPS)}"
        raise SystemExit(msg)
    return ops


def _write_synthetic(path: Path) -> Path:
    """Write a small text file (rows of ASCII, accents and CJK) for the measurements that need no reference file."""
    rows = [f"row {i}: the quick brown fox jumps over the lazy dog, caf\u00e9 \u65e5\u672c\u8a9e {i * 7919 % 1000}" for i in range(SYNTHETIC_ROWS)]
    path.write_text("\n".join(rows) + "\n", encoding="utf-8")
    return path


def _with_file(args: argparse.Namespace, run: Callable[[argparse.Namespace], int]) -> int:
    """Run `run(args)` with `args.file` set; without `--file` a synthetic file of a few hundred KiB exists for the duration of the call."""
    if args.file:
        return run(args)
    with tempfile.TemporaryDirectory() as directory:
        args.file = str(_write_synthetic(Path(directory) / "synthetic.txt"))
        return run(args)


def _mismatches(rows: Sequence[Row]) -> list[int]:
    return [int(row["mismatches"]) for row in rows if row.get("case") == "verify"]


def _edit_memory_rows(args: argparse.Namespace, run: int, samples: list[Row]) -> tuple[list[Row], bool]:
    path = Path(args.file)
    child_rows, lines, result = run_child("edit-memory", _spec(args, run, variant=args.variant, select_bytes=args.select_bytes), args.timeout)
    done = "DONE" in lines
    marks = _marks(lines)
    common = {"status": result.status if result.status != "exit" or done else "exit_incomplete", "returncode": result.returncode}
    rows: list[Row] = [*child_rows]
    begin = result.t0_ns
    for name, end in marks:
        window = [sample for sample in result.samples if begin <= sample.t_ns <= end]
        last = window[-1].rss_anon_kb if window else None
        rows.append(_base("edit-memory", path, wrap=args.wrap, state=name, op="phase", run=run, **common, phase_ms=(end - begin) / 1e6, rss_anon_kb_last=last, **_figures(result.maxima(begin, end))))
        samples.extend(
            {"case": "edit-memory-sample", "file": path.name, "wrap": args.wrap, "run": run, "phase": name, "t_ms": (sample.t_ns - result.t0_ns) / 1e6, "rss_anon_kb": sample.rss_anon_kb}
            for sample in window
        )
        begin = end
    rows.append(
        _base(
            "edit-memory",
            path,
            wrap=args.wrap,
            state="session",
            op="session",
            run=run,
            **common,
            phases=[name for name, _ in marks],
            stderr_tail=result.stderr_tail if not done else "",
            **_figures(result.maxima()),
        )
    )
    return rows, done


def cmd_edit_memory(args: argparse.Namespace) -> int:
    out = _out_path(args, "edit-memory")
    samples: list[Row] = []
    rows: list[Row] = []
    failures = 0
    for run in range(1, args.runs + 1):
        run_rows, done = _edit_memory_rows(args, run, samples)
        rows += run_rows
        failures += 0 if done and not any(_mismatches(run_rows)) else 1
    _append(out, [row for row in rows if row["state"] != "session"])
    _append(Path(f"{out}.samples.jsonl"), samples)
    print(f"edit-memory: window mismatches {sum(_mismatches(rows))} (expected 0), samples -> {out}.samples.jsonl")
    return _finish("edit-memory", [row for row in rows if row["state"] == "session"], out, "rss_anon_mib_max", failures)


def cmd_verify(args: argparse.Namespace) -> int:
    out = _out_path(args, "verify")
    rows: list[Row] = []
    failures = 0
    for run in range(1, args.runs + 1):
        run_rows, _lines, result = run_child("edit-memory", _spec(args, run, variant=args.variant, select_bytes=args.select_bytes), args.timeout)
        rows += [row for row in run_rows if row["case"] == "verify"]
        failures += 0 if result.returncode == 0 and result.status == "exit" else 1
    bad = sum(_mismatches(rows))
    code = _finish("verify", rows, out, "mismatches", failures + (1 if bad else 0))
    print(f"verify: steps={len(rows)} windows={sum(int(row['windows']) for row in rows)} mismatches={bad} (expected 0)")
    return code


def _edit_specs(args: argparse.Namespace, **extra: object) -> list[dict[str, Any]]:
    return [_spec(args, run, wrap=wrap, steps=args.steps, far=FAR, **extra) for wrap in _wraps(args) for run in range(1, args.runs + 1)]


def cmd_edit_latency(args: argparse.Namespace) -> int:
    specs = _edit_specs(args, state=args.state, ops=_ops(args.ops), scatter=0, variant=args.variant)
    return _run_many("edit-latency", "edit-latency", args, specs, "latency_ms", _out_path(args, f"edit-latency-{args.state}"))


def cmd_edit_scatter(args: argparse.Namespace) -> int:
    specs = _edit_specs(args, state="indexed", ops=_ops(args.ops), scatter=args.scatter, variant=args.variant)
    return _run_many("edit-scatter", "edit-latency", args, specs, "latency_ms", _out_path(args, "edit-scatter"))


def cmd_segments(args: argparse.Namespace) -> int:
    specs = _edit_specs(args, state="indexed", ops=_ops(args.ops), cursor_ops=_ops(args.cursor_ops), scatter=args.scatter, variant=args.variant)
    return _run_many("segments", "segments", args, specs, "reestimate_ms", _out_path(args, "segments"))


def cmd_pieces(args: argparse.Namespace) -> int:
    base = {"file": "synthetic", "wrap": "off", "config": "default", "timeout": args.timeout}
    sizes = [int(item) for item in args.sizes.split(",")]
    memory_size = args.memory_size or max(sizes)
    specs: list[dict[str, Any]] = []
    for run in range(1, args.runs + 1):
        specs.append({**base, "run": run, "method": "cost", "sizes": sizes, "calls": args.calls})
        specs += [{**base, "run": run, "method": method, "memory_size": memory_size} for method in ("tracemalloc", "rss")]
    return _run_many("pieces", "pieces", args, specs, "splice_ms", _out_path(args, "pieces"))


def _load_rows(path: Path) -> list[Row]:
    return [json.loads(line) for line in path.read_text(encoding="utf-8").splitlines() if line.strip()] if path.exists() else []


def cmd_undo_record(args: argparse.Namespace) -> int:
    def run(args: argparse.Namespace) -> int:
        specs = [_spec(args, number, count=args.count) for number in range(1, args.runs + 1)]
        return _run_many("undo-record", "undo-record", args, specs, "bytes_per_record", _out_path(args, "undo-record"))

    return _with_file(args, run)


def cmd_clipboard(args: argparse.Namespace) -> int:
    def run(args: argparse.Namespace) -> int:
        sizes = [int(item) for item in args.sizes.split(",")]
        out = _out_path(args, "clipboard")
        specs = [_spec(args, number, sizes=sizes, calls=args.calls) for number in range(1, args.runs + 1)]
        before = len(_load_rows(out))
        code = _run_many("clipboard", "clipboard", args, specs, "clipboard_ms", out)
        fresh = _load_rows(out)[before:]
        for size in sizes:
            for op in ("copy_only", "copy_write"):
                values = [float(row["clipboard_ms"]) for row in fresh if row.get("state") == str(size) and row.get("op") == op]
                if values:
                    print(f"clipboard: {size} bytes {op}: n={len(values)} p50={median(values):.3f} ms p95={percentile(values, 0.95):.3f} ms")
        return code

    return _with_file(args, run)


# -- in-process checks --------------------------------------------------------------------------------------------------------------
def cmd_oracle(args: argparse.Namespace) -> int:
    rows = asyncio.run(_scenarios()["oracle"](_spec(args, 1, column=args.column)))
    bad = sum(int(row["mismatches"]) for row in rows)
    code = _finish("oracle", rows, Path(args.out), "mismatches", 1 if bad else 0)
    print(f"oracle: checks={sum(int(row['checks']) for row in rows)} mismatches={bad} (expected 0)")
    return code


def cmd_calllog(args: argparse.Namespace) -> int:
    rows = asyncio.run(_scenarios()["calllog"](_spec(args, 1)))
    bad = sum(1 for row in rows if not row["ok"])
    _append(Path(args.out), rows)
    row = rows[0]
    print(f"calllog: {row['calls']} calls, max decoded {row['max_decoded_chars']} chars (limit {row['limit']}), whole-row refusals {row['refusals']} -> {'FAIL' if bad else 'ok'}")
    return 1 if bad else 0


def cmd_thresholds(args: argparse.Namespace) -> int:
    listing = args.row_bytes if args.kind in ("wordwrap", "longrow") and args.row_bytes else args.sizes
    if not listing:
        print("thresholds: give --sizes (highlight, eager) or --row-bytes (wordwrap, longrow)", file=sys.stderr)
        return 2
    sizes = [int(item) for item in listing.split(",")]
    with tempfile.TemporaryDirectory() as directory:
        rows = run_thresholds(args.kind, sizes, args.width, args.runs, Path(directory), lambda spec: run_child("threshold", spec, args.timeout)[0])
    metric = {"highlight": "first_screen_ms", "wordwrap": "wrap_ms", "longrow": "render_ms", "eager": "first_screen_ms"}[args.kind]
    return _finish(f"thresholds {args.kind}", rows, Path(args.out), metric)


def cmd_summarise(args: argparse.Namespace) -> int:
    print(summarise_files([Path(item) for item in args.paths]))
    return 0


# -- command line -------------------------------------------------------------------------------------------------------------------
def _add_common(parser: argparse.ArgumentParser, *, wrap: bool, out: bool = True) -> None:
    parser.add_argument("--file", required=True)
    if wrap:
        parser.add_argument("--wrap", choices=("off", "on"), default="off")
    if out:
        parser.add_argument("--out", required=True)
    parser.add_argument("--config", choices=("auto", "default", "lowered"), default="auto", help="lazy thresholds; auto lowers them for files of at most 1 MiB")
    parser.add_argument("--timeout", type=float, default=900.0, help="seconds a child process may run and the scan may take")


def _add_measuring_parsers(sub: Any) -> None:
    p = sub.add_parser("first-screen", help="time to the first content of nova_edit on a pty")
    _add_common(p, wrap=False)
    p.add_argument("--cold", action="store_true", help="drop the page cache of the file before each run (verified with mincore)")
    p.add_argument("--runs", type=int, default=7)
    p.set_defaults(func=cmd_first_screen)
    p = sub.add_parser("memory", help="RssAnon, VmRSS and RssFile during the scripted scroll")
    _add_common(p, wrap=True)
    p.add_argument("--runs", type=int, default=3)
    p.set_defaults(func=cmd_memory)
    p = sub.add_parser("latency", help="key-to-render latency per step")
    _add_common(p, wrap=True)
    p.add_argument("--state", choices=("indexing", "indexed"), default="indexed")
    p.add_argument("--op", choices=_view_scenarios.LATENCY_OPS, default="down")
    p.add_argument("--steps", type=int, default=50)
    p.add_argument("--runs", type=int, default=1)
    p.add_argument("--profile", action="store_true", help="also write cProfile top 25 and per-step timings to <out>.profile.txt")
    p.set_defaults(func=cmd_latency)
    p = sub.add_parser("jump", help="End and far jump before and after the scan")
    _add_common(p, wrap=True)
    p.add_argument("--column", type=int, default=FAR)
    p.add_argument("--runs", type=int, default=3)
    p.add_argument("--resolve-timeout", type=float, default=120.0, help="seconds to wait for the cursor to become RESOLVED")
    p.set_defaults(func=cmd_jump)
    p = sub.add_parser("sweep-yield", help="pagedown/pageup latency while indexing per yield_seconds and scan_block")
    _add_common(p, wrap=False)
    p.add_argument("--values", default="0,0.0005,0.001,0.002,0.005")
    p.add_argument("--scan-block", default=str(DEFAULT_SCAN_BLOCK))
    p.add_argument("--steps", type=int, default=150)
    p.add_argument("--runs", type=int, default=3)
    p.set_defaults(func=cmd_sweep)


def _add_checking_parsers(sub: Any) -> None:
    p = sub.add_parser("oracle", help="strips and cursor x against an independent oracle")
    _add_common(p, wrap=False)
    p.add_argument("--column", type=int, default=FAR)
    p.set_defaults(func=cmd_oracle)
    p = sub.add_parser("calllog", help="open and scroll with the LazyDocument call log; fails on decodes above 8192 characters")
    _add_common(p, wrap=False)
    p.set_defaults(func=cmd_calllog)
    p = sub.add_parser("thresholds", help="synthetic measurements for the highlight, word wrap, long row and eager limits")
    p.add_argument("--kind", choices=("highlight", "wordwrap", "longrow", "eager"), required=True)
    p.add_argument("--sizes", default="")
    p.add_argument("--row-bytes", default="")
    p.add_argument("--width", type=int, default=113)
    p.add_argument("--runs", type=int, default=1)
    p.add_argument("--timeout", type=float, default=900.0, help="seconds one measurement process may run")
    p.add_argument("--out", required=True)
    p.set_defaults(func=cmd_thresholds)
    p = sub.add_parser("summarise", help="markdown tables (median, p95, max, count over 50 ms) of JSON lines files")
    p.add_argument("paths", nargs="+")
    p.set_defaults(func=cmd_summarise)
    p = sub.add_parser("child")  # internal
    p.add_argument("kind")
    p.add_argument("--spec", required=True)
    p.add_argument("--rows", required=True)
    p.add_argument("--dump-after", type=float, default=0.0)
    p.set_defaults(func=cmd_child)


def _add_edit_common(parser: argparse.ArgumentParser, *, wrap: str | None, file_required: bool = True, runs: int = 3) -> None:
    parser.add_argument("--file", required=file_required, default=None if file_required else "")
    if wrap is not None:
        parser.add_argument("--wrap", choices=("off", "on", "both"), default=wrap)
    parser.add_argument("--out", default="", help="JSON lines file; default <results>/<subcommand>-<file stem>[-<wrap>].jsonl")
    parser.add_argument("--results", default="", help="results directory of the default --out (default $RESULTS or the ACT4 directory)")
    parser.add_argument("--config", choices=("auto", "default", "lowered"), default="auto", help="lazy thresholds; auto lowers them for files of at most 1 MiB")
    parser.add_argument("--timeout", type=float, default=EDIT_TIMEOUT, help="seconds a child process may run and the scan may take")
    parser.add_argument("--runs", type=int, default=runs)


def _add_variant(parser: argparse.ArgumentParser) -> None:
    parser.add_argument("--variant", choices=("auto", "lines", "longline"), default="auto", help="lines: whole rows; longline: columns of the long row; auto: longline when row 0 is long")


def _add_edit_parsers(sub: Any) -> None:
    for name, helptext, runs in (
        ("edit-memory", "RssAnon through select, delete, undo, redo, copy, paste, undo, with window verification", 3),
        ("verify", "window hashes of the document against the file after every step of the edit-memory script", 1),
    ):
        p = sub.add_parser(name, help=helptext)
        _add_edit_common(p, wrap="off", runs=runs)
        _add_variant(p)
        p.add_argument("--select-bytes", type=int, default=0, help="bytes to select (default 1 GB, or 100 MB for the long row; always at most half of the document)")
        p.set_defaults(func=cmd_edit_memory if name == "edit-memory" else cmd_verify)
    p = sub.add_parser("edit-latency", help="typing, Backspace, Delete and Enter by direct injection")
    _add_edit_common(p, wrap="both")
    _add_variant(p)
    p.add_argument("--state", choices=("indexed", "indexing"), default="indexed", help="indexed: end of the file or far column; indexing: rows near the start while the scan runs")
    p.add_argument("--ops", default=",".join(_view_edit_latency.EDIT_OPS))
    p.add_argument("--steps", type=int, default=150)
    p.set_defaults(func=cmd_edit_latency)
    p = sub.add_parser("edit-scatter", help="the edit keys and cursor keys after 1,000 earlier scattered edits")
    _add_edit_common(p, wrap="both")
    _add_variant(p)
    p.add_argument("--scatter", type=int, default=1000)
    p.add_argument("--ops", default=",".join((*_view_edit_latency.EDIT_OPS, *_view_edit_latency.CURSOR_OPS)))
    p.add_argument("--steps", type=int, default=150)
    p.set_defaults(func=cmd_edit_scatter)
    p = sub.add_parser("segments", help="perf_counter time of _reestimate, _reconcile_cursor and LazyWrappedDocument._measure beside every step, without and with edits")
    _add_edit_common(p, wrap="on")
    _add_variant(p)
    p.add_argument("--ops", default=",".join(_view_edit_latency.EDIT_OPS))
    p.add_argument("--cursor-ops", default="ctrl+right,home,right,left")
    p.add_argument("--scatter", type=int, default=1000, help="earlier edits before the last variant (0 skips it)")
    p.add_argument("--steps", type=int, default=150)
    p.set_defaults(func=cmd_segments)
    p = sub.add_parser("pieces", help="memory per piece, cost of one splice and one row_range at several piece counts (no file)")
    p.add_argument("--sizes", default="1000,10000,100000,1000000")
    p.add_argument("--calls", type=int, default=200)
    p.add_argument("--memory-size", type=int, default=0, help="pieces for the memory per piece (default the largest of --sizes)")
    p.add_argument("--runs", type=int, default=1)
    p.add_argument("--out", default="")
    p.add_argument("--results", default="")
    p.add_argument("--timeout", type=float, default=EDIT_TIMEOUT)
    p.set_defaults(func=cmd_pieces, file="", wrap="off")
    p = sub.add_parser("undo-record", help="bytes per undo record for typing, Backspace, paste and delete (a synthetic file unless --file)")
    _add_edit_common(p, wrap=None, file_required=False, runs=1)
    p.set_defaults(config="default")
    p.add_argument("--count", type=int, default=1000, help="operations per kind")
    p.set_defaults(func=cmd_undo_record, wrap="off")
    p = sub.add_parser("clipboard", help="time of app.copy_to_clipboard plus the terminal write (a drained pty) per size")
    _add_edit_common(p, wrap=None, file_required=False, runs=1)
    p.set_defaults(config="default")
    p.add_argument("--sizes", default="65536,262144,1048576,4194304")
    p.add_argument("--calls", type=int, default=50)
    p.set_defaults(func=cmd_clipboard, wrap="off")


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(prog="tools.measure_view", description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    sub = parser.add_subparsers(dest="command", required=True)
    _add_measuring_parsers(sub)
    _add_checking_parsers(sub)
    _add_edit_parsers(sub)
    return parser


def main(argv: list[str] | None = None) -> int:
    """Run one subcommand and return its exit status."""
    args = build_parser().parse_args(argv)
    return int(args.func(args))


if __name__ == "__main__":
    sys.exit(main())
