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
  ACT5 (save) subcommands; `--out` defaults to `<results>/<subcommand>-<file stem>[-<wrap>].jsonl`, `--results` to `$RESULTS` or `$REFS/results/act5` (`$REFS`: `/tmp/scratchpad/s0001-refs`):
    uv run python -m tools.measure_view save-5g --file F [--target T] [--wrap {off,on}] [--chunk N] [--fsync-every N] [--scatter 1000] [--delete-bytes N] [--paste-bytes N] --runs N [--out O]
    uv run python -m tools.measure_view save-latency --file F [--target T] --wrap {off,on,both} [--steps 150] [--max-rounds N] --runs N [--out O]
    uv run python -m tools.measure_view save-sweep --file F [--target T] [--chunks 1048576,4194304] [--fsync-every 67108864,268435456,1073741824] [--no-index] [--out O]
    uv run python -m tools.measure_view save-longline --file F --copy C [--column 100000000] [--keep-copy] [--out O]
    uv run python -m tools.measure_view save-retention --file F --copy C [--delete-bytes N] [--keep-copy] [--out O]
    uv run python -m tools.measure_view save-records [--records 1,1000,10000] [--long-indexes 8] [--out O]
    uv run python -m tools.measure_view save-cancel --file F [--target T] [--cancel-at 0.5] [--out O]
    uv run python -m tools.measure_view save-fulldisk --file F [--target T] [--no-mount] [--tmpfs-bytes N] [--out O]

  ACT6 (search) subcommands; `--out` defaults to `<results>/<subcommand>-<file stem>[-<wrap>].jsonl`, `--results` to `$RESULTS` or `$REFS/results/act6`; they write nothing else under `$REFS`:
    uv run python -m tools.measure_view search-5g --file F [--needle N [--escapes]] [--case {sensitive,insensitive}] [--direction {forward,backward}] [--expect E] --runs N [--out O]
    uv run python -m tools.measure_view search-latency --file F [--case ...] --wrap {off,on,both} [--steps 150] [--max-rounds N] --runs N [--out O]
    uv run python -m tools.measure_view search-cancel --file F [--cancel-at 0.5] [--case ...] [--out O]
    uv run python -m tools.measure_view search-edited --file F [--scatter 1000] [--paste-bytes 100000000] [--out O]
    uv run python -m tools.measure_view search-longline --file F --copy C [--column 100000000] [--wrap {off,on,both}] [--steps 150] [--keep-copy] [--out O]
    uv run python -m tools.measure_view search-sweep --file F [--chunks 65536,262144,1048576] [--case ...] [--steps 40] [--out O]
    uv run python -m tools.measure_view search-gen --kind {ascii,nonascii} --size 1GiB [--seed N] --out FILE
    uv run python -m tools.measure_view search-fold [--runs N] [--out O]

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
    save-*: one child per run in a headless app; the parent samples `RssAnon` every 50 ms (`<out>.samples.jsonl`) and cuts the samples at the phase marks `writing`,
        `flushing`, `finishing` and `history` that the child prints when the first progress report of the next phase arrives (`save-window` is `pre_save` to the end of `history`).
        The edited document (1,000 scattered edits, a 1 GB delete, a paste) is saved as to `--target`; the output is verified by window hashes plus the length against
        a model computed from the original file and the edit offsets (never by reading the document). `--target` equal to `--file`, and any write under `$REFS` outside
        `results/act5`, is refused. `save-longline` and `save-retention` copy `--file` to `--copy` and save in place on the copy. `--no-index` runs `build_index=False`:
        the save ends with `SaveFailed` stage `internal` (no line index to rebase onto, Task 20 is conditional); the file is written and verified and the row says
        `index_after_rebase: false`. `save-fulldisk` tries a tmpfs mount and otherwise injects `ENOSPC` through `SaveIo` (the row says which: `method`).
    edit-latency, edit-scatter, segments: direct injection of keys (no Pilot), one child per run, `latency_ms` as for `latency`; edits count as a change when the document length changes.
    search-*: one child per run in a headless app, `RssAnon` sampled every 50 ms by the parent (`<out>.samples.jsonl`); `pre_search`/`search_end` (`pre_matrix`/`matrix_end` for the steps of
        the 200 MB line) mark the search, and the rows that name that window carry `rss_anon_mib_max` and `rss_anon_mib_delta` (peak minus the last sample before the search). Every row carries
        the machine facts and `file_kind`: `stand-in` for a file made by `search-gen` (its sidecar `<file>.search-gen.json` names kind and seed), `reference` for any other file. The steps of
        `search-latency` are the ACT5 steps except the refused typing key (typing is an edit, an edit cancels the search) and count only while the widget is searching; a finished search is
        started again until `--steps` rounds ran. `search-longline` plants the marker in the COPY `--copy` (never in the reference) and checks the exact selected text. `search-edited`
        checks offsets against the `ByteModel` of the scripted edits. `--out`, `--copy` and `search-gen --out` equal to a reference file, and any write under `$REFS` outside
        `results/act6`, are refused.
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
import shutil
import struct
import subprocess
import sys
import tempfile
import termios
import time
from collections.abc import Callable, Coroutine, Sequence
from pathlib import Path
from typing import Any

from nova_editor.core.save import CHUNK, FSYNC_EVERY
from nova_editor.core.search import CHUNK as SEARCH_CHUNK
from nova_editor.core.search import PROGRESS_INTERVAL
from tools import _view_edit, _view_edit_core, _view_edit_latency, _view_oracle, _view_save, _view_scenarios, _view_search, _view_thresholds
from tools._view_app import versions
from tools._view_procmem import COLD_RESIDENCY_LIMIT, KIB_PER_MIB, Supervised, drop_cache, kill_group, median, percentile, read_mem, read_rchar, resident_fraction, supervise
from tools._view_summary import summarise_files
from tools._view_thresholds import run_thresholds
from tools.gen_reference_files import parse_size

FAR = 100_000_000
DEFAULT_REFS = str(Path(tempfile.gettempdir()) / "scratchpad" / "s0001-refs")
DEFAULT_RESULTS = str(Path(DEFAULT_REFS) / "results" / "act4")
SAVE_RESULTS = Path("results") / "act5"
"""Where under `$REFS` the save measurements may write."""
SEARCH_RESULTS = Path("results") / "act6"
"""Where under `$REFS` the search measurements may write."""
BYTES_PER_GIB = 1 << 30
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
        "save": _view_save.save_scenario,
        "save-latency": _view_save.latency_scenario,
        "save-longline": _view_save.longline_scenario,
        "save-retention": _view_save.retention_scenario,
        "save-records": _view_save.records_scenario,
        "search-5g": _view_search.search_scenario,
        "search-latency": _view_search.latency_scenario,
        "search-cancel": _view_search.cancel_scenario,
        "search-edited": _view_search.edited_scenario,
        "search-longline": _view_search.longline_scenario,
        "search-fold": _view_search.fold_scenario,
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
def _out_path(args: argparse.Namespace, name: str, default: str = DEFAULT_RESULTS) -> Path:
    """`--out` when given, otherwise `<results>/<name>-<file stem>[-<wrap>].jsonl` with the results directory from `--results`, `$RESULTS` or `default` (the ACT4 directory)."""
    if args.out:
        return Path(args.out)
    results = Path(args.results or os.environ.get("RESULTS") or default)
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


# -- save subcommands (ACT5) --------------------------------------------------------------------------------------------------------
def _save_results() -> str:
    return str(Path(os.environ.get("REFS") or DEFAULT_REFS) / SAVE_RESULTS)


def _search_results() -> str:
    return str(Path(os.environ.get("REFS") or DEFAULT_REFS) / SEARCH_RESULTS)


def _scope(args: argparse.Namespace) -> tuple[str, Path]:
    """The default results directory and the directory under `$REFS` that a subcommand may write: the ACT6 ones for `search-*`, else the ACT5 ones."""
    if str(getattr(args, "command", "")).startswith("search-"):
        return _search_results(), SEARCH_RESULTS
    return _save_results(), SAVE_RESULTS


def _guard_write(path: Path, *references: Path, allowed: Path = SAVE_RESULTS) -> Path:
    """Return the resolved `path`, or exit when writing it would touch a reference file or `$REFS` outside `allowed` (`results/act5` for saves, `results/act6` for searches)."""
    resolved = Path(os.path.realpath(path))
    for reference in references:
        if resolved == Path(os.path.realpath(reference)) or (resolved.exists() and reference.exists() and resolved.samefile(reference)):
            msg = f"refusing to write {path}: it is the reference file {reference}"
            raise SystemExit(msg)
    refs = Path(os.path.realpath(os.environ.get("REFS") or DEFAULT_REFS))
    if resolved.is_relative_to(refs) and not resolved.is_relative_to(refs / allowed):
        msg = f"refusing to write {path}: under {refs} only {refs / allowed} may be written"
        raise SystemExit(msg)
    return resolved


def _require_free(directory: Path, gib: float) -> None:
    """Exit unless the file system of `directory` (or of its nearest existing parent) has `gib` GiB free."""
    existing = directory
    while not existing.exists() and existing != existing.parent:
        existing = existing.parent
    free = shutil.disk_usage(existing).free
    if free < gib * BYTES_PER_GIB:
        msg = f"only {free / BYTES_PER_GIB:.1f} GiB free on {existing}; need {gib:.1f} GiB (--min-free-gib)"
        raise SystemExit(msg)


def _save_target(args: argparse.Namespace) -> Path:
    """The save-as target: `--target` or `<results>/out-5g.txt`; guarded, its directory made, free space checked."""
    default = Path(args.results or os.environ.get("RESULTS") or _save_results()) / "out-5g.txt"
    target = _guard_write(Path(args.target) if args.target else default, Path(args.file))
    _guard_write(_out_path(args, args.command, _save_results()))
    target.parent.mkdir(parents=True, exist_ok=True)
    _require_free(target.parent, args.min_free_gib)
    return target


def _save_spec(args: argparse.Namespace, run: int, case: str, **extra: object) -> dict[str, Any]:
    return _spec(
        args,
        run,
        case=case,
        chunk=getattr(args, "chunk", CHUNK),
        fsync_every=getattr(args, "fsync_every", FSYNC_EVERY),
        scatter=getattr(args, "scatter", 1000),
        delete_bytes=getattr(args, "delete_bytes", 1_000_000_000),
        paste_bytes=getattr(args, "paste_bytes", 100_000_000),
        **extra,
    )


Annotate = Callable[[dict[str, Any], list[Row], Supervised, list[tuple[str, int]]], None]
"""Hook of `_save_one`: sees the spec, the rows of a run (child, phase and session rows), the supervised child and its `PHASE` marks, and may change the rows."""


def _save_one(args: argparse.Namespace, kind: str, spec: dict[str, Any], samples: list[Row], annotate: Annotate | None = None) -> tuple[list[Row], bool]:
    """Run one save child; return its rows plus one `phase` row per mark, the `save-window` row and a `session` row, and whether it ended as expected."""
    path = Path(spec["file"])
    case = str(spec["case"])
    child_rows, lines, result = run_child(kind, spec, args.timeout)
    done = "DONE" in lines
    marks = _marks(lines)
    common = {"status": result.status if result.status != "exit" or done else "exit_incomplete", "returncode": result.returncode}
    rows: list[Row] = [*child_rows]
    begin = result.t0_ns
    for name, end in marks:
        window = [sample for sample in result.samples if begin <= sample.t_ns <= end]
        last = window[-1].rss_anon_kb if window else None
        rows.append(_base(case, path, wrap=spec["wrap"], state=name, op="phase", run=spec["run"], **common, phase_ms=(end - begin) / 1e6, rss_anon_kb_last=last, **_figures(result.maxima(begin, end))))
        samples.extend(
            {"case": f"{case}-sample", "file": path.name, "wrap": spec["wrap"], "run": spec["run"], "phase": name, "t_ms": (sample.t_ns - result.t0_ns) / 1e6, "rss_anon_kb": sample.rss_anon_kb}
            for sample in window
        )
        begin = end
    start, stop = _view_save.start_mark(marks), _view_save.last_saved_phase(marks)
    if start is not None and stop is not None:
        rows.append(_base(case, path, wrap=spec["wrap"], state="save-window", op="phase", run=spec["run"], **common, phase_ms=(stop - start) / 1e6, **_figures(result.maxima(start, stop))))
    rows.append(
        _base(
            case,
            path,
            wrap=spec["wrap"],
            state="session",
            op="session",
            run=spec["run"],
            **common,
            phases=[name for name, _ in marks],
            stderr_tail=result.stderr_tail if not done else "",
            **_figures(result.maxima()),
        )
    )
    if annotate is not None:
        annotate(spec, rows, result, marks)
    ok = done and not any(row.get("ok") is False for row in child_rows)
    return rows, ok


def _save_finish(case: str, rows: list[Row], samples: list[Row], out: Path, failures: int) -> int:
    _append(out, [row for row in rows if row["state"] != "session"])
    _append(Path(f"{out}.samples.jsonl"), samples)
    peaks = [float(row["rss_anon_mib_max"]) for row in rows if row["state"] == "save-window" and "rss_anon_mib_max" in row]
    if peaks:
        print(f"{case}: RssAnon peak over the save window {max(peaks):.1f} MiB (series -> {out}.samples.jsonl)")
    bad = sum(_mismatches(rows))
    print(f"{case}: window mismatches {bad} (expected 0)")
    return _finish(case, [row for row in rows if row["state"] == "session"], out, "rss_anon_mib_max", failures + (1 if bad else 0))


def _save_loop(
    args: argparse.Namespace,
    case: str,
    kind: str,
    specs: Sequence[dict[str, Any]],
    *,
    before: Callable[[dict[str, Any]], None] | None = None,
    after: Callable[[dict[str, Any]], None] | None = None,
    annotate: Annotate | None = None,
) -> int:
    out = _out_path(args, case, _scope(args)[0])
    rows: list[Row] = []
    samples: list[Row] = []
    failures = 0
    for spec in specs:
        if before is not None:
            before(spec)
        try:
            run_rows, ok = _save_one(args, kind, spec, samples, annotate)
        finally:
            if after is not None:
                after(spec)
        rows += run_rows
        failures += 0 if ok else 1
    return _save_finish(case, rows, samples, out, failures)


def cmd_save_5g(args: argparse.Namespace) -> int:
    target = _save_target(args)
    specs = [_save_spec(args, run, "save-5g", target=str(target)) for run in range(1, args.runs + 1)]
    return _save_loop(args, "save-5g", "save", specs)


def cmd_save_sweep(args: argparse.Namespace) -> int:
    target = _save_target(args)
    chunks = [int(item) for item in args.chunks.split(",")]
    cadences = [int(item) for item in args.fsync_every.split(",")]
    specs = [
        {**_save_spec(args, run, "save-sweep", target=str(target)), "chunk": chunk, "fsync_every": cadence, "build_index": not args.no_index}
        for chunk in chunks
        for cadence in cadences
        for run in range(1, args.runs + 1)
    ]
    return _save_loop(args, "save-sweep", "save", specs)


def cmd_save_latency(args: argparse.Namespace) -> int:
    target = _save_target(args)
    out = _out_path(args, "save-latency", _save_results())
    rows: list[Row] = []
    failures = 0
    for wrap in _wraps(args):
        for run in range(1, args.runs + 1):
            spec = {**_save_spec(args, run, "save-latency", target=str(target), steps=args.steps, max_rounds=args.max_rounds), "wrap": wrap}
            run_rows, _lines, result = run_child("save-latency", spec, args.timeout)
            rows += run_rows
            failures += 0 if result.returncode == 0 and result.status == "exit" and not any(row.get("ok") is False for row in run_rows) else 1
    for row in rows:
        if row.get("op") == "summary":
            print(
                f"save-latency: wrap={row['wrap']} run={row['run']} steps during the save {row['steps_saving']}, longest {row['longest_step_ms']} ms ({row['longest_step_op']}), "
                f"plan calls {row['plan_calls']} (expected {row['plan_calls_expected']}) max {row['plan_ms_max']} ms"
            )
    return _finish("save-latency", rows, out, "latency_ms", failures)


def _copy_of(args: argparse.Namespace) -> Path:
    """Copy `--file` to `--copy` (guarded: never the reference, nothing under `$REFS` outside `results/act5`) and return the copy."""
    reference = Path(args.file)
    results, allowed = _scope(args)
    copy = _guard_write(Path(args.copy), reference, allowed=allowed)
    _guard_write(_out_path(args, args.command, results), reference, allowed=allowed)
    copy.parent.mkdir(parents=True, exist_ok=True)
    _require_free(copy.parent, args.min_free_gib + reference.stat().st_size / BYTES_PER_GIB)
    shutil.copyfile(reference, copy)
    return copy


def _copy_loop(args: argparse.Namespace, case: str, kind: str, extras: Sequence[dict[str, Any]]) -> int:
    reference = str(Path(args.file).resolve())
    copy = Path(args.copy)
    specs = [{**_save_spec(args, run, case, origin=reference, column=getattr(args, "column", 0)), **extra} for run in range(1, args.runs + 1) for extra in extras]

    def make(spec: dict[str, Any]) -> None:
        spec["file"] = str(_copy_of(args))

    def drop(_spec: dict[str, Any]) -> None:
        if not args.keep_copy:
            copy.unlink(missing_ok=True)

    return _save_loop(args, case, kind, specs, before=make, after=drop)


def cmd_save_longline(args: argparse.Namespace) -> int:
    return _copy_loop(args, "save-longline", "save-longline", [{}])


def cmd_save_retention(args: argparse.Namespace) -> int:
    return _copy_loop(args, "save-retention", "save-retention", [{"sequence": "undo-save"}, {"sequence": "save-undo"}])


def cmd_save_records(args: argparse.Namespace) -> int:
    out = _out_path(args, "save-records", _save_results())
    _guard_write(out)
    counts = [int(item) for item in args.records.split(",")]
    with tempfile.TemporaryDirectory() as directory:
        args.file = str(_view_save.write_records_file(Path(directory) / "records.txt", args.long_indexes))
        specs = [_spec(args, run, case="save-records", records=counts, long_indexes=args.long_indexes) for run in range(1, args.runs + 1)]
        return _run_many("save-records", "save-records", args, specs, "prepare_rebase_ms", out)


def cmd_save_cancel(args: argparse.Namespace) -> int:
    target = _save_target(args)
    specs = [_save_spec(args, run, "save-cancel", target=str(target), fault="cancel", cancel_at=args.cancel_at) for run in range(1, args.runs + 1)]
    return _save_loop(args, "save-cancel", "save", specs)


def _try_tmpfs(size: int) -> Path | None:
    """Mount a tmpfs of `size` bytes on a new directory when `mount` allows it (it needs root), otherwise return `None`."""
    directory = Path(tempfile.mkdtemp(prefix="nn-fulldisk-"))
    try:
        done = subprocess.run(["mount", "-t", "tmpfs", "-o", f"size={size}", "tmpfs", str(directory)], capture_output=True, timeout=30, check=False)
    except (OSError, subprocess.TimeoutExpired):
        done = None
    if done is not None and done.returncode == 0:
        return directory
    directory.rmdir()
    return None


def cmd_save_fulldisk(args: argparse.Namespace) -> int:
    mounted = None if args.no_mount else _try_tmpfs(args.tmpfs_bytes)
    method = "tmpfs" if mounted else "injection"
    print(f"save-fulldisk: method {method}" + ("" if mounted else " (no tmpfs could be mounted without root: ENOSPC is injected through SaveIo at the cancel-at fraction)"))
    try:
        target = (mounted / "out.txt") if mounted else _save_target(args)
        specs = [_save_spec(args, run, "save-fulldisk", target=str(target), fault="real-enospc" if mounted else "enospc", cancel_at=args.cancel_at, method=method) for run in range(1, args.runs + 1)]
        return _save_loop(args, "save-fulldisk", "save", specs)
    finally:
        if mounted:
            subprocess.run(["umount", str(mounted)], capture_output=True, timeout=30, check=False)
            mounted.rmdir()


# -- search subcommands (ACT6) ------------------------------------------------------------------------------------------------------
def _mib(value: float | None) -> str:
    return "-" if value is None else f"{value:.1f} MiB"


def _announce(row: Row) -> None:
    """Print one line for a search row (the figures a reader of the terminal wants first)."""
    name = f"{row['case']}: wrap={row['wrap']} run={row['run']}"
    if row["op"] == "search":
        print(
            f"{name} terminal={row['terminal']} {row['search_ms']:.1f} ms, {row.get('throughput_mb_s') or 0:.0f} MB/s, progress messages {row['progress_messages']} "
            f"({row.get('progress_per_s') or 0:.1f}/s), RssAnon max {_mib(row.get('rss_anon_mib_max'))} delta {_mib(row.get('rss_anon_mib_delta'))} ({row['file_kind']})"
        )
    elif row["op"] == "summary":
        print(
            f"{name} steps while searching {row['steps_searching']} of {row['steps_total']}, longest {row['longest_step_ms']} ms ({row['longest_step_op']}), "
            f"throughput {row.get('throughput_mb_s')} MB/s"
        )


def _annotate_search(spec: dict[str, Any], rows: list[Row], result: Supervised, marks: list[tuple[str, int]]) -> None:
    """Label every row with the machine facts and the file kind, add the `RssAnon` figures of the window each row names, and print the search rows."""
    _view_search.label_rows(spec, rows)
    for row in rows:
        window = row.pop("memory_window", None)
        if window:
            row.update(_view_search.search_memory(result, marks, window))
        _announce(row)


def _search_out(args: argparse.Namespace) -> Path:
    """The guarded `--out` (default under `$REFS/results/act6`): it is neither the reference file nor anything under `$REFS` outside `results/act6`."""
    return _guard_write(_out_path(args, args.command, _search_results()), Path(args.file), allowed=SEARCH_RESULTS)


def _search_spec(args: argparse.Namespace, run: int, case: str, **extra: object) -> dict[str, Any]:
    return _spec(
        args,
        run,
        case=case,
        search_chunk=args.chunk,
        progress_interval=args.progress_interval,
        case_sensitive=getattr(args, "case", "sensitive") == "sensitive",
        **extra,
    )


def _search_specs(args: argparse.Namespace, case: str, **extra: object) -> list[dict[str, Any]]:
    return [{**_search_spec(args, run, case, **extra), "wrap": wrap} for wrap in _wraps(args) for run in range(1, args.runs + 1)]


def _search_loop(args: argparse.Namespace, case: str, kind: str, specs: Sequence[dict[str, Any]], **hooks: Any) -> int:
    _search_out(args)
    return _save_loop(args, case, kind, specs, annotate=_annotate_search, **hooks)


def cmd_search_5g(args: argparse.Namespace) -> int:
    needle = args.needle.encode("latin-1", "backslashreplace").decode("unicode_escape") if args.escapes else args.needle
    expect = args.expect or ("not_found" if needle == _view_search.NO_NEEDLE else "any")
    return _search_loop(args, "search-5g", "search-5g", _search_specs(args, "search-5g", needle=needle, backward=args.direction == "backward", expect=expect))


def cmd_search_latency(args: argparse.Namespace) -> int:
    return _search_loop(args, "search-latency", "search-latency", _search_specs(args, "search-latency", steps=args.steps, max_rounds=args.max_rounds))


def cmd_search_sweep(args: argparse.Namespace) -> int:
    chunks = [int(item) for item in args.chunks.split(",")]
    specs = [{**spec, "search_chunk": chunk} for chunk in chunks for spec in _search_specs(args, "search-sweep", steps=args.steps, max_rounds=args.max_rounds, idle_search=True)]
    return _search_loop(args, "search-sweep", "search-latency", specs)


def cmd_search_cancel(args: argparse.Namespace) -> int:
    return _search_loop(args, "search-cancel", "search-cancel", _search_specs(args, "search-cancel", cancel_at=args.cancel_at))


def cmd_search_edited(args: argparse.Namespace) -> int:
    return _search_loop(args, "search-edited", "search-edited", _search_specs(args, "search-edited", scatter=args.scatter, paste_bytes=args.paste_bytes, seed=args.seed))


def cmd_search_longline(args: argparse.Namespace) -> int:
    reference = str(Path(args.file).resolve())
    copy = Path(args.copy)
    specs = _search_specs(args, "search-longline", origin=reference, column=args.column, marker=args.marker, steps=args.steps, max_rounds=args.max_rounds)

    def make(spec: dict[str, Any]) -> None:
        spec["file"] = str(_copy_of(args))
        spec.update(_view_search.plant_marker(copy, args.column, args.marker))

    def drop(_spec: dict[str, Any]) -> None:
        if not args.keep_copy:
            copy.unlink(missing_ok=True)

    return _search_loop(args, "search-longline", "search-longline", specs, before=make, after=drop)


def cmd_search_fold(args: argparse.Namespace) -> int:
    args.file = args.file or "casefold"
    specs = [_spec(args, run, case="search-fold", file_kind="none") for run in range(1, args.runs + 1)]
    return _search_loop(args, "search-fold", "search-fold", specs)


def cmd_search_gen(args: argparse.Namespace) -> int:
    out = _guard_write(Path(args.out), allowed=SEARCH_RESULTS)
    if out.exists() and not _view_search.sidecar_path(out).exists():
        msg = f"refusing to write {out}: it exists and was not made by search-gen"
        raise SystemExit(msg)
    out.parent.mkdir(parents=True, exist_ok=True)
    _require_free(out.parent, args.min_free_gib + args.size / BYTES_PER_GIB)
    row = _view_search.generate(out, args.kind, args.size, args.seed)
    print(json.dumps(row))
    print(
        f"search-gen: stand-in {args.kind} file {out} of {args.size} bytes (seed {args.seed}, crc32 {row['crc32']:08x}), {row['throughput_mb_s']:.0f} MB/s; marked by {_view_search.sidecar_path(out)}"
    )
    return 0


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


def _add_save_common(parser: argparse.ArgumentParser, *, wrap: str | None, file_required: bool = True, runs: int = 3) -> None:
    _add_edit_common(parser, wrap=wrap, file_required=file_required, runs=runs)
    parser.add_argument("--min-free-gib", type=float, default=11.0, help="GiB that must be free where the output goes (plus the size of a copy)")


def _add_save_edit_args(parser: argparse.ArgumentParser, *, target: bool = True) -> None:
    if target:
        parser.add_argument("--target", default="", help="the save-as target (default <results>/out-5g.txt); refused when it is a reference file")
    parser.add_argument("--scatter", type=int, default=1000, help="scattered single character edits before the save")
    parser.add_argument("--delete-bytes", type=int, default=1_000_000_000, help="bytes of the delete (clamped to half of the document)")
    parser.add_argument("--paste-bytes", type=int, default=100_000_000, help="bytes copied and pasted (clamped to a quarter of the document)")


def _add_save_parsers(sub: Any) -> None:
    p = sub.add_parser("save-5g", help="the edited save: RssAnon per phase, time per phase, throughput, progress messages, window verification of the output")
    _add_save_common(p, wrap="off")
    _add_save_edit_args(p)
    p.add_argument("--chunk", type=int, default=CHUNK)
    p.add_argument("--fsync-every", type=int, default=FSYNC_EVERY)
    p.set_defaults(func=cmd_save_5g)
    p = sub.add_parser("save-latency", help="cursor keys, page keys, End, Ctrl+End, scroll and a refused typing key while the edited save runs; plan lock hold times")
    _add_save_common(p, wrap="both")
    _add_save_edit_args(p)
    p.add_argument("--chunk", type=int, default=CHUNK)
    p.add_argument("--fsync-every", type=int, default=FSYNC_EVERY)
    p.add_argument("--steps", type=int, default=150, help="rounds of one step per op (more while the save still runs)")
    p.add_argument("--max-rounds", type=int, default=2000)
    p.set_defaults(func=cmd_save_latency)
    p = sub.add_parser("save-sweep", help="the edited save for chunk sizes and fsync cadences; --no-index runs build_index=False")
    _add_save_common(p, wrap="off", runs=1)
    _add_save_edit_args(p)
    p.add_argument("--chunks", default=f"{1 << 20},{4 << 20}")
    p.add_argument("--fsync-every", default=f"{64 << 20},{256 << 20},{1024 << 20}")
    p.add_argument("--no-index", action="store_true", help="build_index=False: the scan baseline (the rebase then has no line index)")
    p.set_defaults(func=cmd_save_sweep)
    for name, helptext, func in (
        ("save-longline", "save a copy of the 200 MB line file in place with the cursor at --column, then type there", cmd_save_longline),
        ("save-retention", "delete 1 GB, undo, save and delete 1 GB, save, undo (in place on a copy), with window hashes and the retained disk space", cmd_save_retention),
    ):
        p = sub.add_parser(name, help=helptext)
        _add_save_common(p, wrap="off", runs=1)
        _add_save_edit_args(p, target=False)
        p.add_argument("--copy", required=True, help="where the copy of --file is made (the reference is never opened for writing)")
        p.add_argument("--keep-copy", action="store_true")
        p.add_argument("--chunk", type=int, default=CHUNK)
        p.add_argument("--fsync-every", type=int, default=FSYNC_EVERY)
        p.add_argument("--column", type=int, default=FAR)
        p.set_defaults(func=func)
    p = sub.add_parser("save-records", help="prepare_rebase and apply_rebase times for N undo records and cached long row indexes (a synthetic file)")
    _add_edit_common(p, wrap=None, file_required=False, runs=1)
    p.set_defaults(config="lowered", wrap="off", func=cmd_save_records)
    p.add_argument("--records", default="1,1000,10000")
    p.add_argument("--long-indexes", type=int, default=8)
    for name, helptext, func in (
        ("save-cancel", "cancel the edited save at --cancel-at: time to the terminal message, temp file removed", cmd_save_cancel),
        ("save-fulldisk", "the edited save on a full disk (tmpfs when mount allows it, else ENOSPC injected at --cancel-at)", cmd_save_fulldisk),
    ):
        p = sub.add_parser(name, help=helptext)
        _add_save_common(p, wrap="off", runs=1)
        _add_save_edit_args(p)
        p.add_argument("--chunk", type=int, default=CHUNK)
        p.add_argument("--fsync-every", type=int, default=FSYNC_EVERY)
        p.add_argument("--cancel-at", type=float, default=0.5, help="fraction of the document written when the cancel or the failure happens")
        if name == "save-fulldisk":
            p.add_argument("--no-mount", action="store_true", help="do not try to mount a tmpfs")
            p.add_argument("--tmpfs-bytes", type=int, default=2 << 30)
        p.set_defaults(func=func)


def _add_search_common(parser: argparse.ArgumentParser, *, wrap: str | None, file_required: bool = True, runs: int = 3, case: bool = True) -> None:
    _add_edit_common(parser, wrap=wrap, file_required=file_required, runs=runs)
    parser.add_argument("--chunk", type=int, default=SEARCH_CHUNK, help="bytes of one search unit (SearchSettings.chunk)")
    parser.add_argument("--progress-interval", type=float, default=PROGRESS_INTERVAL, help="seconds between two progress reports of the search core")
    parser.add_argument("--min-free-gib", type=float, default=1.0, help="GiB that must be free where a copy or a generated file goes (plus its size)")
    if case:
        parser.add_argument("--case", choices=("sensitive", "insensitive"), default="sensitive")


def _add_search_steps(parser: argparse.ArgumentParser, steps: int) -> None:
    parser.add_argument("--steps", type=int, default=steps, help="rounds of one step per operation (a finished search is started again before the next step)")
    parser.add_argument("--max-rounds", type=int, default=2000)


def _add_search_parsers(sub: Any) -> None:
    p = sub.add_parser("search-5g", help="a full-circle search: time, throughput, progress messages, RssAnon and the terminal message (a miss unless --needle exists)")
    _add_search_common(p, wrap="off")
    p.add_argument("--needle", default=_view_search.NO_NEEDLE, help="default a needle that cannot occur (the generated files contain no @)")
    p.add_argument("--escapes", action="store_true", help="read backslash escapes in --needle (\\n, \\r\\n, \\u00e9)")
    p.add_argument("--direction", choices=("forward", "backward"), default="forward")
    p.add_argument("--expect", choices=("found", "not_found", "any"), default="", help="the terminal message that counts as ok (default not_found for the default needle, else found or not_found)")
    p.set_defaults(func=cmd_search_5g)
    p = sub.add_parser("search-latency", help="the ACT5 latency steps while a miss search runs; steps counted only while the widget is searching")
    _add_search_common(p, wrap="both")
    _add_search_steps(p, 150)
    p.set_defaults(func=cmd_search_latency)
    p = sub.add_parser("search-sweep", help="throughput and longest step per search chunk")
    _add_search_common(p, wrap="off", runs=1)
    _add_search_steps(p, 40)
    p.add_argument("--chunks", default=f"{1 << 16},{1 << 18},{1 << 20}")
    p.set_defaults(func=cmd_search_sweep)
    p = sub.add_parser("search-cancel", help="cancel a miss search at --cancel-at of its progress: time from cancel_search() to SearchCancelled")
    _add_search_common(p, wrap="off", runs=1)
    p.add_argument("--cancel-at", type=float, default=0.5)
    p.set_defaults(func=cmd_search_cancel)
    p = sub.add_parser("search-edited", help="1,000 scattered edits and a paste of known text; a needle in the paste and one across a piece boundary, offsets checked against ByteModel")
    _add_search_common(p, wrap="off", runs=1)
    p.add_argument("--scatter", type=int, default=1000)
    p.add_argument("--paste-bytes", type=int, default=100_000_000)
    p.add_argument("--seed", type=int, default=1, help="salt of the planted needles")
    p.set_defaults(func=cmd_search_edited)
    p = sub.add_parser("search-longline", help="a marker planted at --column of a COPY of the 200 MB line: exact selection, result-to-selection time, latency steps in both wrap modes")
    _add_search_common(p, wrap="both", runs=1)
    _add_search_steps(p, 150)
    p.add_argument("--copy", required=True, help="where the copy of --file is made and the marker planted (the reference is never opened for writing)")
    p.add_argument("--keep-copy", action="store_true")
    p.add_argument("--column", type=int, default=FAR, help="byte offset of the marker")
    p.add_argument("--marker", default="@@longline-marker@@")
    p.set_defaults(func=cmd_search_longline)
    p = sub.add_parser("search-gen", help="write a deterministic stand-in file of --size bytes (ascii, or accented, CJK and emoji items with CRLF lines) to --out")
    p.add_argument("--kind", choices=("ascii", "nonascii"), required=True)
    p.add_argument("--size", type=parse_size, default=parse_size("1GiB"), help="for example 1GiB, 1MiB, 4KiB")
    p.add_argument("--seed", type=int, default=1)
    p.add_argument("--out", required=True, help="the generated file (refused when it exists and was not made by search-gen)")
    p.add_argument("--min-free-gib", type=float, default=1.0, help="GiB that must stay free after the file is written")
    p.set_defaults(func=cmd_search_gen)
    p = sub.add_parser("search-fold", help="facts of the case folding table: class count, build time of build_variant_table, memory")
    _add_search_common(p, wrap=None, file_required=False, runs=1, case=False)
    p.set_defaults(func=cmd_search_fold)


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(prog="tools.measure_view", description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    sub = parser.add_subparsers(dest="command", required=True)
    _add_measuring_parsers(sub)
    _add_checking_parsers(sub)
    _add_edit_parsers(sub)
    _add_save_parsers(sub)
    _add_search_parsers(sub)
    return parser


def main(argv: list[str] | None = None) -> int:
    """Run one subcommand and return its exit status."""
    args = build_parser().parse_args(argv)
    return int(args.func(args))


if __name__ == "__main__":
    sys.exit(main())
