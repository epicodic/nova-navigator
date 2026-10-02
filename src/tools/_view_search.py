"""Search scenarios of the view benchmark harness (ACT6 design 11 and 13): the miss search, steps during a search, cancel, edited text, the 200 MB line, the chunk sweep, the fold table.

Every scenario runs in a headless app of its own process (`tools.measure_view child ...`) and returns raw JSON rows; the parent samples `RssAnon` every 50 ms.
The widget is driven through its public search API (`NovaTextArea.search`, `cancel_search`, `searching`, the `search_settings` class attribute and the `SearchProgress`,
`SearchFound`, `SearchNotFound`, `SearchCancelled` and `SearchFailed` messages); the harness only wraps methods of the running instance (as `SaveRunner` does) to see the core progress
reports (cancel at a fraction) and the instant the search thread hands over its result.

Every row says which file it used: `file_kind` is `stand-in` for a file made by `search-gen` (a sidecar `<file>.search-gen.json` names kind and seed) and `reference` for any other file,
and carries the machine facts (`python`, `textual`, `kernel`, `machine`, `cpus`).
The phase marks `PHASE <name> <ns>` bound the search (`pre_search`, `search_end`; `pre_matrix`, `matrix_end` for the steps of the 200 MB line); the parent turns the `RssAnon` samples between
them into `rss_anon_mib_baseline`, `rss_anon_mib_max` and `rss_anon_mib_delta` (peak minus the last sample before the search) on every row that names the window in `memory_window`.
The expected document of the edited text is the `ByteModel` of `tools._view_save`; a result is never verified by reading the document.
"""

from __future__ import annotations

import contextlib
import json
import os
import platform
import re
import time
import tracemalloc
import zlib
from collections import Counter
from collections.abc import AsyncIterator, Callable, Sequence
from dataclasses import dataclass
from importlib.metadata import version
from pathlib import Path
from typing import Any, NamedTuple

from nova_editor.core.casefold import ascii_table, build_variant_table
from nova_editor.core.search import CHUNK, PROGRESS_INTERVAL, SearchSettings
from nova_editor.widget import NovaTextArea
from tools._view_app import ProbeTextArea, emit, settle, wait_until
from tools._view_edit import place_at
from tools._view_procmem import KIB_PER_MIB, Supervised, median, percentile, read_mem
from tools._view_save import LATENCY_OPS, SLOW_STEP_MS, ByteModel, SaveApp, SaveSession, edit_row, literal, measure_op, open_session, scatter, settle_edits
from tools._view_scenarios import Row, Spec, base_row
from tools.gen_reference_files import _Rng

NO_NEEDLE = "@@no-such-needle@@"
"""A needle that cannot occur: the generated files contain no `@`."""
SEARCH_OPS = tuple(op for op in LATENCY_OPS if op != "x-refused")
"""The ACT5 latency steps minus the refused typing key: typing during a search is an edit, and an edit cancels the search (`text changed`)."""
SIDECAR_SUFFIX = ".search-gen.json"
GENERATOR = "search-gen-1"
BLOCK = 8 << 20
POOL_LINES = 4096
POOL_WORDS = 3000
CRLF_PER_MILLE = 250
DIGIT_PER_MILLE = 80
CAPITAL_PER_MILLE = 120
UPPER_PER_MILLE = 150
PUNCTUATION_PER_MILLE = 180
ITEM_PER_MILLE = 340
FIRST_ACCENT, LAST_ACCENT, DIVISION_SIGN = 0xE0, 0xFF, 0xF7
CJK_FIRST, CJK_LAST = 0x4E00, 0x9FFF
EMOJI_FIRST, EMOJI_LAST = 0x1F600, 0x1F64F
ACCENTED_ITEMS, CJK_ITEMS, EMOJI_ITEMS = 600, 600, 300
CONTINUATION_MASK, CONTINUATION = 0xC0, 0x80
ASCII_PUNCTUATION = ".,;:-_/()[]{}<>=+*&%$#!?~|"
PASTE_LINE = "paste filler line 0123456789 the quick brown fox jumps over the lazy dog\n"
SETTLE_AFTER_EDITS = 0.1
ROUND_SETTLE = 0.03
END_LIMIT = 30.0
BOUNDARY_TRIES = 40
BOUNDARY_STEP = 97
BOUNDARY_TAIL_BYTES = 16
BOUNDARY_TAIL_MIN = 4
MARKER_WINDOW = 16
MARKER_SLACK = 8
BUILDS = 3
MB = 1_000_000


# -- labels, machine facts ----------------------------------------------------------------------------------------------------------
def sidecar_path(path: Path) -> Path:
    """The file that marks `path` as made by `search-gen` (and names its kind, seed and size)."""
    return path.with_name(path.name + SIDECAR_SUFFIX)


def file_label(path: Path) -> Row:
    """`file_kind` `stand-in` (with `stand_in_kind` and `stand_in_seed`) for a generated file, `reference` for any other."""
    sidecar = sidecar_path(path)
    try:
        info = json.loads(sidecar.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return {"file_kind": "reference"}
    return {"file_kind": "stand-in", "stand_in_kind": info.get("kind"), "stand_in_seed": info.get("seed")}


def machine_facts() -> Row:
    """The machine and software facts every row carries."""
    return {"python": platform.python_version(), "textual": version("textual"), "kernel": platform.release(), "machine": platform.machine(), "cpus": os.cpu_count()}


def labels(spec: Spec) -> Row:
    """Machine facts and the file label of a spec (`file_kind` of the spec when it has one, else the label of `origin` or `file`)."""
    kind = {"file_kind": spec["file_kind"]} if "file_kind" in spec else file_label(Path(spec.get("origin") or spec["file"]))
    return {**machine_facts(), **kind}


def search_row(spec: Spec, *, case: str, state: str, op: str, **extra: object) -> Row:
    """A row with the identifying fields, the machine facts, the file label and the search settings."""
    settings = search_settings_of(spec)
    extra.setdefault("needle", str(spec.get("needle", NO_NEEDLE)))
    return base_row(spec, case=case, state=state, op=op, **labels(spec), search_chunk=settings.chunk, progress_interval=settings.progress_interval, **extra)


def label_rows(spec: Spec, rows: Sequence[Row]) -> None:
    """Add the machine facts, the file label and (when the spec has one) the needle to rows that lack them (the phase rows the parent builds, the edit rows of the save helpers)."""
    facts = {**labels(spec), **({"needle": str(spec["needle"])} if "needle" in spec else {})}
    for row in rows:
        for key, value in facts.items():
            row.setdefault(key, value)


# -- search-gen ---------------------------------------------------------------------------------------------------------------------
def _accents() -> list[str]:
    return [chr(code) for code in range(FIRST_ACCENT, LAST_ACCENT + 1) if code != DIVISION_SIGN]


def _items(rng: _Rng, words: list[str]) -> list[str]:
    """Non-ASCII items: accented words, CJK runs, emoji (single and joined), and the characters whose case folding is not simple (sharp s, Kelvin sign, long s)."""
    accents = _accents()
    items: list[str] = []
    for _ in range(ACCENTED_ITEMS):
        word = list(rng.pick(words))
        for _ in range(rng.between(1, 2)):
            word[rng.below(len(word))] = rng.pick(accents)
        items.append("".join(word))
    items += ["".join(chr(rng.between(CJK_FIRST, CJK_LAST)) for _ in range(rng.between(1, 5))) for _ in range(CJK_ITEMS)]
    items += ["".join(chr(rng.between(EMOJI_FIRST, EMOJI_LAST)) for _ in range(rng.between(1, 2))) for _ in range(EMOJI_ITEMS)]
    items += [chr(0x1F468) + chr(0x200D) + chr(0x1F469), "e" + chr(0x301), chr(0xDF), "stra" + chr(0xDF) + "e", chr(0x212A) + "elvin", chr(0x17F) + "ong", chr(0x130) + "stanbul"]
    return items


def _part(rng: _Rng, words: list[str], items: list[str]) -> str:
    """One word-like piece of a line: a number, a capitalised or upper case word, punctuation, a non-ASCII item (`nonascii` only) or a plain word."""
    roll = rng.below(1000)
    if roll < DIGIT_PER_MILLE:
        return str(rng.below(100_000))
    if roll < CAPITAL_PER_MILLE:
        return rng.pick(words).capitalize()
    if roll < UPPER_PER_MILLE:
        return rng.pick(words).upper()
    if roll < PUNCTUATION_PER_MILLE:
        return rng.pick(ASCII_PUNCTUATION)
    if items and roll < ITEM_PER_MILLE:
        return rng.pick(items)
    return rng.pick(words)


def build_pool(kind: str, rng: _Rng) -> list[bytes]:
    """The pool of finished lines (terminator included) the file is made from: `ascii` has only bytes below 0x80 and no `@`, `nonascii` adds the items of `_items` and CRLF lines."""
    words = ["".join(chr(ord("a") + rng.below(26)) for _ in range(rng.between(2, 11))) for _ in range(POOL_WORDS)]
    items = _items(rng, words) if kind == "nonascii" else []
    pool: list[bytes] = []
    for _ in range(POOL_LINES):
        text = " ".join(_part(rng, words, items) for _ in range(rng.between(3, 18)))
        terminator = b"\r\n" if items and rng.chance(CRLF_PER_MILLE) else b"\n"
        pool.append(text.encode("utf-8") + terminator)
    return pool


def _next_block(rng: _Rng, pool: list[bytes], longest: int, remaining: int) -> bytes:
    """The next bytes to write: a block of random lines while much is left, then whole lines and a filler line that end the file exactly at `remaining` bytes."""
    if remaining >= 2 * BLOCK:
        return b"".join([rng.pick(pool) for _ in range(BLOCK // longest)])
    lines: list[bytes] = []
    used = 0
    while True:
        line = rng.pick(pool)
        if used + len(line) > remaining:
            break
        lines.append(line)
        used += len(line)
    fill = remaining - used
    if fill:
        lines.append(b"x" * (fill - 1) + b"\n")
    return b"".join(lines)


def generate(path: Path, kind: str, size: int, seed: int) -> Row:
    """Write the deterministic stand-in file `path` of exactly `size` bytes (`kind` `ascii` or `nonascii`) and its sidecar; return the `search-gen` row.

    Lines come from a pool built from the seeded generator, so the file is written at disk speed. The file is written under a temporary name and moved into place.
    """
    rng = _Rng(seed)
    pool = build_pool(kind, rng)
    longest = max(len(line) for line in pool)
    partial = path.with_name(path.name + ".partial")
    crc = 0
    written = 0
    started = time.perf_counter()
    with partial.open("wb") as handle:
        while written < size:
            data = _next_block(rng, pool, longest, size - written)
            handle.write(data)
            crc = zlib.crc32(data, crc)
            written += len(data)
    seconds = time.perf_counter() - started
    partial.replace(path)
    info = {"stand_in": True, "kind": kind, "seed": seed, "size": size, "crc32": crc, "generator": GENERATOR, "python": platform.python_version()}
    sidecar_path(path).write_text(json.dumps(info) + "\n", encoding="utf-8")
    return {
        "case": "search-gen",
        "file": path.name,
        "wrap": "off",
        "state": "generated",
        "op": "gen",
        "run": 1,
        **machine_facts(),
        "file_kind": "stand-in",
        "stand_in_kind": kind,
        "stand_in_seed": seed,
        "bytes": size,
        "crc32": crc,
        "pool_lines": len(pool),
        "action_ms": seconds * 1000,
        "throughput_mb_s": size / seconds / MB if seconds > 0 else None,
    }


# -- the app and the session -------------------------------------------------------------------------------------------------------
class SearchEnd(NamedTuple):
    """A terminal message: `found`, `not_found`, `cancelled` or `failed`, the time it arrived and its payload."""

    kind: str
    at: float
    detail: object


class SearchApp(SaveApp):
    """`SaveApp` that also records the search messages of its widget with the time they arrived."""

    def __init__(self, widget: NovaTextArea) -> None:
        super().__init__(widget)
        self.search_progress: list[tuple[float, int, int, str]] = []
        self.ends: list[SearchEnd] = []

    def reset_search(self) -> None:
        """Forget the messages of an earlier search."""
        self.search_progress = []
        self.ends = []

    def on_nova_text_area_search_progress(self, message: NovaTextArea.SearchProgress) -> None:
        """Record a progress message."""
        self.search_progress.append((time.perf_counter(), message.done, message.total, message.phase))

    def on_nova_text_area_search_found(self, message: NovaTextArea.SearchFound) -> None:
        """Record the terminal message `SearchFound`."""
        self.ends.append(SearchEnd("found", time.perf_counter(), message))

    def on_nova_text_area_search_not_found(self, message: NovaTextArea.SearchNotFound) -> None:
        """Record the terminal message `SearchNotFound`."""
        self.ends.append(SearchEnd("not_found", time.perf_counter(), message.needle))

    def on_nova_text_area_search_cancelled(self, message: NovaTextArea.SearchCancelled) -> None:
        """Record the terminal message `SearchCancelled`."""
        self.ends.append(SearchEnd("cancelled", time.perf_counter(), message.reason))

    def on_nova_text_area_search_failed(self, message: NovaTextArea.SearchFailed) -> None:
        """Record the terminal message `SearchFailed`."""
        self.ends.append(SearchEnd("failed", time.perf_counter(), repr(message.error)))


def search_settings_of(spec: Spec) -> SearchSettings:
    """The `SearchSettings` of a spec (`search_chunk`, `progress_interval`; absent keys keep the production values)."""
    return SearchSettings(chunk=int(spec.get("search_chunk") or CHUNK), progress_interval=float(spec.get("progress_interval", PROGRESS_INTERVAL)))


@contextlib.asynccontextmanager
async def open_search(spec: Spec) -> AsyncIterator[SaveSession]:
    """Open the file of `spec` in a headless app that records search messages, wait until the line scan is complete and yield the session."""
    ProbeTextArea.search_settings = search_settings_of(spec)
    try:
        async with open_session(spec, SearchApp) as session:
            yield session
    finally:
        ProbeTextArea.search_settings = SearchSettings()


def app_of(session: SaveSession) -> SearchApp:
    """The `SearchApp` of a session made by `open_search`."""
    app = session.app
    if not isinstance(app, SearchApp):
        msg = "the session was not opened by open_search"
        raise TypeError(msg)
    return app


def start_search(session: SaveSession, needle: str, *, case_sensitive: bool, backward: bool = False) -> float:
    """Start a full-circle search and return the time it started.

    Raises:
        RuntimeError: The widget did not start the search.
    """
    started = time.perf_counter()
    if not session.area.search(needle, backward=backward, case_sensitive=case_sensitive, wrap=True):
        msg = "the widget did not start the search"
        raise RuntimeError(msg)
    return started


async def await_ends(session: SaveSession, count: int) -> None:
    """Wait until `count` terminal messages arrived and the widget is no longer searching.

    Raises:
        RuntimeError: The search did not end within the time limit of the spec.
    """
    app, area = app_of(session), session.area
    if not await wait_until(lambda: len(app.ends) >= count and not area.searching, session.limit):
        msg = "the search did not end"
        raise RuntimeError(msg)


def outcome_fields(app: SearchApp, started: float, length: int) -> Row:
    """What the messages of one search say: terminal, time, throughput, progress messages and the match or the reason."""
    end = app.ends[-1]
    seconds = end.at - started
    last = app.search_progress[-1] if app.search_progress else None
    searched = length if end.kind == "not_found" else (last[1] if last else None)
    stamps = [stamp for stamp, *_ in app.search_progress]
    span = stamps[-1] - stamps[0] if len(stamps) > 1 else 0.0
    dones = [done for _stamp, done, _total, _phase in app.search_progress]
    fields: Row = {
        "terminal": "+".join(item.kind for item in app.ends),
        "terminals_posted": len(app.ends),
        "search_ms": seconds * 1000,
        "action_ms": seconds * 1000,
        "doc_length": length,
        "searched_bytes": searched,
        "throughput_mb_s": searched / seconds / MB if searched and seconds > 0 else None,
        "throughput_mib_s": searched / seconds / (1 << 20) if searched and seconds > 0 else None,
        "progress_messages": len(app.search_progress),
        "progress_per_s": len(app.search_progress) / seconds if seconds > 0 else None,
        "progress_span_per_s": (len(stamps) - 1) / span if span > 0 else None,
        "progress_monotonic": dones == sorted(dones),
        "progress_last_done": last[1] if last else None,
        "progress_total": last[2] if last else None,
    }
    if end.kind == "found":
        found = end.detail
        if isinstance(found, NovaTextArea.SearchFound):
            fields.update(found_start=found.start, found_end=found.end, found_row=found.row, found_column=found.column, found_wrapped=found.wrapped)
    elif end.kind == "cancelled":
        fields["cancel_reason"] = end.detail
    elif end.kind == "failed":
        fields["error"] = end.detail
    return fields


def terminal_ok(expect: str, terminal: str) -> bool:
    """Whether a terminal message is the one that was expected (`found`, `not_found`, or `any` of those two)."""
    return terminal in ("found", "not_found") if expect == "any" else terminal == expect


async def search_once(session: SaveSession, spec: Spec, *, case: str, state: str, mark: str | None, needle: str | None = None, **extra: object) -> Row:
    """One search of `needle` (default the spec's), awaited to its terminal message, as one `search` row.

    `mark` names the memory window (`PHASE pre_<mark>` before the start, `<mark>_end` after the end); the row says so in `memory_window`.
    """
    app, area = app_of(session), session.area
    needle = str(spec["needle"]) if needle is None else needle
    case_sensitive = bool(spec.get("case_sensitive", True))
    backward = bool(spec.get("backward", False))
    app.reset_search()
    length = session.document.length
    if mark:
        emit(f"PHASE pre_{mark} {time.perf_counter_ns()}")
    started = start_search(session, needle, case_sensitive=case_sensitive, backward=backward)
    await await_ends(session, 1)
    if mark:
        emit(f"PHASE {mark}_end {time.perf_counter_ns()}")
    fields = outcome_fields(app, started, length)
    expect = str(spec.get("expect", "any"))
    return search_row(
        spec,
        case=case,
        state=state,
        op="search",
        needle=needle,
        case_sensitive=case_sensitive,
        backward=backward,
        expect=expect,
        ok=terminal_ok(expect, str(fields["terminal"])) and not area.searching,
        memory_window=mark,
        **fields,
        **extra,
    )


async def search_scenario(spec: Spec) -> list[Row]:
    """`search-5g` child: the file is open and its line scan complete; one full-circle search of `spec["needle"]` (default a needle that cannot occur).

    The row records time, throughput, progress messages and rate, the terminal message and the match; the parent adds the `RssAnon` figures.
    With a needle that exists the time is the first-result latency (the time to the terminal message `found`).
    """
    async with open_search(spec) as session:
        row = await search_once(session, spec, case=str(spec.get("case", "search-5g")), state="search", mark="search")
        emit("DONE")
        return [row]


# -- steps during a search ------------------------------------------------------------------------------------------------------------
async def _step(session: SaveSession, spec: Spec, case: str, op: str, index: int, search_index: int) -> Row:
    area = session.area
    before = area.searching
    measured = await measure_op(app_of(session), area, session.document, op, index)
    after = area.searching
    state = "searching" if before and after else "idle"
    return search_row(spec, case=case, state=state, op=op, step=index, search_index=search_index, searching_before=before, searching_after=after, **measured)


def _summary(session: SaveSession, spec: Spec, case: str, steps: list[Row], starts: list[float], rounds: int, mark: str) -> Row:
    app = app_of(session)
    during = [row for row in steps if row["state"] == "searching" and row["latency_ms"] is not None]
    values = [float(row["latency_ms"]) for row in during]
    longest = max(during, key=lambda row: row["latency_ms"], default=None)
    length = session.document.length
    seconds = [end.at - start for end, start in zip(app.ends, starts, strict=False) if end.kind == "not_found"]
    typical = median(seconds) if seconds else None
    return search_row(
        spec,
        case=case,
        state="summary",
        op="summary",
        needle=str(spec.get("needle", NO_NEEDLE)),
        case_sensitive=bool(spec.get("case_sensitive", True)),
        steps_total=len(steps),
        steps_searching=sum(1 for row in steps if row["state"] == "searching"),
        steps_idle=sum(1 for row in steps if row["state"] == "idle"),
        steps_noop=sum(1 for row in steps if row.get("noop")),
        steps_over_50ms=sum(1 for value in values if value > SLOW_STEP_MS),
        rounds=rounds,
        searches_started=len(starts),
        terminals=dict(Counter(end.kind for end in app.ends)),
        longest_step_ms=None if longest is None else longest["latency_ms"],
        longest_step_op=None if longest is None else longest["op"],
        latency_ms_p50=percentile(values, 0.5) if values else None,
        latency_ms_p95=percentile(values, 0.95) if values else None,
        latency_ms_p99=percentile(values, 0.99) if values else None,
        latency_ms_max=max(values, default=None),
        doc_length=length,
        search_ms_median=None if typical is None else typical * 1000,
        throughput_mb_s=None if not typical else length / typical / MB,
        memory_window=mark,
    )


async def search_matrix(session: SaveSession, spec: Spec, *, case: str, mark: str) -> list[Row]:
    """The ACT5 latency steps (`SEARCH_OPS`) in `spec["steps"]` rounds while a miss search runs; a finished search is started again before the next step, so the matrix covers the search period.

    Rows: one per step (`state` `searching` when the widget searched before and after the step, else `idle`; only `searching` steps are latency samples of the search period),
    then one `summary` row (step counts, longest step and its operation, nearest-rank percentiles, searches started, terminal messages, throughput of the finished searches).
    A search still running at the end is cancelled.
    """
    app, area = app_of(session), session.area
    steps, limit = int(spec["steps"]), int(spec.get("max_rounds", 2000))
    needle = str(spec.get("needle", NO_NEEDLE))
    case_sensitive = bool(spec.get("case_sensitive", True))
    rows: list[Row] = []
    starts: list[float] = []
    rounds = 0
    app.reset_search()
    emit(f"PHASE pre_{mark} {time.perf_counter_ns()}")
    for index in range(limit):
        if rounds >= steps:
            break
        rounds += 1
        for op in SEARCH_OPS:
            if not area.searching:
                starts.append(start_search(session, needle, case_sensitive=case_sensitive))
            rows.append(await _step(session, spec, case, op, index, len(starts)))
            await settle(ROUND_SETTLE)
    if area.searching:
        area.cancel_search()
    await await_ends(session, len(starts))
    emit(f"PHASE {mark}_end {time.perf_counter_ns()}")
    return [*rows, _summary(session, spec, case, rows, starts, rounds, mark)]


async def latency_scenario(spec: Spec) -> list[Row]:
    """`search-latency` and `search-sweep` child: a miss search runs while the cursor and page keys, End, Ctrl+End, Ctrl+Home and vertical scroll are injected.

    With `idle_search` a search without steps comes first (its throughput is free of the steps). `search_chunk` sets the chunk of the search.
    """
    async with open_search(spec) as session:
        document = session.document
        rows: list[Row] = []
        case = str(spec.get("case", "search-latency"))
        if spec.get("idle_search"):
            rows.append(await search_once(session, {**spec, "expect": "not_found"}, case=case, state="idle", mark=None, needle=str(spec.get("needle", NO_NEEDLE))))
        session.area.move_cursor((min(100, document.line_count - 1), 0))
        await settle(0.2)
        rows += await search_matrix(session, spec, case=case, mark="search")
        emit("DONE")
        return rows


# -- cancel -------------------------------------------------------------------------------------------------------------------------
class CancelTrigger:
    """Cancels the search once a core progress report says that `fraction` of the owned bytes are done (the report arrives on the search thread)."""

    def __init__(self, area: NovaTextArea, fraction: float) -> None:
        self.area = area
        self.fraction = fraction
        self.fired = False
        self.t_cancel = 0.0
        self.seen = 0.0
        original = area._on_search_report
        vars(area)["_on_search_report"] = lambda run, report: self._report(original, run, report)

    def _report(self, original: Callable[[Any, Any], None], run: Any, report: Any) -> None:
        if not self.fired and report.total and report.done / report.total >= self.fraction:
            self.fired = True
            self.seen = report.done / report.total
            self.t_cancel = time.perf_counter()
            self.area.cancel_search()
        original(run, report)


async def cancel_scenario(spec: Spec) -> list[Row]:
    """`search-cancel` child: a miss search that is cancelled at `cancel_at` of its progress; the row records the time from `cancel_search()` to the terminal `SearchCancelled`."""
    async with open_search(spec) as session:
        trigger = CancelTrigger(session.area, float(spec.get("cancel_at", 0.5)))
        row = await search_once(session, {**spec, "expect": "cancelled"}, case="search-cancel", state="cancel", mark="search", needle=str(spec.get("needle", NO_NEEDLE)))
        row.update(
            cancel_at=float(spec.get("cancel_at", 0.5)),
            cancel_issued=trigger.fired,
            cancel_progress_fraction=trigger.seen if trigger.fired else None,
            cancel_latency_ms=(app_of(session).ends[-1].at - trigger.t_cancel) * 1000 if trigger.fired else None,
        )
        emit("DONE")
        return [row]


# -- edited text --------------------------------------------------------------------------------------------------------------------
@dataclass(frozen=True)
class Planted:
    """A needle planted by an edit: where the harness put it (`start` in the expected document) and which kind of edit did."""

    target: str
    needle: str
    start: int


def _following_text(model: ByteModel, at: int) -> str:
    """The text of the expected document that follows byte `at` up to the end of its row (the longest strictly valid UTF-8 prefix of a few bytes)."""
    raw = model.read(at, BOUNDARY_TAIL_BYTES)
    for end in range(len(raw), 0, -1):
        try:
            text = raw[:end].decode("utf-8")
        except UnicodeDecodeError:
            continue
        return re.split(r"[\r\n]", text, maxsplit=1)[0]
    return ""


async def plant_boundary(session: SaveSession) -> Planted:
    """Insert `@@bd-<seed>@@` at the start of a piece of original text: the needle is the insert plus the original text after it, so it spans a piece boundary."""
    spec, document, area, model = session.spec, session.document, session.area, session.model
    marker = f"@@bd-{spec.get('seed', 1)}@@"
    for attempt in range(BOUNDARY_TRIES):
        location, byte = place_at(document, document.length // 2 + attempt * BOUNDARY_STEP)
        text = _following_text(model, byte)
        if len(text) >= BOUNDARY_TAIL_MIN:
            break
    else:
        msg = "no row near the middle of the document has a text to span"
        raise RuntimeError(msg)
    started = time.perf_counter()
    area.insert(marker, location)
    elapsed = (time.perf_counter() - started) * 1000
    model.insert(byte, literal(marker.encode()))
    edit_row(session, "boundary", elapsed, bytes=len(marker))
    await settle_edits(session)
    return Planted("boundary", marker + text, byte)


async def plant_paste(session: SaveSession, size: int) -> Planted:
    """Paste about `size` bytes of known ASCII text with `@@paste-<seed>@@` in its middle at three quarters of the document."""
    spec, document, area, model = session.spec, session.document, session.area, session.model
    marker = f"@@paste-{spec.get('seed', 1)}@@"
    half = max(0, (size - len(marker)) // 2)
    filler = (PASTE_LINE * (half // len(PASTE_LINE) + 1))[:half]
    text = filler + marker + filler
    location, byte = place_at(document, document.length * 3 // 4)
    area.move_cursor(location)
    await settle(SETTLE_AFTER_EDITS)
    started = time.perf_counter()
    area.insert(text, location)
    elapsed = (time.perf_counter() - started) * 1000
    model.insert(byte, literal(text.encode()))
    edit_row(session, "paste", elapsed, bytes=len(text))
    await settle_edits(session)
    return Planted("paste", marker, byte + len(filler))


async def edited_scenario(spec: Spec) -> list[Row]:
    """`search-edited` child: `scatter` single character edits, a needle planted across a piece boundary and a paste of `paste_bytes` of known text; two searches from the start.

    The searches look for the needle inside the paste and for the one spanning the boundary. The offsets of the result are checked against the `ByteModel` that the scripted edits
    changed with the same offset arithmetic (`offset_ok`: start and end are where the harness put the needle, `bytes_ok`: the model has the needle's bytes there); the document is not read.
    """
    async with open_search(spec) as session:
        await scatter(session, int(spec.get("scatter", 1000)))
        boundary = await plant_boundary(session)
        paste = await plant_paste(session, int(spec.get("paste_bytes", 100_000_000)))
        emit(f"PHASE edits {time.perf_counter_ns()}")
        rows: list[Row] = []
        for planted in (paste, boundary):
            session.area.move_cursor((0, 0))
            await settle(SETTLE_AFTER_EDITS)
            row = await search_once(
                session, {**spec, "needle": planted.needle, "expect": "found"}, case="search-edited", state="edited", mark=None, target=planted.target, needle_bytes=len(planted.needle.encode())
            )
            size = len(planted.needle.encode())
            offset_ok = row.get("found_start") == planted.start and row.get("found_end") == planted.start + size
            bytes_ok = session.model.read(planted.start, size) == planted.needle.encode()
            row.update(expected_start=planted.start, offset_ok=offset_ok, bytes_ok=bytes_ok, ok=bool(row["ok"] and offset_ok and bytes_ok))
            rows.append(row)
        for row in session.rows:
            if row["case"] == "save-edits":
                row["case"] = "search-edited"
        emit("DONE")
        return [*session.rows, *rows]


# -- the 200 MB line ----------------------------------------------------------------------------------------------------------------
def plant_marker(path: Path, column: int, marker: str) -> Row:
    """Overwrite the bytes of the COPY `path` at byte `column` (moved to the next character start) with `marker`, padded with dots to the end of a whole character.

    The length of the file does not change, so every offset stays valid; returns `expected_start` and `expected_end` (the marker's range) and `planted_bytes`.
    The caller passes the copy, never the reference file.
    """
    data = marker.encode("ascii")
    fd = os.open(path, os.O_RDWR)
    try:
        size = os.fstat(fd).st_size
        at = max(0, min(column, size - len(data) - MARKER_SLACK - MARKER_WINDOW))
        window = os.pread(fd, MARKER_WINDOW + len(data) + MARKER_SLACK, at)
        start = next((i for i, byte in enumerate(window) if byte & CONTINUATION_MASK != CONTINUATION), 0)
        end = start + len(data)
        while end < len(window) and window[end] & CONTINUATION_MASK == CONTINUATION:
            end += 1
        payload = data + b"." * (end - start - len(data))
        os.pwrite(fd, payload, at + start)
    finally:
        os.close(fd)
    return {"expected_start": at + start, "expected_end": at + start + len(data), "planted_bytes": len(payload)}


async def longline_scenario(spec: Spec) -> list[Row]:
    """`search-longline` child, on a copy that has the marker planted: search for the marker from the start and measure the time from the result to the resolved selection.

    The row records the exact selected text, the found range against the planted one and `result_to_selection_ms` (the search thread hands over its result, the widget posts `SearchFound` once
    both ends of the match are exact). Then the latency steps run with the cursor at the match (column `column`) while a miss search runs, one `summary` row (the wrap mode is the spec's).
    """
    async with open_search(spec) as session:
        area, app = session.area, app_of(session)
        marker = str(spec["marker"])
        handed: list[float] = []
        original = area._post_search_outcome

        def post(run: Any, outcome: Any) -> None:
            handed.append(time.perf_counter())
            original(run, outcome)

        vars(area)["_post_search_outcome"] = post
        row = await search_once(session, {**spec, "expect": "found"}, case="search-longline", state="marker", mark="search", needle=marker)
        end = app.ends[-1]
        selected = area.selected_text if end.kind == "found" else None
        start_ok = row.get("found_start") == spec["expected_start"] and row.get("found_end") == spec["expected_end"]
        row.update(
            expected_start=spec["expected_start"],
            expected_end=spec["expected_end"],
            start_ok=start_ok,
            selected_text=selected,
            selection_ok=selected == marker,
            result_to_selection_ms=(end.at - handed[-1]) * 1000 if end.kind == "found" and handed else None,
            planted=True,
            column=int(spec["column"]),
            cursor_state=area.cursor_state.name,
            ok=bool(row["ok"] and start_ok and selected == marker),
        )
        await settle(0.5)
        matrix = await search_matrix(session, {**spec, "needle": NO_NEEDLE}, case="search-longline", mark="matrix")
        emit("DONE")
        return [row, *matrix]


# -- the fold table -----------------------------------------------------------------------------------------------------------------
async def fold_scenario(spec: Spec) -> list[Row]:
    """`search-fold` child: the facts of the case folding table (class count, entries, build time of `build_variant_table`, memory by `RssAnon` and by `tracemalloc`)."""
    rss_before = read_mem("self").rss_anon_kb
    times: list[float] = []
    table: dict[str, tuple[str, ...]] = {}
    for _ in range(BUILDS):
        started = time.perf_counter()
        table = build_variant_table()
        times.append((time.perf_counter() - started) * 1000)
    rss_after = read_mem("self").rss_anon_kb
    tracemalloc.start()
    traced = build_variant_table()
    current, peak = tracemalloc.get_traced_memory()
    tracemalloc.stop()
    classes = set(table.values())
    row = base_row(
        spec,
        case="search-fold",
        state="fold",
        op="fold",
        **machine_facts(),
        file_kind="none",
        class_count=len(classes),
        entries=len(table),
        largest_class=max((len(item) for item in classes), default=0),
        ascii_entries=len(ascii_table()),
        build_ms=median(times),
        build_ms_all=times,
        table_kib_tracemalloc=current / 1024,
        build_peak_kib_tracemalloc=peak / 1024,
        same_table=traced == table,
        rss_anon_mib_baseline=rss_before / KIB_PER_MIB,
        rss_anon_mib_max=rss_after / KIB_PER_MIB,
        rss_anon_mib_delta=(rss_after - rss_before) / KIB_PER_MIB,
        action_ms=median(times),
    )
    emit("DONE")
    return [row]


# -- parent side --------------------------------------------------------------------------------------------------------------------
def search_memory(result: Supervised, marks: Sequence[tuple[str, int]], window: str) -> Row:
    """`RssAnon` over the memory window `window` (`pre_<window>` to `<window>_end`): the last sample before the start is the baseline, the delta is the peak minus the baseline.

    A window shorter than the sampling interval holds no sample; the first sample after it then stands for the window (`rss_samples_in_window` says how many samples were inside).
    """
    starts = [stamp for name, stamp in marks if name == f"pre_{window}"]
    stops = [stamp for name, stamp in marks if name == f"{window}_end"]
    if not starts or not stops or not result.samples:
        return {"rss_samples_in_window": 0}
    start, stop = starts[0], stops[0]
    before = [sample for sample in result.samples if sample.t_ns <= start]
    inside = [sample for sample in result.samples if start <= sample.t_ns <= stop]
    after = [sample for sample in result.samples if sample.t_ns > stop][:1]
    chosen = inside or after
    base = before[-1] if before else (chosen[0] if chosen else result.samples[0])
    peak = max([base.rss_anon_kb, *(sample.rss_anon_kb for sample in chosen)])
    return {
        "rss_anon_mib_baseline": base.rss_anon_kb / KIB_PER_MIB,
        "rss_anon_mib_max": peak / KIB_PER_MIB,
        "rss_anon_mib_delta": (peak - base.rss_anon_kb) / KIB_PER_MIB,
        "rss_anon_mib_session_max": max(sample.rss_anon_kb for sample in result.samples) / KIB_PER_MIB,
        "rss_samples_in_window": len(inside),
    }
