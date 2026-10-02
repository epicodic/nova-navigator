"""Save scenarios of the view benchmark harness (ACT5 design 12 and 15): the edited save, steps during a save, the 200 MB line, retention, rebase records, cancel and full disk.

Every scenario runs in a headless app of its own process (`tools.measure_view child ...`) and returns raw JSON rows; the parent samples `RssAnon` every 50 ms.
The widget is driven through its public save API (`NovaTextArea.save`, `cancel_save`, the `SaveProgress`, `Saved`, `SaveFailed` and `SaveCancelled` messages and
the `save_settings` and `save_io` class attributes); the harness only wraps methods of the running instances (`SaveRunner`), no class of `nova_editor` changes.

The output of a save is never verified by reading the document: `ByteModel` is the expected document, a list of runs of the original file and literal bytes
changed with the same offset arithmetic as the scripted edits, and the saved file is compared with it by window hashes plus the length.
The phase marks `PHASE <name> <ns>` mark the end of `writing`, `flushing`, `finishing` and `history` (the parent attributes `RssAnon` samples to them) and `pre_save`
marks the instant before the save starts.
"""

from __future__ import annotations

import asyncio
import contextlib
import errno
import hashlib
import math
import os
import shutil
import tempfile
import time
from collections.abc import AsyncIterator, Callable
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, NamedTuple

from nova_editor.core.save import SaveIo, SaveSettings
from nova_editor.document._document import Location, Selection
from nova_editor.document._lazy_document import LazyDocument
from nova_editor.widget import NovaTextArea
from tools._view_app import SIZE, ProbeApp, ProbeTextArea, emit, lazy_document, scan_busy, send_key, settle, timed, wait_until
from tools._view_edit import WINDOW, edit_state, place_at, window_offsets
from tools._view_procmem import percentile
from tools._view_scenarios import Row, Spec, _first_content, _open, base_row

PHASES = ("writing", "flushing", "finishing", "history")
"""The phases of a save in order (`NovaTextArea.SaveProgress.phase`)."""
LATENCY_OPS = ("x-refused", "down", "pagedown", "pageup", "up", "right", "left", "end", "home", "ctrl+end", "ctrl+home", "vscroll")
"""The steps of the latency rounds: a refused typing key first (it is only sent while the save runs), cursor and page keys, End, Ctrl+End, vertical scroll."""
JUMP_OPS = ("ctrl+end", "ctrl+home")
"""The widget binds no Ctrl+End or Ctrl+Home, so these steps call the cursor jump (`move_cursor` to the end or the start of the document) that the keys would make."""
STEP_LIMIT = 0.5
"""Seconds a latency step may take before it is recorded without time."""
REFUSE_LIMIT = 1.0
SCROLL_ROWS = 3
SLOW_STEP_MS = 50.0
LONG_ROW_CHARS = 2000
RECORD_ROWS = 3000
CASE_DEFAULT = "save-5g"
PROC_FD = "/proc/self/fd"


class Run(NamedTuple):
    """A run of the expected document: `length` bytes of the original file from `offset`, or the literal `data`."""

    offset: int
    length: int
    data: bytes | None

    def cut(self, low: int, high: int) -> Run:
        """The part `[low, high)` of the run."""
        return Run(self.offset + low, high - low, None if self.data is None else self.data[low:high])


def _cut(runs: list[Run], start: int, end: int) -> tuple[list[Run], list[Run], list[Run]]:
    before: list[Run] = []
    middle: list[Run] = []
    after: list[Run] = []
    position = 0
    for run in runs:
        low, high = position, position + run.length
        if low < start:
            before.append(run.cut(0, min(high, start) - low))
        first, last = max(low, start), min(high, end)
        if first < last:
            middle.append(run.cut(first - low, last - low))
        if high > end:
            keep = max(end, low)
            after.append(run.cut(keep - low, high - low))
        position = high
    return before, middle, after


class ByteModel:
    """The expected document, computed from the original file and the offsets of the scripted edits (never from the editor)."""

    def __init__(self, path: Path) -> None:
        self._fd = os.open(path, os.O_RDONLY)
        size = os.fstat(self._fd).st_size
        self._runs: list[Run] = [Run(0, size, None)] if size else []

    def close(self) -> None:
        """Close the file."""
        os.close(self._fd)

    @property
    def length(self) -> int:
        """Length of the expected document."""
        return sum(run.length for run in self._runs)

    def slice(self, start: int, size: int) -> list[Run]:
        """The runs of the expected bytes `[start, start + size)`."""
        return _cut(self._runs, start, start + size)[1]

    def delete(self, start: int, size: int) -> list[Run]:
        """Remove `[start, start + size)` and return the removed runs."""
        before, middle, after = _cut(self._runs, start, start + size)
        self._runs = [*before, *after]
        return middle

    def insert(self, at: int, runs: list[Run]) -> None:
        """Insert runs at document offset `at`."""
        before, _middle, after = _cut(self._runs, at, at)
        self._runs = [*before, *runs, *after]

    def read(self, offset: int, size: int) -> bytes:
        """Return up to `size` expected bytes from `offset`."""
        return b"".join(run.data if run.data is not None else os.pread(self._fd, run.length, run.offset) for run in self.slice(offset, size))


def literal(data: bytes) -> list[Run]:
    """A run list of literal bytes."""
    return [Run(0, len(data), data)]


def window_row(spec: Spec, step: str, actual: Callable[[int, int], bytes], actual_length: int, model: ByteModel, extra: list[int], **fields: object) -> Row:
    """Compare window hashes of `actual` with those of the model; `mismatches` counts differing windows plus a differing length."""
    offsets = window_offsets(model.length, extra)
    bad = [offset for offset in offsets if hashlib.sha256(model.read(offset, WINDOW)).digest() != hashlib.sha256(actual(offset, WINDOW)).digest()]
    return base_row(
        spec,
        case="verify",
        state=step,
        op="verify",
        windows=len(offsets),
        mismatches=len(bad) + (0 if actual_length == model.length else 1),
        mismatch_offsets=bad[:10],
        doc_length=actual_length,
        model_length=model.length,
        **fields,
    )


def verify_file(spec: Spec, step: str, path: Path, model: ByteModel, extra: list[int]) -> Row:
    """Window hashes of the file at `path` against the model (the saved output, read with `pread`, not through the editor)."""
    fd = os.open(path, os.O_RDONLY)
    try:
        return window_row(spec, step, lambda offset, size: os.pread(fd, size, offset), os.fstat(fd).st_size, model, extra)
    finally:
        os.close(fd)


def verify_document(spec: Spec, step: str, document: LazyDocument, model: ByteModel, extra: list[int]) -> Row:
    """Window hashes of the document against the model (used where the document itself is the subject: after the rebase, after undo and redo)."""
    return window_row(spec, step, lambda offset, size: document.read_bytes(offset, size, cache=False), document.length, model, extra)


def disk_used(directory: Path) -> int:
    """Bytes in use on the file system of `directory`."""
    stat = os.statvfs(directory)
    return (stat.f_blocks - stat.f_bfree) * stat.f_frsize


def retained_inode_bytes() -> int:
    """Bytes of the files this process still holds open although they were unlinked (the old file kept by a retained generation)."""
    seen: dict[tuple[int, int], int] = {}
    for name in os.listdir(PROC_FD):
        link = f"{PROC_FD}/{name}"
        try:
            if os.readlink(link).endswith(" (deleted)"):
                info = os.stat(link)
                seen[(info.st_dev, info.st_ino)] = info.st_size
        except OSError:
            continue
    return sum(seen.values())


def target_state(path: Path) -> tuple[int, int] | None:
    """Size and modification time of `path`, or `None` when it does not exist."""
    try:
        info = path.stat()
    except OSError:
        return None
    return info.st_size, info.st_mtime_ns


def temp_files_left(path: Path) -> int:
    """Temp files of a save of `path` left in its directory."""
    try:
        names = os.listdir(path.parent)
    except OSError:
        return 0
    return sum(1 for name in names if name.startswith(f".{path.name}.") and name.endswith(".tmp"))


class SaveApp(ProbeApp):
    """`ProbeApp` that records the save messages of its widget with the time they arrived."""

    def __init__(self, widget: NovaTextArea) -> None:
        super().__init__(widget)
        self.progress: list[tuple[float, str, int, int]] = []
        self.terminal: str | None = None
        self.terminal_at = 0.0
        self.stage = ""
        self.error_errno: int | None = None
        self.refused_at = 0.0
        self.refused = asyncio.Event()

    def reset(self) -> None:
        """Forget the messages of an earlier save."""
        self.progress = []
        self.terminal = None
        self.stage = ""
        self.error_errno = None

    def on_nova_text_area_save_progress(self, message: NovaTextArea.SaveProgress) -> None:
        """Record a progress message."""
        self.progress.append((time.perf_counter(), message.phase, message.done, message.total))

    def on_nova_text_area_saved(self, _message: NovaTextArea.Saved) -> None:
        """Record the terminal message `Saved`."""
        self._end("saved")

    def on_nova_text_area_save_failed(self, message: NovaTextArea.SaveFailed) -> None:
        """Record the terminal message `SaveFailed`."""
        self.stage = message.stage
        self.error_errno = message.error.errno
        self._end("failed")

    def on_nova_text_area_save_cancelled(self, _message: NovaTextArea.SaveCancelled) -> None:
        """Record the terminal message `SaveCancelled`."""
        self._end("cancelled")

    def on_nova_text_area_edit_refused(self, _message: NovaTextArea.EditRefused) -> None:
        """Record a refused edit."""
        self.refused_at = time.perf_counter()
        self.refused.set()

    def _end(self, kind: str) -> None:
        if self.terminal is None:
            self.terminal_at = time.perf_counter()
            self.terminal = kind
        else:
            self.terminal = f"{self.terminal}+{kind}"  # more than one terminal message is a defect that must show in the rows


def _timed_call[**P, R](calls: list[float], function: Callable[P, R]) -> Callable[P, R]:
    def call(*args: P.args, **kwargs: P.kwargs) -> R:
        started = time.perf_counter()
        try:
            return function(*args, **kwargs)
        finally:
            calls.append((time.perf_counter() - started) * 1000)

    return call


class SaveRunner:
    """Starts a save of the widget and collects what the harness measures: phase times, the time of `plan`, `prepare_rebase` and `apply_rebase`, progress messages.

    The wrappers are installed on the instances of the document and the widget (as `SegmentClock` does); the phase marks are printed from the save thread.
    """

    def __init__(self, app: SaveApp, area: ProbeTextArea, document: LazyDocument) -> None:
        self.app = app
        self.area = area
        self.document = document
        self.calls: dict[str, list[float]] = {"plan": [], "prepare_rebase": [], "apply_rebase": []}
        self.length = 0
        self.t_start = 0.0
        self.t_cancel: float | None = None
        self._first: dict[str, float] = {}
        self._ended: set[str] = set()
        self._reports = 0
        for name in self.calls:
            vars(document)[name] = _timed_call(self.calls[name], getattr(document, name))
        original = area._on_save_report
        vars(area)["_on_save_report"] = lambda run, report: self._report(original, run, report)

    def _report(self, original: Callable[..., None], run: object, report: Any) -> None:
        self._reports += 1
        phase = str(report.phase)
        if phase not in self._first:
            self._first[phase] = time.perf_counter()
            for name in PHASES[: PHASES.index(phase)]:
                self._end_phase(name)
        original(run, report)

    def _end_phase(self, name: str) -> None:
        if name not in self._ended:
            self._ended.add(name)
            emit(f"PHASE {name} {time.perf_counter_ns()}")

    def current_phase(self) -> str:
        """The latest phase a report was seen for (`writing` before the first report)."""
        seen = [name for name in PHASES if name in self._first]
        return seen[-1] if seen else "writing"

    def fault_io(self, kind: str, fraction: float) -> SaveIo:
        """A `SaveIo` that cancels the save (`cancel`) or fails the write with `ENOSPC` (`enospc`) once `fraction` of the document was written."""
        limit = int(self.length * fraction)
        written = 0
        fired = False

        def write(fd: int, data: bytes | memoryview) -> int:
            nonlocal written, fired
            if kind == "enospc" and written + len(data) > limit:
                raise OSError(errno.ENOSPC, "No space left on device (injected)")
            count = os.write(fd, data)
            written += count
            if kind == "cancel" and not fired and written >= limit:
                fired = True
                self.t_cancel = time.perf_counter()
                self.area.cancel_save()
            return count

        return SaveIo(write=write)

    def start(self, target: Path | None) -> None:
        """Start the save (`target` `None`: a plain save to the widget's file).

        Raises:
            RuntimeError: The widget did not start a save.
        """
        for calls in self.calls.values():
            calls.clear()
        self._first.clear()
        self._ended.clear()
        self._reports = 0
        self.t_cancel = None
        self.app.reset()
        self.length = self.document.length
        emit(f"PHASE pre_save {time.perf_counter_ns()}")
        self.t_start = time.perf_counter()
        if not self.area.save(target, overwrite=True):
            msg = "the widget did not start the save"
            raise RuntimeError(msg)

    async def finish(self, limit: float) -> Row:
        """Wait for the terminal message and return the measured fields: terminal, stage, phase times, throughput, progress messages, plan, prepare and apply times."""
        if not await wait_until(lambda: self.app.terminal is not None, limit):
            msg = "the save did not end"
            raise RuntimeError(msg)
        for name in PHASES:
            if name in self._first:
                self._end_phase(name)
        app = self.app
        end = app.terminal_at
        starts = {"writing": self.t_start, **{name: self._first[name] for name in PHASES[1:] if name in self._first}}
        present = [name for name in PHASES if name in starts]
        fields: Row = {}
        for index, name in enumerate(present):
            stop = starts[present[index + 1]] if index + 1 < len(present) else end
            fields[f"{name}_ms"] = (stop - starts[name]) * 1000
        save_s = end - self.t_start
        writing_s = fields.get("writing_ms", save_s * 1000) / 1000
        stamps = [stamp for stamp, *_ in app.progress]
        span = stamps[-1] - stamps[0] if len(stamps) > 1 else 0.0
        dones = [done for _stamp, phase, done, _total in app.progress if phase == "writing"]
        mib = self.length / (1 << 20)
        return {
            **fields,
            "terminal": app.terminal,
            "stage": app.stage,
            "errno": app.error_errno,
            "save_ms": save_s * 1000,
            "action_ms": save_s * 1000,
            "doc_length": self.length,
            "throughput_mib_s": mib / writing_s if writing_s > 0 else None,
            "throughput_total_mib_s": mib / save_s if save_s > 0 else None,
            "progress_messages": len(app.progress),
            "progress_per_s": len(app.progress) / span if span > 0 else None,
            "progress_monotonic": dones == sorted(dones),
            "report_calls": self._reports,
            "plan_calls": len(self.calls["plan"]),
            "prepare_rebase_ms": self.calls["prepare_rebase"][0] if self.calls["prepare_rebase"] else None,
            "apply_rebase_ms": self.calls["apply_rebase"][0] if self.calls["apply_rebase"] else None,
            "cancel_latency_ms": None if self.t_cancel is None else (end - self.t_cancel) * 1000,
        }


@dataclass
class SaveSession:
    """A running save scenario: the app, the widget, the model of the expected document and the rows so far."""

    spec: Spec
    app: SaveApp
    area: ProbeTextArea
    document: LazyDocument
    model: ByteModel
    runner: SaveRunner
    rows: list[Row] = field(default_factory=list)
    extra: list[int] = field(default_factory=list)
    """Offsets of the edit points; a verified window starts before each."""

    @property
    def limit(self) -> float:
        """Seconds a wait may take."""
        return float(self.spec.get("timeout", 900))


def settings_of(spec: Spec) -> SaveSettings:
    """The `SaveSettings` of a spec (`chunk`, `fsync_every`, `build_index`; absent keys keep the defaults)."""
    defaults = SaveSettings()
    return SaveSettings(
        chunk=int(spec.get("chunk") or defaults.chunk),
        fsync_every=int(spec.get("fsync_every") or defaults.fsync_every),
        build_index=bool(spec.get("build_index", True)),
    )


@contextlib.asynccontextmanager
async def open_session(spec: Spec, app_factory: Callable[[NovaTextArea], SaveApp] = SaveApp) -> AsyncIterator[SaveSession]:
    """Open the file of `spec` in a headless app, wait until it is indexed and yield the session; the model reads `spec["origin"]` (default the file).

    `app_factory` builds the app that hosts the widget (a subclass of `SaveApp` that records more messages, as the search scenarios do).
    """
    _plain, area = _open(spec)
    app = app_factory(area)
    ProbeTextArea.save_settings = settings_of(spec)
    ProbeTextArea.save_io = None
    model = ByteModel(Path(spec.get("origin") or spec["file"]))
    try:
        async with app.run_test(size=SIZE):
            await _first_content(area)
            emit(f"PHASE open {time.perf_counter_ns()}")
            await wait_until(lambda: not scan_busy(area), float(spec.get("timeout", 900)))
            await settle(0.2)
            document = lazy_document(area)
            if document is None:
                msg = "the file did not open lazily"
                raise RuntimeError(msg)
            yield SaveSession(spec, app, area, document, model, SaveRunner(app, area, document))
    finally:
        ProbeTextArea.save_io = None
        model.close()


async def settle_edits(session: SaveSession) -> None:
    """Wait until the edits are visible to the scan and the cursor machine."""
    area = session.area
    await wait_until(lambda: area.pending_progress is None, session.limit)
    await wait_until(lambda: not scan_busy(area), session.limit)
    await settle(0.2)


def edit_row(session: SaveSession, op: str, action_ms: float, **fields: object) -> None:
    """Record one scripted edit step."""
    session.rows.append(base_row(session.spec, case="save-edits", state="edit", op=op, action_ms=action_ms, doc_length=session.document.length, **fields))


async def scatter(session: SaveSession, count: int) -> None:
    """Insert `e` at `count` places spread over the document (from the last place to the first, so earlier offsets stay valid)."""
    document, area, model = session.document, session.area, session.model
    length = document.length
    spots: dict[Location, int] = {}
    for k in range(count):
        location, byte = place_at(document, (k + 1) * length // (count + 1))
        spots.setdefault(location, byte)
    started = time.perf_counter()
    for location in sorted(spots, reverse=True):
        area.insert("e", location)
        model.insert(spots[location], literal(b"e"))
    session.extra += list(spots.values())
    edit_row(session, "scatter", (time.perf_counter() - started) * 1000, edits=len(spots))
    await settle_edits(session)


class Deleted(NamedTuple):
    """A deleted block: where it started and the runs that were removed."""

    start: int
    runs: list[Run]

    @property
    def size(self) -> int:
        """Bytes removed."""
        return sum(run.length for run in self.runs)


async def delete_block(session: SaveSession, size: int) -> Deleted:
    """Select about `size` bytes from a quarter of the document and delete them (clamped to half of the document)."""
    document, area, model = session.document, session.area, session.model
    size = max(1, min(size, document.length // 2))
    first_loc, first_byte = place_at(document, document.length // 4)
    last_loc, last_byte = place_at(document, document.length // 4 + size, up=True)
    session.extra += [first_byte, last_byte]
    area.selection = Selection(first_loc, last_loc)
    await settle(0.1)
    started = time.perf_counter()
    area.action_delete_left()
    elapsed = (time.perf_counter() - started) * 1000
    removed = model.delete(first_byte, last_byte - first_byte)
    edit_row(session, "delete", elapsed, bytes=last_byte - first_byte)
    await settle_edits(session)
    return Deleted(first_byte, removed)


async def paste_block(session: SaveSession, size: int) -> None:
    """Copy about `size` bytes from an eighth of the document and paste them at three quarters."""
    document, area, model = session.document, session.area, session.model
    size = max(1, min(size, document.length // 4))
    copy_start, copy_start_byte = place_at(document, document.length // 8)
    copy_end, copy_end_byte = place_at(document, document.length // 8 + size, up=True)
    if copy_end_byte <= copy_start_byte:
        return
    clip = model.slice(copy_start_byte, copy_end_byte - copy_start_byte)
    area.selection = Selection(copy_start, copy_end)
    await settle(0.1)
    area.action_copy()
    paste_loc, paste_byte = place_at(document, document.length * 3 // 4)
    session.extra += [copy_start_byte, paste_byte]
    area.move_cursor(paste_loc)
    await settle(0.1)
    started = time.perf_counter()
    area.action_paste()
    elapsed = (time.perf_counter() - started) * 1000
    model.insert(paste_byte, clip)
    edit_row(session, "paste", elapsed, bytes=copy_end_byte - copy_start_byte)
    await settle_edits(session)


async def scripted_edits(session: SaveSession) -> None:
    """The scripted edits of the 5 GB save: `scatter` single character edits, a delete (`delete_bytes`) and a paste (`paste_bytes`), all before the save."""
    spec = session.spec
    await scatter(session, int(spec.get("scatter", 1000)))
    await delete_block(session, int(spec.get("delete_bytes", 1_000_000_000)))
    await paste_block(session, int(spec.get("paste_bytes", 100_000_000)))
    emit(f"PHASE edits {time.perf_counter_ns()}")


def _expected(spec: Spec) -> tuple[str, set[str]]:
    """The terminal message a spec expects and the stages it accepts."""
    fault = spec.get("fault")
    if fault == "cancel":
        return "cancelled", {""}
    if fault in ("enospc", "real-enospc"):
        return "failed", {"write", "prepare"}
    if not spec.get("build_index", True):
        return "failed", {"internal"}  # no line index: the rebase cannot run (Task 20 adds the fallback); the file is written
    return "saved", {""}


async def complete_save(session: SaveSession, target: Path, before: tuple[int, int] | None, **extra: object) -> Row:
    """Wait for the end of the started save, record its row and verify the result.

    A written file is compared with the model (`verify` row `output`) and, after a rebase, the document too (`after_rebase`); a cancelled or failed save must
    leave `target` as it was and no temp file (`target_unchanged`, `temp_files_left`).
    Row fields: everything `SaveRunner.finish` measures, `ok` (the outcome is the expected one and no window differs), `index_after_rebase`.
    """
    spec = session.spec
    fields = await session.runner.finish(session.limit)
    expected, stages = _expected(spec)
    written = fields["terminal"] == "saved" or (not spec.get("build_index", True) and fields["terminal"] == "failed" and fields["stage"] == "internal")
    verifies: list[Row] = []
    if written:
        verifies.append(verify_file(spec, "output", target, session.model, session.extra))
    if fields["terminal"] == "saved":
        verifies.append(verify_document(spec, "after_rebase", session.document, session.model, session.extra))
    session.rows += verifies
    clean = written or (target_state(target) == before and temp_files_left(target) == 0)
    outcome_ok = fields["terminal"] == expected and fields["stage"] in stages
    has_index = getattr(session.document, "_line_index", None) is not None
    session.rows.append(
        base_row(
            spec,
            case=spec.get("case", CASE_DEFAULT),
            state="save",
            op="save",
            **fields,
            expected_terminal=expected,
            ok=outcome_ok and clean and not any(row["mismatches"] for row in verifies),
            chunk=settings_of(spec).chunk,
            fsync_every=settings_of(spec).fsync_every,
            build_index=settings_of(spec).build_index,
            index_after_rebase=bool(fields["terminal"] == "saved" and has_index),
            target_unchanged=target_state(target) == before if not written else None,
            temp_files_left=temp_files_left(target),
            fault=spec.get("fault"),
            method=spec.get("method"),
            **extra,
        )
    )
    emit(f"PHASE verify {time.perf_counter_ns()}")
    return session.rows[-1]


async def save_scenario(spec: Spec) -> list[Row]:
    """`save-5g`, `save-sweep`, `save-cancel` and `save-fulldisk` child: the scripted edits, a save-as to `spec["target"]` and the verification.

    `spec["fault"]` is `cancel` (cancel once `cancel_at` of the document is written), `enospc` (the write fails with `ENOSPC` there) or `real-enospc` (the target is on a
    small tmpfs, nothing is injected).
    """
    async with open_session(spec) as session:
        await scripted_edits(session)
        target = Path(spec["target"])
        before = target_state(target)
        fault = spec.get("fault")
        session.runner.length = session.document.length
        if fault in ("cancel", "enospc"):
            ProbeTextArea.save_io = session.runner.fault_io(fault, float(spec.get("cancel_at", 0.5)))
        session.runner.start(target)
        await complete_save(session, target, before)
        emit("DONE")
        return session.rows


# -- steps during a save ------------------------------------------------------------------------------------------------------------
async def measure_op(app: SaveApp, area: ProbeTextArea, document: LazyDocument, op: str, index: int) -> Row:
    """Run one step of the latency rounds (not `x-refused`, which is an edit) and return its measured fields: `latency_ms`, `handler_ms`, `changed`.

    A `vscroll` step also reports `scroll_y_before`, `scroll_y_after` and `noop` (a step that did not scroll is never a latency sample: it waits for the next cursor blink repaint).
    """
    if op in JUMP_OPS:

        def jump() -> None:
            area.move_cursor(document.end if op == "ctrl+end" else (0, 0))

        action = jump
    elif op == "vscroll":
        offset = area.scroll_offset.y
        down = offset + SCROLL_ROWS <= area.max_scroll_y and (offset < SCROLL_ROWS or index % 2 == 0)
        target = offset + SCROLL_ROWS if down else max(0, offset - SCROLL_ROWS)

        def scroll() -> None:
            area.scroll_to(y=target, animate=False)

        action = scroll
    else:

        def press() -> None:
            send_key(app, op)

        action = press
    scroll_before = area.scroll_offset.y
    timing = await timed(area, action, limit=STEP_LIMIT, require_change=False, state=edit_state)
    if op == "vscroll":
        scroll_after = area.scroll_offset.y
        noop = scroll_after == scroll_before
        return {
            "latency_ms": None if noop else timing.latency_ms,
            "handler_ms": timing.handler_ms,
            "changed": timing.changed,
            "scroll_y_before": scroll_before,
            "scroll_y_after": scroll_after,
            "noop": noop,
        }
    return {"latency_ms": timing.latency_ms, "handler_ms": timing.handler_ms, "changed": timing.changed}


async def _latency_step(session: SaveSession, op: str, index: int) -> Row:
    area, app, runner = session.area, session.app, session.runner
    saving = area.saving
    common = {"phase": runner.current_phase() if saving else "idle", "step": index, "saving": saving}
    state = "saving" if saving else "idle"
    if op == "x-refused":
        app.refused.clear()
        started = time.perf_counter()
        send_key(app, "x")
        refused = True
        try:
            async with asyncio.timeout(REFUSE_LIMIT):
                await app.refused.wait()
        except TimeoutError:
            refused = False
        return base_row(session.spec, case="save-latency", state=state, op=op, latency_ms=(app.refused_at - started) * 1000 if refused else None, handler_ms=None, changed=False, **common)
    measured = await measure_op(app, area, session.document, op, index)
    return base_row(session.spec, case="save-latency", state=state, op=op, **measured, **common)


async def latency_rounds(session: SaveSession, steps: int, max_rounds: int) -> list[Row]:
    """Rounds of one step per op while the save runs: at least `steps` rounds, and on until the save ended, at most `max_rounds`."""
    rows: list[Row] = []
    for index in range(max_rounds):
        if index >= steps and session.app.terminal is not None:
            break
        for op in LATENCY_OPS:
            if op == "x-refused" and not session.area.saving:
                continue  # typing now would be an edit
            rows.append(await _latency_step(session, op, index))
            await settle(0.03)
    return rows


def _summary_row(session: SaveSession, steps: list[Row]) -> Row:
    during = [row for row in steps if row["state"] == "saving" and row["latency_ms"] is not None]
    longest = max(during, key=lambda row: row["latency_ms"], default=None)
    plans = session.runner.calls["plan"]
    chunk = settings_of(session.spec).chunk
    return base_row(
        session.spec,
        case="save-latency",
        state="summary",
        op="summary",
        steps_total=len(steps),
        steps_saving=sum(1 for row in steps if row["state"] == "saving"),
        steps_noop=sum(1 for row in steps if row.get("noop")),
        steps_over_50ms=sum(1 for row in during if row["latency_ms"] > SLOW_STEP_MS),
        longest_step_ms=None if longest is None else longest["latency_ms"],
        longest_step_op=None if longest is None else longest["op"],
        longest_step_phase=None if longest is None else longest["phase"],
        plan_calls=len(plans),
        plan_calls_expected=math.ceil(session.runner.length / chunk) if chunk else None,
        plan_ms_p95=percentile(plans, 0.95) if plans else None,
        plan_ms_max=max(plans, default=None),
    )


async def latency_scenario(spec: Spec) -> list[Row]:
    """`save-latency` child: the edited save while cursor keys, page keys, End, Ctrl+End (see `JUMP_OPS`), vertical scroll and a refused typing key are injected.

    Rows: one per step (`state` `saving` or `idle`, `phase` the save phase it fell into), one `plan` row per call of `LazyDocument.plan` (`action_ms`, the time the
    document lock is held), the save row, the verification and one `summary` row.
    """
    async with open_session(spec) as session:
        await scripted_edits(session)
        document, area = session.document, session.area
        area.move_cursor((min(100, document.line_count - 1), 0))
        await settle(0.2)
        target = Path(spec["target"])
        before = target_state(target)
        session.runner.start(target)
        steps = await latency_rounds(session, int(spec["steps"]), int(spec.get("max_rounds", 2000)))
        await complete_save(session, target, before)
        session.rows += steps
        session.rows += [base_row(spec, case="save-latency", state="plan", op="plan", step=index, action_ms=value) for index, value in enumerate(session.runner.calls["plan"])]
        session.rows.append(_summary_row(session, steps))
        emit("DONE")
        return session.rows


# -- the 200 MB line ----------------------------------------------------------------------------------------------------------------
async def longline_scenario(spec: Spec) -> list[Row]:
    """`save-longline` child: save the (copy of the) file in place with the cursor at `column`, then type one character there.

    A plain save writes only a modified document, so `e` is inserted at the start first. Rows: the save (`apply_rebase_ms` included), the verification and the `type`
    row (`latency_ms`, `changed`, `doc_delta`, `cursor_state_before`, `cursor_state_after`, `cursor_location_after`).
    """
    async with open_session(spec) as session:
        area, document = session.area, session.document
        area.insert("e", (0, 0))
        session.model.insert(0, literal(b"e"))
        await settle_edits(session)
        area.goto_byte(min(int(spec["column"]), document.length - 1))
        await wait_until(lambda: area.pending_progress is None, session.limit)
        await settle(0.5)
        before_state = area.cursor_state.name
        target = Path(spec["file"])
        before = target_state(target)
        session.runner.start(None)
        await complete_save(session, target, before)
        await settle(0.5)
        state_after_save = area.cursor_state.name
        length = document.length
        timing = await timed(area, lambda: send_key(session.app, "x"), limit=3.0, state=edit_state)
        await settle(0.2)
        session.rows.append(
            base_row(
                spec,
                case="save-longline",
                state="after_save",
                op="type",
                latency_ms=timing.latency_ms,
                handler_ms=timing.handler_ms,
                changed=timing.changed,
                doc_delta=document.length - length,
                cursor_state_before=before_state,
                cursor_state_after=state_after_save,
                cursor_state_typed=area.cursor_state.name,
                cursor_location_after=list(area.cursor_location),
            )
        )
        emit("DONE")
        return session.rows


# -- retention ----------------------------------------------------------------------------------------------------------------------
async def retention_scenario(spec: Spec) -> list[Row]:
    """`save-retention` child, `spec["sequence"]` `undo-save` (delete, undo, save) or `save-undo` (delete, save, undo); the save is in place on a copy.

    A first small edit makes the plain save write. Every step is verified by window hashes (`undo`, `output`, `after_rebase`, `redo`); the `disk` row reports the extra
    disk space of the retained generation: the old file stays open (unlinked) while undo records still refer to it.
    """
    sequence = str(spec["sequence"])
    async with open_session(spec) as session:
        area, document, model = session.area, session.document, session.model
        area.insert("e", (0, 0))
        model.insert(0, literal(b"e"))
        area.history.checkpoint()
        await settle_edits(session)
        deleted = await delete_block(session, int(spec.get("delete_bytes", 1_000_000_000)))
        target = Path(spec["file"])
        old_bytes = target.stat().st_size
        before = target_state(target)

        async def undo() -> None:
            area.undo()
            model.insert(deleted.start, deleted.runs)
            await settle_edits(session)
            session.rows.append(verify_document(spec, "undo", document, model, session.extra))

        async def save() -> None:
            used = disk_used(target.parent)
            session.runner.start(None)
            row = await complete_save(session, target, before, sequence=sequence)
            delta = disk_used(target.parent) - used
            new_bytes = target.stat().st_size
            session.rows.append(
                base_row(
                    spec,
                    case="save-retention",
                    state="disk",
                    op="disk",
                    sequence=sequence,
                    old_file_bytes=old_bytes,
                    new_file_bytes=new_bytes,
                    disk_used_delta=delta,
                    retained_inode_bytes=retained_inode_bytes(),
                    extra_disk_bytes=delta - (new_bytes - old_bytes),
                    terminal=row["terminal"],
                )
            )

        if sequence == "undo-save":
            await undo()
            await save()
        else:
            await save()
            await undo()
        area.redo()
        model.delete(deleted.start, deleted.size)
        await settle_edits(session)
        session.rows.append(verify_document(spec, "redo", document, model, session.extra))
        emit("DONE")
        return session.rows


# -- rebase records -----------------------------------------------------------------------------------------------------------------
def write_records_file(path: Path, long_rows: int = 8) -> Path:
    """Write a synthetic file of `RECORD_ROWS` short rows with `long_rows` long rows among them (no reference file is involved)."""
    step = RECORD_ROWS // (long_rows + 1)
    long_at = {step * (k + 1) for k in range(long_rows)}
    long_row = "long" + "abcdefghij" * (LONG_ROW_CHARS // 10)
    rows = [long_row if i in long_at else f"row {i}: the quick brown fox jumps over the lazy dog, caf\u00e9 \u65e5\u672c\u8a9e {i * 7919 % 1000}" for i in range(RECORD_ROWS)]
    path.write_text("\n".join(rows) + "\n", encoding="utf-8")
    return path


async def _records_once(spec: Spec, count: int) -> Row:
    with tempfile.TemporaryDirectory() as directory:
        copy = Path(directory) / "records.txt"
        shutil.copyfile(spec["file"], copy)
        async with open_session({**spec, "file": str(copy)}) as session:
            area, document = session.area, session.document
            longs = [row for row in range(document.line_count) if document.is_long(row)][: int(spec["long_indexes"])]
            for row in longs:
                document.long_index(row)
            await wait_until(lambda: not scan_busy(area), session.limit)
            plain = [row for row in range(document.line_count) if row not in set(longs)]
            for k in range(count):
                row = plain[k % len(plain)]
                area.insert("e", (row, min(k // len(plain) % 8, document.line_length(row) or 0)))
            await settle_edits(session)
            contents = len(area._reachable_contents())
            session.runner.start(None)
            fields = await session.runner.finish(session.limit)
            return base_row(
                spec,
                case="save-records",
                state=f"records={count}",
                op="rebase",
                records_requested=count,
                contents=contents,
                long_indexes=len(longs),
                long_indexes_cached=len(getattr(document, "_long", ())),
                **fields,
            )


async def records_scenario(spec: Spec) -> list[Row]:
    """`save-records` child: for each count of `spec["records"]` a fresh document with that many scattered edits (undo records) and `long_indexes` cached long row indexes, saved in place.

    One `rebase` row per count: `prepare_rebase_ms` (save thread), `apply_rebase_ms` (UI thread), `contents` (the references the history and clipboard hand to the rebase).
    """
    rows = [await _records_once(spec, int(count)) for count in spec["records"]]
    emit("DONE")
    return rows


def last_saved_phase(marks: list[tuple[str, int]]) -> int | None:
    """The time of the last save phase mark of a child's `PHASE` marks (the end of the save window), or `None`."""
    ends = [end for name, end in marks if name in PHASES]
    return max(ends, default=None)


def start_mark(marks: list[tuple[str, int]]) -> int | None:
    """The time of the `pre_save` mark (the start of the save window), or `None`."""
    return next((end for name, end in marks if name == "pre_save"), None)
