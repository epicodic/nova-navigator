# Text Editor Package (`nova_editor`)

This document describes the architecture and design of the `nova_editor` package, a standalone Textual-based text editor with vendored TextArea widget implementation.

---

## Overview

**Purpose:** Provide a modern, Textual-based text editor widget and standalone application designed to handle large files and very long lines with minimal overhead.

The package is separate from `nova_navigator` and can be used independently.

**Status:** The core layer is tested and performs background scanning of large files.
The widget is a vendored copy of Textual 8.2.8's `TextArea` (renamed `NovaTextArea`) with significant modifications to add lazy document support, long-row handling, and windowed rendering.
The lazy document layer (`LazyDocument`, `LazyWrappedDocument`) uses the core indexes to stream file content on demand; capability methods never wait for a scan and return `None` / empty results beyond the scanned frontier.
The widget layer supports eager opening of files up to 1 MiB and lazy opening above that threshold via the `open()` class method; `nova_edit` chooses automatically unless `--lazy` is passed.
Two-path mode is under development: small files use stock `Document`, large files use `LazyDocument`, and both are backed by the same in-memory `ByteSource` (convergence planned for ACT4).

---

## Layering

The package is organized in four layers, with one-way dependencies: core → document → widget → app.

### 1. **Core Layer** (`nova_editor/core/`)

Textual-free, immutable-file byte indexing and scanning infrastructure.
No module in this package may import Textual (REQ-17); a boundary test enforces it.

**Modules and roles:**

`byte_source` — Random-access byte protocol, change detection, and `PreadSource` implementation.
`ByteSource` protocol defines `length()`, `read(offset, size, cache)`, and `close()`.
`SourceChanged` exception signals file mutation (size/mtime changed, short read, or OS error).

`text_width` — Display-width calculations for tabs, wide characters and combining marks.
Cell widths come from `rich.cells`; the module does not import Textual.
Functions `advance_disp`, `locate_cover`, `safe_cut`, `resync`, `utf8_len` support the long-line index.

`line_index` — Sparse background line index over the file.
`LineIndex` scans rows in a daemon thread, appending stride entries (every 64th row by default).
`RowRange` describes a row as a byte range: `(start, content_end, end)` where the terminator is bytes from `content_end` to `end`.
`LineSnapshot` gives `(count, complete, error)`: count is a lower bound until complete.

`long_line_index` — Lazy checkpoint index for one very long row.
`LongLineIndex` records character column, display column and byte offset checkpoints at most 65,536 characters apart.
`Frontier` shows how far the background scan has reached: chars, disp (display columns), byte_rel (bytes relative to row start), and complete flag.
Queries beyond the scanned frontier return `None` (non-blocking); blocking is opt-in via `wait_until_known`.

**I/O strategy (ACT1 measurements):**

`PreadSource` uses `os.pread` behind a small LRU block cache: 64 KiB blocks, 128 blocks (8 MiB total).
One shared cache per source instance; scan reads bypass it via `cache=False`.
Memory map was rejected: warm window reads are 5.6x faster with mmap (1.3 us vs 7.0 us per 4 KiB read), but mmap dies with SIGBUS on file truncation and pread cannot; scan speed is equal (about 2.5 GB/s warm, 1.4 GB/s cold); fstat change checks cost about 1.8%.

**Change detection:**

`fstat` check on size and mtime_ns before each read, including scan reads.
Short-read rule: a pread result shorter than requested raises `SourceChanged` because the file shrank.
Short data is never cached; a failed source stays failed.
OSError during read becomes `SourceChanged`.

**Line semantics:**

Rows end at LF (`\n`), CRLF (`\r\n`), or lone CR (`\r`).
Terminator bytes belong to the row (included in the `RowRange`).
A file ending in a terminator has a final empty row; an empty file is one empty row.
U+2028 (line separator), U+2029 (paragraph separator), VT (U+000B), FF (U+000C), FS (U+001C), GS (U+001D), RS (U+001E), and NEL (U+0085) are ordinary content (differs from stock Document using `str.splitlines`).
BOM is content; line endings are never normalised (REQ-13).

**Sparse line index:**

Every 64th row start is stored in an array (default stride).
For a 5 GB file: 733,516 entries, about 36 MiB RssAnon with full cache; a full array would need 381 MiB.
Background thread appends and publishes `(count, complete)` snapshots under one lock.
Count is a lower bound until complete; UI-path calls never wait.
Callbacks run on the scan thread, outside locks.
Scanning 512 MiB of 99-byte rows takes 0.65 s with LF terminators (byte-search fast path), about 2.3 s with CRLF, and about 2.4 s with lone CR (regex path).
Files with CR bytes scan about 3.5 times slower per byte.
Scan of the 5 GB file takes about 6.6 s warm and 8.4 s cold, against 4.1 s in the ACT1 prototype.
Each row now also checks the long-row rule.

**UI-path byte budget (DEC-14):**

Every `row_range` and `lines` call reads at most 4 MiB (hard rule).
Returns `None` or a shorter list when the budget is exceeded.
Rows longer than 16 KiB are recorded during scan in a side table (cap 524,288 entries, about 12 MiB worst case) so walks jump over them.
`lines` returns at most 128 rows per call.
The walk reads windows of min(64 KiB, `long_line_threshold` + 2) bytes, which is 16,386 bytes with the defaults.
A recorded long row costs no walk read, but its terminator is found with a two-byte read of the row tail.
With defaults, worst case is about 3.1 MB per call; measured worst 0.31 ms on a 5 GB file and 8 ms / 2.5 MB on files with 15–17 KiB lines.

**Long-line index:**

Checkpoints (character column, display column, byte offset) are stored relative to the row start.
Their spacing is at most 65,536 characters: each scan block of 1 MiB adds a shorter tail piece when its length is not a multiple of 65,536.
For a 200 MB line: 3,201 checkpoints (`checkpoint_count`), about 77 KB of arrays.
Peak about 35 MiB RssAnon in the measurement.
A first version peaked at 77.5 MiB because rich's caching cell-width function retained every 65,536-character piece.
text_width now uses the non-caching rich.cells.cell_len.
UTF-8 resync on block boundaries; surrogateescape for invalid bytes; tabs, wide chars and combining marks via `rich.cells`.
Non-blocking `try_*` queries return `None` beyond the scanned frontier (no waiting; a far query returned in 0.003 ms vs ACT1's 1.8–4.3 s blocking).
Blocking is opt-in via `wait_until_known` with timeout/cancel support.
Estimates (character count, display width) are available at any time and become exact when the scan completes.
`try_get_slice` returns at most one checkpoint interval of text per call and never text beyond the scanned frontier.

**Concurrency:**

Append-only publication under one lock per index (LineIndex uses a threading.Lock; LongLineIndex uses threading.Condition).
Callbacks registered via `subscribe()` run on the scan thread outside all locks.
No lock is held across I/O.
`subscribe()` on a scan that already completed or failed runs the callback once at subscription; a callback that raises is contained and logged at debug level.
`PreadSource.close()` waits for reads in flight, and reads that start after `close()` began raise `ValueError`.
Callers should `cancel()` and `join()` a scan before closing its source.

**Errors on the UI path:**

The UI-path methods (`LineIndex.row_range` and `lines`, and every decoding query of `LongLineIndex`) read the source themselves and can raise `SourceChanged`.
Their callers must handle it; it is not reported only through the scan error.
`None` or a short list from `row_range` and `lines` means either that the scan has not reached the row or that the read budget was exceeded; `snapshot()` and `long_overflow` tell them apart.

**Edit-awareness (future work):**

Indexes describe the immutable original file bytes.
The document layer will layer an edit overlay on top (sorted edits with cumulative row and byte deltas).
Long-line checkpoints remain valid up to the first edited byte.

**Memory budget (5 GB file, REQ-2):**

Baseline (imports, data structures): about 22 MiB.
Sparse line index: about 7 MiB.
Block cache: 8 MiB.
Scan buffers: 1–2 MiB.
Total measured: about 36 MiB against the 200 MB limit.

**Textual import boundary:**

Direct `import nova_editor.core` still loads Textual through `nova_editor/__init__.py` (imports the widget).
The boundary test therefore stubs the parent package and loads core modules with it.
Making the top-level exports lazy is a possible later change outside this activity.

### 2. **Document Layer** (`nova_editor/document/`)

Lazy read-only documents built over the core indexes; used only when a file is opened via `NovaTextArea.open()`.
The layer also includes stock document support (vendored from Textual 8.2.8) for small files opened with `text=`.

**Lazy classes:**
- `LazyDocument` — read-only document backed by `ByteSource` and `LineIndex`; decodes rows on demand with capability methods that never wait.
- `LazyWrappedDocument` — wrapping layer over `LazyDocument` for soft-wrap and grid-wrap modes; manages wrap-offset indexes.
- `LazyConfig` — tunable thresholds and cache sizes with defaults.

**Row classes (by content byte length):**
- Short: 0 to `word_wrap_limit` (default 64 KiB); stock wrapping, fully decoded.
- Medium: 64 KiB to `long_row_threshold` (default 1 MiB); grid wrapping above `word_wrap_limit`, fully decoded for wrapping.
- Long: above 1 MiB; never decoded as a whole; windowed rendering and byte-anchored cursor.

**Capability methods (on DocumentBase; lazy documents override):**
- `is_long(row)` — return whether the row must never be decoded as a whole.
- `line_length(row)` — return the character count, or `None` if not yet known.
- `row_byte_length(row)` — return the UTF-8 byte length of the row content.
- `column_slice(row, start, stop)` — return characters [start, stop), at most 8192 characters for lazy documents.
- `has_char_at(row, column)` — return whether the row has a character at column.
- `display_column(row, column, tab_width)` — return the display column of character column, or `None` when unknown.
- `column_at_display(row, x, tab_width)` — return the character column covering display column x, or `None` when unknown.
- `byte_offset(row, column)` — return the byte offset from document start, or `None` when unknown.

**Cursor state machine (`_cursor_anchor.CursorMachine`):**
The cursor on a long row has three states: `RESOLVED` (exact column known), `PROVISIONAL` (byte position is exact, column is an estimate), `PENDING` (waits for the scan to reach the cursor byte).
The machine accepts `Op` (operations like `LEFT`, `RIGHT`, `GOTO_COLUMN`, etc.) and returns a `Verdict` (`DONE` or `PENDING`).
When the index of a long row is created, its starting position is `PROVISIONAL` (byte 0, estimated column 0).
A RESOLVED cursor is retained if the operation stays within the known frontier; otherwise it becomes PROVISIONAL.
A PENDING cursor rejects new operations until the scan reaches the cursor byte (only `jump_to_byte` retargets).

### 3. **Widget Layer** (`nova_editor/widget/`)

Textual widget and lazy rendering support.

**Core module:**
- `NovaTextArea` — the main editor widget (vendored from Textual 8.2.8, renamed from `TextArea`).
  Supports both eager mode (stock `Document`, fully loaded) and lazy mode (`LazyDocument`, streamed).
  In lazy mode: rows are decoded on-demand; long rows use windowed rendering; cursor on long rows goes through the state machine.

**Helper modules:**
- `_text_area_theme.py` — theme support for syntax highlighting.
- `_lazy_window.py` — windowed rendering for medium and long rows; decodes and classifies a visible window without decoding more than 8192 characters.
- `_long_row_cursor.py` — cursor state machine integration and provisional layout for long rows.

**Public API (`NovaTextArea`):**
- `open(source, language, soft_wrap, config, highlight_limit, **kwargs)` — open a file lazily; returns the widget ready to mount.
- `is_lazy` — read-only boolean; true when the widget uses a lazy document.
- `text` (property) — full file text in eager mode; raises `WholeLineAccess` on lazy documents (by design).
- `read_only` — forced true for lazy documents.
- `document` — a `DocumentBase` (stock `Document` or `LazyDocument`).
- `cursor_state` (read-only) — the `CursorState` of a long-row cursor; `None` for stock documents.
- `cursor_byte_offset` (read-only) — the absolute byte offset of the cursor; `None` for stock documents.
- `column_exact` (read-only) — true when the cursor column is exact (RESOLVED state); false for PROVISIONAL/PENDING.
- `cursor_location` — the row and column of the cursor; estimate for PROVISIONAL.
- `pending_progress` — fraction in [0, 1) of a deferred cursor jump; `None` when nothing is pending.
- `line_count` (read-only) — lower bound on the total lines; exact when indexing is complete.
- `line_count_exact` (read-only) — true when the line count is final.
- `indexing_complete` (read-only) — true when the line scan is complete.
- `goto_line(line_num)` — go to a line; deferred and returns a `Verdict` if the line is not yet indexed.
- `goto_byte(byte_offset)` — go to an absolute byte offset; deferred and returns a `Verdict` if the byte is beyond the scanned frontier.
- `cancel_pending()` — cancel a deferred jump; action bound to Escape in lazy mode.
- `toggle_wrap()` — toggle soft-wrap mode; action bound to F4 in lazy mode.
- `close()` — close the underlying source and background scans (called on widget unmount).

**Message classes (posted by the widget):**
- `IndexProgress(count, complete)` — posted at most 10 times per second while the line scan grows; once at completion.
- `IndexingComplete` — posted once when the line scan finishes.
- `SourceChanged(reason)` — posted once if the file behind a lazy document changed or could not be read; the view stays blank.
- `JumpProgress(fraction)` — posted when a deferred cursor jump starts or advances.
- `JumpCompleted(row, column)` — posted when a goto has moved the cursor; `column` is an estimate while PROVISIONAL.
- `JumpRejected(reason)` — posted when a goto target is out of range.

**Thresholds (defaults, tunable via `LazyConfig`):**
- `word_wrap_limit = 65536` — rows above this use grid wrap instead of word wrap.
- `long_row_threshold = 1048576` — rows above this are never decoded as a whole; use windowed rendering.
- `highlight_limit = 1048576` (in `open()`) — files larger than this are not syntax highlighted (separate from lazy threshold).

### 4. **Application Layer** (`nova_editor/app.py`)

Standalone Textual app (`NovaEditApp`) providing:
- Automatic eager/lazy choice based on file size: files up to `EAGER_LIMIT` (1 MiB) are opened eagerly; larger files lazily.
- File loading/saving via `Ctrl+S` and `Ctrl+Q`.
- F4 to toggle soft-wrap mode.
- Ctrl+G to open the goto bar for line or byte offset navigation (`@N` syntax for byte offsets).
- Escape to cancel a pending jump (lazy mode only).
- Footer showing file path and keyboard shortcuts.
- Entry point `main()` for the `nova_edit` CLI command (supports `--lazy` flag to force lazy mode).
- Timing hook support via `NOVA_EDIT_TIMING_FILE` environment variable (writes `FIRST_CONTENT <ns>` when content first renders).

**GotoBar:** Inline input field; accepts `N` (line number) or `@N` (byte offset); Escape closes it, Enter navigates.

**TimedNovaTextArea:** Wrapper that logs the first content render time for benchmarking.

---

## Two-Path Model

A document can be opened in either eager or lazy mode:

**Eager mode (small files):**
Files up to `EAGER_LIMIT` (1 MiB) are opened with `text=` parameter; `NovaTextArea` creates a stock `Document` and loads all bytes into memory immediately.
This mode supports all text editor features (undo/redo, full-text search, etc.).

**Lazy mode (large files):**
Files larger than 1 MiB are opened via `NovaTextArea.open(path)`, which creates a `LazyDocument` backed by `ByteSource` and `LineIndex`.
Rows are decoded on demand; long rows are never decoded as a whole.
This mode is read-only by design.

**Convergence (planned for ACT4):**
A `ByteSource` over in-memory byte buffers allows both documents to share the same immutable source.
Small files will be backfilled into memory after initial display, then switched to stock `Document` to gain edit support.
Long rows will remain lazy and windowed even in this mode.

---

## Wrap Semantics

Wrapping is layer-dependent and triggered by row size:

**Short rows (0 to 64 KiB):**
Stock word-wrap (Textual's `compute_wrap_offsets`); fast because the row is decoded once.
Estimate: 0.22 ms at 4 KiB, 0.90 ms at 16 KiB, 3.55 ms at 64 KiB.

**Medium rows (64 KiB to 1 MiB):**
Grid wrap: soft-wrap is disabled, the row is fully decoded for grid layout, and wrapping is done via windowed rendering.
The window starts at the visible display column and never decodes more than 8192 characters.
Estimate: 15.4 ms to compute wrap offsets of a 256 KiB row at width 113.

**Long rows (above 1 MiB):**
Always windowed; grid wrap by default, but wrap toggle still works to switch between no-wrap and grid-wrap display.
No full-row decode is ever done; the window is the decoded region.
Estimate: 2.4 ms to render the window without wrapping, 6.2 ms with wrapping (at 256 KiB rows), 21.5 ms at 1 MiB rows.

**Word-wrap limit (64 KiB):**
Above this threshold, soft-wrap uses grid wrapping (each wrap line has a fixed width) instead of word wrapping (wraps at word boundaries).
This avoids the cost of full-row scans for width calculation on every page down.

**Wrap toggle (F4 in nova_edit):**
Switches between no-wrap and grid-wrap modes.
For long rows on a lazy document, this is a state change only; the window is re-rendered.
On a stock document, the underlying `WrappedDocument` is recalculated.

---

## Cursor State Machine (Long Rows)

A cursor on a long row goes through three states:

**RESOLVED:**
The byte position and column are both exact.
The cursor can move freely within the scanned frontier.

**PROVISIONAL:**
The byte position is exact (from a byte-anchored jump), but the column is an estimate.
This state is entered after a jump to a location (via `goto_byte` or when waiting for a deferred jump completes).
The cursor cannot move until the column is recalculated (via a request to `display_column`).

**PENDING:**
The cursor awaits the scan to reach the byte position.
This state is entered when a jump target lies beyond the scanned frontier.
The widget shows `JumpProgress` messages and allows `Escape` to cancel.
Once the scan reaches the byte, the machine transitions to PROVISIONAL.

**State transitions:**
- `RESOLVED` + operation within frontier → `RESOLVED`
- `RESOLVED` + operation beyond frontier → `PENDING`
- `PROVISIONAL` + column request → `RESOLVED` (if possible) or stay `PROVISIONAL` (if beyond frontier)
- `PENDING` + scan reaches cursor → `PROVISIONAL`
- Any state + `jump_to_byte` → `PROVISIONAL` (retargets the pending jump)

---

## Thread Model

Background scanning runs in a separate thread managed by `LineIndex` and `LongLineIndex`.

**Foreground gate (`nova_editor/core/foreground.py`):**
A `Foreground` instance records when the UI is busy (via `touch()`).
The scan thread calls `pause_seconds()` between work pieces: if the UI has been active within the last 50 ms, the scan sleeps 1 ms before the next piece.
This hands the GIL to the UI without waiting for the 5 ms scheduler interval, keeping down-key latency under 2.3 ms even during indexing.

**Scan thread lifecycle:**
- Append-only updates: scans publish `(line_count, complete)` snapshots under one lock per index.
- Callbacks run on the scan thread outside all locks.
- Shutdown: `cancel()` and `join()` the scan, then `close()` the source.

**Coalesced messages:**
Scan-completion callbacks use `call_from_thread()` to post updates to the Textual event loop.
Multiple pending callbacks are coalesced into a single `IndexProgress` message (posted at most 10 times per second).

---

## Performance Thresholds (Measured)

All measurements are on the developer's machine (i5-14600K) with the harness `uv run python -m tools.measure_view ...`.

**Syntax highlighting limit (1 MiB):**
Files above this size are not highlighted when opened lazily, to keep first-screen latency low.
First screen render: 97.6 ms at 64 KiB, 221 ms at 256 KiB, 790 ms at 1 MiB, 3.1 s at 4 MiB, 13.4 s at 16 MiB.

**Word-wrap limit (64 KiB):**
Above this threshold, soft-wrap uses grid wrapping instead of word wrapping.
Word wrap of one row at width 113: 0.22 ms at 4 KiB, 0.90 ms at 16 KiB, 3.55 ms at 64 KiB, 15.4 ms at 256 KiB, 61.6 ms at 1 MiB.

**Long-row threshold (1 MiB):**
Rows above this are never decoded as a whole; use windowed rendering.
Whole-row decode render: 5.7 ms at 256 KiB, 21.4 ms at 1 MiB, 74 ms at 4 MiB.
Windowed render (up to 8192 characters): 2.4 ms at 256 KiB, 6.2 ms at 1 MiB, 21.5 ms at 4 MiB.

**Eager limit (1 MiB):**
Files above this are opened lazily; below are opened eagerly.
First screen latency comparison (lazy vs eager): 18 vs 34 ms at 64 KiB, 45 vs 36 ms at 256 KiB, 139 vs 37 ms at 1 MiB, 680 vs 44 ms at 4 MiB.

**Checkpoint spacing:**
Long-line indexes use checkpoints spaced at most 65,536 characters apart.
Fallback minimum: 8,192 characters (for very small synthetic tests).

**Page up/down latency (during indexing, 5 GB file):**
GIL contention between scan and UI threads was the bottleneck.
Fixes: ASCII/non-ASCII run splitting in `text_width._cells()` to minimize per-character width lookups; `Foreground` gate to let the UI yield the scan between work pieces.
Result: down-key median 2.3 ms, page down/page up median 15 ms, max 33 ms (during active scanning on a 5 GB file).

---

## Testing

Tests are located under `tests/nova_editor/`:

**Core layer tests:**
- `core/test_byte_source.py` — PreadSource and SourceChanged.
- `core/test_foreground.py` — Foreground gate pause behavior.
- `core/test_row_at_offset.py` — LineIndex.row_at_offset and scanned_bytes.
- `core/test_text_width.py` — Display-width helpers.

**Document layer tests:**
- `document/test_lazy_document.py` — LazyDocument capability methods and RowUnavailable.
- `document/test_lazy_wrapped_document.py` — LazyWrappedDocument wrap offset indexes.
- `document/test_cursor_anchor.py` — CursorMachine state transitions.
- `document/test_long_row_anchor.py` — LongLineIndex checkpoints and non-blocking queries.
- `document/test_capabilities.py` — DocumentBase capability methods (stock and lazy).
- `document/test_navigator_capabilities.py` — DocumentNavigator on lazy documents.
- `document/test_navigator_long_rows.py` — DocumentNavigator wrapping logic for long rows.

**Widget layer tests:**
- `test_lazy_widget.py` — LazyDocument rendering and cursor state.
- `test_lazy_cursor.py` — Cursor machine integration; state transitions and message posting.
- `test_jump.py` — Goto line/byte on lazy documents; deferred jumps and cancellation.
- `test_highlight_limit.py` — Syntax highlighting disabled for large files.
- `test_app_lazy.py` — NovaEditApp lazy opening and timing hooks.
- `test_widget_capabilities.py` — Widget capability methods (lazy and stock).
- `test_bindings.py` — Keybindings (F4 wrap, Ctrl+G goto, Escape cancel).

**Helpers and benchmarks:**
- `helpers_view.py` — Synthetic file builder and oracle for correctness testing.
- `test_helpers_view.py` — Oracle tests (never use reference files).
- `tools/test_measure_view.py` — Smoke test for the benchmark harness.

**Measurement harness (`uv run python -m tools.measure_view`):**
Subcommands: `first-screen`, `memory`, `latency`, `jump`, `oracle`, `calllog`, `sweep-yield`, `thresholds`, `summarise`.
See `src/tools/measure_view.py` for usage.

---

## Vendoring Policy

Rather than subclassing Textual's `TextArea`, we vendor (copy) the full implementation and its dependencies:

| Reason | Benefit |
|--------|---------|
| **Anticipated heavy modifications** | Future work on large-file support will be deep and pervasive; vendoring avoids subclass fragility. |
| **Minimize Textual coupling** | Textual's internals may change; vendoring gives us control over API stability. |
| **Performance optimization** | We can optimize for Nova Navigator's specific use cases. |
| **Version independence** | We pin the vendored code, not Textual. |

### Vendored Files

| File | Upstream | Notes |
|------|----------|-------|
| `widget/_text_area.py` | Textual `widgets/_text_area.py` | Main widget; class renamed `TextArea` → `NovaTextArea` |
| `widget/_text_area_theme.py` | Textual `_text_area_theme.py` | Theme system; imports updated |
| `document/_*.py` | Textual `document/` package | Document model (6 files); internal imports updated |

See `src/nova_editor/UPSTREAM.md` for detailed file inventory and upgrade instructions.

---

## Public API

**Main exports** (via `nova_editor.__init__.py`):
- `NovaTextArea` — the editor widget

**Widget subpackage exports** (via `nova_editor.widget.__init__.py`):
- `NovaTextArea`
- `TextAreaLanguage`
- `ThemeDoesNotExist`
- `LanguageDoesNotExist`

**Usage example:**
```python
from nova_editor import NovaTextArea
from textual.app import App, ComposeResult

class MyEditorApp(App):
    def compose(self) -> ComposeResult:
        yield NovaTextArea(text="Hello, world!")
```

---

## Running the Editor

### Standalone application

```sh
uv run nova_edit                    # Open editor with no file
uv run nova_edit /path/to/file.txt  # Open a specific file
```

### Programmatic use

```python
from nova_editor import NovaTextArea

widget = NovaTextArea(text="initial content")
# Mount in a Textual app as normal
```

---

## Testing

Tests are located under `tests/nova_editor/`:

- **`test_widget.py`** — Widget mounting, typing, undo/redo, newline handling
- **`test_app.py`** — File loading/saving, app initialization, error handling
- **`test_independence.py`** — Verify no dependency on `nova_navigator`
- **`core/`** — Core layer tests: property tests with hypothesis against reference implementations, a truncation test that runs in a subprocess (`test_truncation.py`), and the Textual import boundary test (`test_core_boundary.py`)

The measurement script `src/tools/measure_core.py` has a smoke test in `tests/tools/test_measure_core.py`.

Run all tests:
```sh
uv run pytest tests/nova_editor/ tests/tools/test_measure_core.py
```

---

## Updating from Upstream

When Textual releases a new version and we want to update our vendored code:

1. Identify the target Textual version
2. Extract the updated files from the Textual repository
3. Carefully merge changes while preserving:
   - `NovaTextArea` class name (instead of `TextArea`)
   - Import paths pointing to `nova_editor` packages
4. Test thoroughly with `uv run qa`
5. Update the version reference in `UPSTREAM.md`

See `UPSTREAM.md` for detailed upgrade instructions.

---

## Future Work

- **Edit support:** Restore undo/redo and text editing for large files via the two-path model (ACT4).
- **Search:** Add find/replace that works on lazy documents (search only the decoded portions or the original file).
- **Selection:** Multi-line and multi-region selection on lazy documents.
- **Integration:** Embed in `nova_navigator` as an editor dialog for large file viewing and light editing.
- **Syntax highlighting on long rows:** Currently disabled for files >1 MiB; tree-sitter queries could be windowed.
- **Goto optimizations:** Cache line scan progress in the file so re-opening a file starts with the last-known positions.
