# Text Editor Package (`nova_editor`)

This document describes the architecture and design of the `nova_editor` package, a standalone Textual-based text editor with a vendored TextArea widget.

---

## Overview

**Purpose:** Provide a Textual-based text editor widget and standalone application that handles large files and very long lines with minimal overhead.

The package is separate from `nova_navigator` and can be used independently.

**Status:** The widget is a vendored copy of Textual 8.2.8's `TextArea` (renamed `NovaTextArea`) with a lazy, editable document, long-row handling and windowed rendering.
The core layer scans large files in background threads and holds the edited document as a piece table over the original bytes.
The lazy document layer (`LazyDocument`, `LazyWrappedDocument`) uses the core to decode rows on demand.
Its capability methods never wait for a scan and return `None` or empty results beyond the scanned frontier.
Every document uses this one path: `text=`, `load_text` and files of every size all become a `LazyDocument` (see "Converged Document Model").
The widget is editable; undo, redo, cut, copy and paste work on piece references.
Saving streams the document to a temporary file on a worker thread and replaces the target atomically (see "Streaming Save").
After a save the document stands on the saved file, and undo still works (see "Rebase After a Save").
A change of the file on disk is detected and never overwritten silently (see "External Changes").
A literal search runs over the whole document on a worker thread (see "Search").

---

## Layering

The package is organized in four layers, with one-way dependencies: core, document, widget, app.

### 1. **Core Layer** (`nova_editor/core/`)

Textual-free byte indexing, scanning and piece-table infrastructure.
The original file is immutable; edits live in the piece table above it.
No module in this package may import Textual (REQ-17); a boundary test enforces it.

**Modules and roles:**

`memory_source` — `BytesSource`, a `ByteSource` over bytes held in memory (small files and `text=`).

`pieces`, `piece_tree`, `add_store`, `original_source`, `piece_table`, `row_source` — the piece table and its parts (see "Piece Table").

`byte_source` — Random-access byte protocol, change detection, and `PreadSource` implementation.
`ByteSource` protocol defines `length()`, `read(offset, size, cache)`, and `close()`.
`SourceChanged` exception signals file mutation (size/mtime changed, short read, or OS error) and carries a `ChangeKind`.
`FileIdentity(dev, ino, size, mtime_ns)` is taken from `os.stat` or `os.fstat`.
`PreadSource.from_fd` adopts an open descriptor, `identity()` returns the recorded identity, `check()` runs the descriptor check without a read, and `unverified_reader()` returns a reader that skips the size and mtime check.

`row_scanner` — `RowScanner`, the row-boundary scan shared by `LineIndex` and the save thread (see "Row Scanner and the Stream Index").

`save`, `save_layout`, `rebase` — `SaveJob` and its types, `SaveLayout`, and `Rebaser` (see "Streaming Save" and "Rebase After a Save").
`save` re-exports `ChangeKind` and `FileIdentity` and imports only core modules.

`text_width` — Display-width calculations for tabs, wide characters and combining marks.
Cell widths come from `rich.cells`; the module does not import Textual.
Functions `advance_disp`, `locate_cover`, `safe_cut`, `resync`, `utf8_len` support the long-line index.
A long text with some non-ASCII characters is split into ASCII and non-ASCII runs, so the per-character width lookup runs only over the non-ASCII part.

`line_index` — Sparse background line index over the file.
`LineIndex` scans rows in a daemon thread, appending stride entries (every 64th row by default).
`RowRange` describes a row as a byte range: `(start, content_end, end)` where the terminator is bytes from `content_end` to `end`.
`LineSnapshot` gives `(count, complete, error, scanned_bytes)`: count is a lower bound until complete, and `scanned_bytes` is how far the scan has covered the file.
`LineIndex.row_at_offset(offset)` returns the row that holds a byte offset, or `None` when it is not indexed yet or the walk would exceed the read budget.
`LineIndex.scan_now()` runs the whole scan on the calling thread; the document layer uses it for sources up to 1 MiB.
`LineIndex.from_scan` builds a complete index from a `RowScanner` that saw every byte (see "Row Scanner and the Stream Index").

`long_line_index` — Lazy checkpoint index for one very long row.
`LongLineIndex` records character column, display column and byte offset checkpoints at most 65,536 characters apart by default.
`Frontier` shows how far the background scan has reached: chars, disp (display columns), byte_rel (bytes relative to row start), and complete flag.
Queries beyond the scanned frontier return `None` (non-blocking); blocking is opt-in via `wait_until_known`.
`LongLineIndex.spliced(old, new_source, edit)` builds the index of an edited row without rescanning it (see "Long Rows After Edits").
`LongLineIndex.rebased(old, new_source)` moves an index onto a source with identical bytes (see "Rebase After a Save").

`foreground` — The `Foreground` gate that lets the scans give way to the UI thread (see "Thread Model").
Both indexes accept it through a `foreground=` argument.

`casefold` — Simple one-to-one case folding and the table of case variants (see "Search").

`search` — The needle compiler, `Matcher`, `SearchJob` and their types (see "Search").

**I/O strategy (ACT1 measurements):**

`PreadSource` uses `os.pread` behind a small LRU block cache: 64 KiB blocks, 128 blocks (8 MiB total).
One shared cache per source instance; scan reads bypass it via `cache=False`.
Memory map was rejected: warm window reads are 5.6x faster with mmap (1.3 us vs 7.0 us per 4 KiB read), but mmap dies with SIGBUS on file truncation and pread cannot; scan speed is equal (about 2.5 GB/s warm, 1.4 GB/s cold); fstat change checks cost about 1.8%.

**Change detection:**

`fstat` check on size and mtime_ns before each read, including scan reads.
Short-read rule: a pread result shorter than requested raises `SourceChanged` because the file shrank.
Short data is never cached; a failed source stays failed.
OSError during read becomes `SourceChanged`.
`SourceChanged.kind` is `TRUNCATED` for a short read or a smaller size, `UNREADABLE` for an `OSError` and `MODIFIED` otherwise.
`check_path(path, held)` in `core/save.py` compares the resolved path with a held `FileIdentity` (see "External Changes").

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
Their spacing is at most the `checkpoint_chars` argument, 65,536 characters by default; a lazy document passes 8192 (see "Document Layer").
Each scan block of 1 MiB adds a shorter tail piece when its length is not a multiple of the spacing.
For a 200 MB line at the default spacing: 3,201 checkpoints (`checkpoint_count`), about 77 KB of arrays.
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

**Edit-awareness:**

The indexes describe the immutable original file bytes and are never rewritten.
The piece table answers row queries over the edited document from them (see "Piece Table").
A long-line index is kept across an edit by splicing its checkpoints (see "Long Rows After Edits").

**Memory budget while reading (5 GB file, REQ-2):**

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

The layer holds the editable lazy documents built over the core piece table.
It also holds the documents and the edit history vendored from Textual 8.2.8.
The stock `Document` stays only as the base of `SyntaxAwareDocument`, the mirror that feeds syntax highlighting; the widget never uses it as its own document.

**Lazy classes:**
- `LazyDocument` — editable document over a `PieceTable`; decodes rows on demand with capability methods that never wait.
  Its edit methods are `replace_range` (text), `splice` (piece references between locations) and `splice_bytes` (piece references between byte offsets, used by undo and redo).
  `selection_content` returns the bytes of a selection as piece references without reading them.
  `search_plan(offset, limit)` returns a `SearchPlan` (revision, length and parts) in one lock hold, and `revision` counts the committed edits (see "Search").
- `LazyWrappedDocument` — wrapping layer over `LazyDocument`; manages grid wrap sections and a sparse vertical size estimate.
- `LazyConfig` — tunable thresholds and cache sizes with defaults; `sync_scan_limit` (1 MiB) is the largest source scanned on the constructing thread.

**Edit classes (vendored, changed in ACT4):**
- `Edit` — one edit; after `do` it holds `start_byte`, `removed` and `inserted` (piece references) and `end_location`.
- `EditHistory` — undo and redo stacks of batches of edits (see "Undo and Redo").

**Row classes (by content byte length):**
- Short: 0 to `word_wrap_limit` (default 64 KiB); stock word wrap, decoded whole.
- Medium: above `word_wrap_limit` up to `long_row_threshold` (default 1 MiB); grid wrap when soft wrap is on.
  A medium row is decoded whole and cached (an LRU of `text_cache_rows` rows), but it is drawn through a window like a long row.
- Long: above `long_row_threshold`; never decoded whole; windowed rendering and a byte-anchored cursor.

**Capability methods (on `DocumentBase`; lazy documents override):**
- `is_long(row)` — return whether the row must never be decoded as a whole.
- `line_length(row)` — return the character count, or `None` if not yet known.
- `row_byte_length(row)` — return the UTF-8 byte length of the row content.
- `column_slice(row, start, stop)` — return characters [start, stop), at most 8192 characters on a long row.
- `has_char_at(row, column)` — return whether the row has a character at column.
- `display_column(row, column, tab_width)` — return the display column of a character column, or `None` when unknown.
- `column_at_display(row, x, tab_width)` — return the character column covering display column x, or `None` when unknown.
- `byte_offset(row, column)` — return the byte offset from document start, or `None` when unknown.

**Checkpoint spacing:**
A lazy document creates its long-row indexes with `index_step(config)`, which is `min(checkpoint_chars, 8192)`.
With the defaults this is always 8192 characters, so every query decodes at most one window.

**Cursor state machine (`_cursor_anchor.CursorMachine`):**
The cursor on a long row is a byte position plus a column that is exact or estimated.
The machine accepts an `Op` (`LEFT`, `RIGHT`, `WORD_LEFT`, `WORD_RIGHT`, `HOME`, `END`, `UP`, `DOWN`, `PAGE_UP`, `PAGE_DOWN`, `SELECT_EXACT`, `GOTO_COLUMN`, `WRAP_TOGGLE`) and returns a `Verdict`: `DONE`, `PENDING` or `IGNORED`.
See "Cursor State Machine" below for the states and transitions.

### 3. **Widget Layer** (`nova_editor/widget/`)

Textual widget and lazy rendering support.

**Core module:**
- `NovaTextArea` — the main editor widget (vendored from Textual 8.2.8, renamed from `TextArea`).
  It always uses a `LazyDocument`: rows are decoded on demand, medium and long rows are drawn through windows, and the cursor on a long row goes through the state machine.
  Bytes that are not valid UTF-8 are drawn as U+FFFD with the width of one cell; the document keeps the escaped character, so selection and delete act on the byte (REQ-13).

**Helper modules:**
- `_text_area_theme.py` — theme support for syntax highlighting.
- `_lazy_window.py` — windowed rendering for medium and long rows; decodes at most 8192 characters per window.
- `_long_row_cursor.py` — cursor machine integration and provisional window layout for long rows.
- `_search_run.py` — `SearchRun`, the state of one running search, and `run_search_thread`, the body of the `nova-search` thread.

**Public API (`NovaTextArea`):**
- `open(source, language, soft_wrap, config, highlight_limit, **kwargs)` — open a file; returns the widget ready to mount.
  A file of up to `SMALL_FILE_LIMIT` (1 MiB) is read into memory and scanned on the calling thread, so its counts are exact at once and no file stays open.
  A larger file is read on demand and scanned in the background.
- `NovaTextArea(text=...)`, `load_text(text)` and the `text` setter build a `LazyDocument` over the UTF-8 bytes of the text, scanned on the calling thread whatever the size.
- `text` (property) — the decoded document up to `TEXT_LIMIT` (8 MiB); `""` above it (see "Known Limit: `text` Above 8 MiB").
- `read_only` — as in stock; a lazy document is no longer read-only.
- `document` — the `LazyDocument`.
- `clipboard_cap` (class attribute) — largest selection in bytes that is also copied to the system clipboard (see "Clipboard").
- `cursor_state` (read-only) — the `CursorState` of the cursor; always `RESOLVED` off long rows.
- `cursor_byte_offset` (read-only) — the absolute byte offset of the cursor.
- `column_exact` (read-only) — true when the cursor column is exact (`RESOLVED`).
- `cursor_location` — the row and column of the cursor; an estimate while `PROVISIONAL`.
- `pending_progress` — fraction in [0, 1) of a deferred jump; `None` when nothing is pending.
- `line_count` (read-only) — a lower bound on the row count; exact when indexing is complete.
- `line_count_exact` and `indexing_complete` (read-only) — true when the line count is final.
- `find_matching_bracket` — returns `None` for a document above `BRACKET_SEARCH_LIMIT` (1 MiB), because a search would decode arbitrarily many rows.
- `goto_line(line)` — go to a 1-based line; returns `None`, and a line the scan has not reached stays pending.
- `goto_byte(offset)` — go to an absolute byte offset; returns `None`, and an offset beyond the scanned frontier stays pending.
- `cancel_pending()` — cancel a pending jump or deferred cursor operation; the action is bound to Escape while one is pending.
- `toggle_wrap()` — flip `soft_wrap`; `nova_edit` binds it to F4.
- `close()` — cancel the scans and close the source (also called on unmount); a running save is cancelled and its terminal message is posted at once.
- `file_path` — the file the document is bound to; set by `open(path)` and by every successful save.
- `modified` (read-only) — whether the text differs from the last saved state (see "Modified State").
- `saving` (read-only) — whether a save runs.
- `save(path=None, *, overwrite=False) -> bool` — start a save (see "Streaming Save" and "Save As and Confirmations").
- `cancel_save()` — ask a running save to stop; nothing happens when none runs.
- `reload() -> bool` — discard the edits and show the file as it is now (see "Reload").
- `check_external_change() -> ChangeKind` — synchronous check of the file on disk (see "External Changes").
- `refresh_after_rebase()` — re-point the cursor holders after a rebase; the widget calls it itself.
- `search(needle, *, backward=False, case_sensitive=True, wrap=True) -> bool` — start a literal search (see "Search").
- `cancel_search()` — ask a running search to stop; nothing happens when none runs.
- `searching` (read-only) — whether a search runs.
- `search_settings` (class attribute) — `SearchSettings` (chunk, progress interval, tier switch); tests lower it.
- `select_all` is bound to `ctrl+shift+a` and `f8`; F7 belongs to the search of `nova_edit`.

**Message classes (posted by the widget):**
- `IndexProgress(count, complete)` — posted at most 10 times per second while the line scan grows, and once at completion.
- `IndexingComplete` — posted once when the line scan finishes.
- `SourceChanged(reason, kind)` — posted once if the file behind a lazy document changed or could not be read; `kind` is a `ChangeKind` (see "External Changes").
- `JumpProgress(fraction)` — posted when a deferred jump starts or advances, at most 10 times per second.
- `JumpCompleted(row, column)` — posted when a goto has moved the cursor; `column` is an estimate while `PROVISIONAL`.
- `JumpRejected(reason)` — posted when a goto target is out of range.
- `EditRefused(reason)` — posted when an edit, undo, redo or copy is refused because a position is not resolved yet (see "Known Limit: Edits Refused at Unresolved Positions") or because edits are locked (see "Edit Lock").
- `SaveProgress(phase, done, total)` — posted at most 10 times per second while a save runs.
- `Saved(path, length)`, `SaveFailed(error, stage, path)` and `SaveCancelled` — the terminal messages of a save; exactly one is posted per save.
- `SaveNeedsConfirmation(kind, path)` — posted instead of starting a save that needs consent.
- `Reloaded` and `ReloadFailed(error, path)` — the outcome of `reload`.
- `SearchProgress(done, total, phase)` — posted at most 10 times per second while a search runs.
- `SearchFound(start, end, row, column, wrapped)`, `SearchNotFound(needle)`, `SearchCancelled(reason)` and `SearchFailed(error)` — the terminal messages of a search; exactly one is posted per search.

**Thresholds (defaults, tunable via `LazyConfig`):**
- `word_wrap_limit = 65536` — rows above this use grid wrap instead of word wrap.
- `long_row_threshold = 1048576` — rows above this are never decoded as a whole.
- `highlight_limit = 1048576` (argument of `open()`) — files larger than this are not syntax highlighted.
- `sync_scan_limit = 1048576` — sources up to this size are scanned on the constructing thread.

### 4. **Application Layer** (`nova_editor/app.py`)

Standalone Textual app (`NovaEditApp`) providing:
- Every file is opened through `NovaTextArea.open`; there is no eager path and no `--lazy` flag.
- `Ctrl+S` saves; without a file it opens the path bar.
- `F2` opens the path bar for save as; `F5` reloads the file; `Escape` cancels a running save (see "nova_edit Bars and Keys").
- `Ctrl+Z`, `Ctrl+Y`, `Ctrl+X`, `Ctrl+C` and `Ctrl+V` are the stock bindings for undo, redo, cut, copy and paste.
- `Ctrl+Q` quits; during a save it cancels the save and waits at most 2 seconds.
- `F4` toggles soft wrap.
- `F7` opens the search bar, `F3` and `Shift+F3` repeat the search forward and backward (see "Search").
- `Ctrl+G` shows or hides the goto bar for line or byte offset navigation (`@N` syntax for byte offsets).
- `Escape` closes the goto bar or the path bar while it has the focus, cancels a pending jump in the editor, cancels a running search, and cancels a running save.
- Footer showing the file path and keyboard shortcuts.
- Entry point `main()` for the `nova_edit` CLI command.
- Timing hook via the `NOVA_EDIT_TIMING_FILE` environment variable (writes `FIRST_CONTENT <ns>` when content first renders).

**GotoBar:** Inline input field that accepts `N` (line number) or `@N` (byte offset); Enter navigates and Escape closes it.

**PathBar, SaveBar and ConfirmBar:** the bars of the save (see "nova_edit Bars and Keys").

**SearchBar and SearchStatus** (`nova_editor/search_bar.py`): the input of the search and its status line (see "Search").

**TimedNovaTextArea:** Wrapper that logs the first content render time for benchmarking.

---

## Converged Document Model

There is one document path.
`NovaTextArea(text=...)`, `load_text` and every file opened with `NovaTextArea.open()` build a `LazyDocument`.
The stock `Document` is no longer the document of the widget, the eager branch of `nova_edit` and `--lazy` are gone, and the editor is no longer read-only (DEC-17).

**Sources:**
`text=` encodes the text as UTF-8 with `surrogateescape` and wraps the bytes in a `BytesSource`.
`open()` reads a file of up to 1 MiB into a `BytesSource`, so no file stays open.
A larger file uses a `PreadSource` and the background scan.
A source of up to 1 MiB, and every `text=` source whatever its size, is scanned on the calling thread (`LineIndex.scan_now()`), so counts are exact at once and tests are deterministic.

**Highlighting mirror:**
A document up to `highlight_limit` also feeds a `SyntaxAwareDocument` that holds its text.
`LazyDocument` forwards every edit to it with `replace_range`, so the parse tree follows the edits.
This is the only remaining use of the stock `Document` classes.

**Line terminators:**
Rows end at LF, CRLF or lone CR only, so the document differs from the stock widget, which split on `str.splitlines` separators (DEC-13).
The golden traces in `tests/nova_editor/golden/` pin the stock behaviour on small files.

---

## Piece Table

The document is the byte string that results from concatenating pieces in order.
Original bytes are never copied: a piece only names a range of a source.

**Piece:**
A `Piece` is `(src, a, b, breaks, row0, first_is_lf, last_is_cr)`.
`src` is `0` for the original file or `k >= 1` for add segment `k`, and `[a, b)` is a non-empty byte range of that source.
`breaks` counts the terminators of those bytes read on their own, and `row0` is the row, inside the source, that contains byte `a`.
The two flags tell whether the first byte is LF and whether the last byte is CR.

**Content:**
`Content` is an immutable tuple of pieces with its byte length, break count and `tail_chars`, the characters after its last break.
Undo records, the clipboard and `splice` exchange `Content`, so no text is copied.

**Counted tree (`PieceTree`):**
A B+ tree (fan-out 32) keeps the pieces in parallel `array` columns, 37 bytes per piece in the leaf columns.
That is the leaf slot only.
The total per piece, with the inner nodes and the `Piece` object, is 99 bytes (see "Memory Budget While Editing"); the design target of below 80 bytes was missed.
Every node carries the piece count and an aggregate `(length, breaks, first_is_lf, last_is_cr)`.
The aggregates combine associatively, so offsets and breaks are found in O(log n).
`splice(start, end, content)` replaces a byte range, returns the removed `Content`, merges adjacent pieces that are contiguous in one source, and rebalances.
Typing therefore extends one piece instead of adding one piece per key.

**Add store (`AddStore`):**
All added bytes live in numbered segments that are never rewritten.
A small append extends the tail segment, a `bytearray` of at most 64 KiB; then the tail is sealed into `bytes` and a new tail starts.
An append of 64 KiB or more becomes its own sealed segment, so a pasted text is held once.
Every segment keeps the start of every 32nd row, so a row query walks at most 32 rows inside memory.
One lock guards reads and appends.

**Row oracle (`PieceSource`):**
The original file and each add segment answer the same small protocol: `row_of(offset)`, `row_start(row)`, `breaks_between(a, b)` and `read(a, b)`.
The adapter for the original file (`OriginalSource`) sits on the `LineIndex`, so editing never starts a new scan.
It raises `RowNotIndexed` when the index cannot answer yet.

**Row queries (`PieceTable`):**
`PieceTable` offers `snapshot`, `row_range`, `lines` (at most 128 rows) and `row_at_offset` in document coordinates.
The start of row `r` is the end of the `r`-th break: the tree descends by break count, picks the piece, and asks its source for the row start.
The terminator length of a row is read from at most two bytes before its end.
Each answer keeps the per-call read budget of 4 MiB.
While the document is exactly the original (one original piece), every query goes straight to the `LineIndex`, so an unedited document costs what it cost before ACT4.

**Open tail:**
While the original scan is incomplete, the last original piece `[t, L)` stays outside the tree, because its break count is not final.
Its rows come from the original index on demand.
A position inside the scanned part cuts the tail at that position, which moves `[t, x)` into the tree with exact counts.
A position beyond the scanned part is not accepted for an edit.
When the scan completes, the next call folds the tail into the tree.
Row counts are lower bounds until then, as before.

**Junction rules:**
A boundary between a CR and an LF is rejected by the tree with `ValueError`.
The aggregate counts breaks over junctions between sources exactly, so the row count always equals a split of the joined bytes on `\r\n|\n|\r`.
A deletion can join a CR and an LF into one CRLF; the aggregate then reports one break fewer, and the document rebuilds its row caches from the row above the edit.
Undo and redo take the other half of a CRLF along when a recorded boundary falls inside one.

**Merge guard:**
Decoding is defined over the whole row, not per piece, so an edit can merge bytes into other characters (a truncated UTF-8 lead byte followed by inserted continuation bytes).
After every edit the document decodes at most three bytes on each side of each junction and compares the result with decoding the parts alone.
When they differ, it recomputes the end location from the table and does not splice the long index of that row.

**Newline rule:**
Text from the keyboard, the system clipboard and the API is normalised: every LF, CRLF and CR in it becomes one terminator `T`.
`T` is the terminator of the row that holds the insertion point, or the document newline when that row has none.
If the byte before the insertion point is CR, `T` must not start with LF, and if the byte after it is LF, `T` must not end with CR.
The candidates in order are the row terminator, LF, CRLF and CR, and the first valid one wins.
Bytes the user did not edit are never touched, so mixed line endings stay as they are (REQ-13).
A paste of internal clipboard content is not normalised.

**Invalid bytes:**
Invalid UTF-8 bytes are the characters U+DC80 to U+DCFF.
They are ordinary characters for cursor, selection and delete, and they encode back to the exact byte.

---

## Long Rows After Edits

A long-line index never sees the piece table.
`LazyDocument.long_index(row)` builds one over a `RowSource`, a `ByteSource` over the pieces of that row with offsets relative to the row start.
The original file and sealed add segments are immutable, and the tail segment only grows past the bytes a piece names, so a scan thread can read a `RowSource` while the UI thread keeps editing.

**Splice instead of rescan:**
An edit inside one long row, without a line break, builds the new index from the old one with `LongLineIndex.spliced(old, new_source, edit)`.
Checkpoints at or before the edit are kept, checkpoints inside the removed range are dropped, and checkpoints behind it are kept with their byte, character and display positions shifted by the deltas.
Exact checkpoints are added at the edit and at the end of the inserted text.
Inserted text longer than one checkpoint step gets interior checkpoints from a scan of the inserted bytes, on the calling thread, up to 1 MiB.
The cost is proportional to the number of checkpoints behind the edit.
When the old scan had not reached the edit, only the prefix is kept and the scan resumes from the old frontier.

**Rebuilt instead of spliced:**
The index of a row is rebuilt from the row start in the background when an edit adds or removes a line break, spans rows, inserts more than 1 MiB, or leaves the row at or below the long-row threshold.
It is also not spliced when the edit merged UTF-8 characters.
Columns beyond the scanned part are unknown (`None`) until the scan passes them.

**Re-keying:**
`LazyDocument` keeps its long indexes in an LRU keyed by row.
After an edit the indexes of rows above the edit keep their key, and the indexes of rows below keep their object under the key shifted by the row delta.
The index of the edited row is replaced by the spliced one, or retired when none was built.
A retired index is cancelled and listed until its scan thread has returned.
An index whose scan thread has ended is dropped from the list at the next edit or lookup, because it holds checkpoint arrays and a snapshot of its pieces.
The list holds at most 64 entries; the oldest ones beyond that are joined on a daemon reaper thread.

---

## Undo and Redo

An `Edit` records byte positions and piece references, not text.
After `do` it holds `start_byte`, `removed` and `inserted` (both `Content`) and the `end_location`.
Undo replaces `[start_byte, start_byte + inserted.length)` by `removed`, and redo replaces `[start_byte, start_byte + removed.length)` by `inserted`.
The byte offsets are exact because undo and redo are strictly last in, first out; locations only serve the selection and the wrap update.
After a deletion, undo needs no text: the removed pieces are put back, so a 1 GB delete costs one piece in the record.

**Typing coalescing:**
Within one batch, an insertion that starts where the previous insertion ended extends the previous `Edit`.
A backspace run and a delete run merge in the same way.
Edits with line breaks, or with escaped invalid bytes in the inserted text, are never merged.
A run of typing within one batch therefore leaves one record.
A batch holds at most `checkpoint_max_characters` (100) characters, so a million keystrokes leave about 10,000 batches of one coalesced `Edit` each.

**Batching and limits:**
The stock batching rules stay: a timer (2 s), a character limit (100), a newline, a paste and replacement versus insertion all start a new batch.
`EditHistory.max_checkpoints` defaults to `None`, which keeps every batch; the stock default of 50 is gone.
An undo or redo that needs an unresolved position is refused as a whole, and an applied part is rolled back so that the document is unchanged.

---

## Clipboard

**Internal clipboard:**
Copy and cut build a `Content` for the selection from piece references; no bytes are read.
The internal clipboard holds that content, its length and the text written to the system clipboard.
There is no size limit inside the editor, and invalid bytes stay exact.
A paste uses the internal content when `app.clipboard` still holds what the copy left there, otherwise it inserts `app.clipboard` as text.
A paste of internal content is a splice of piece references: a 1 GB paste copies nothing and adds only pieces.
A paste of outside text is held once in the add store, which is the only permitted growth (REQ-2).

**System clipboard:**
The system clipboard is written through `app.copy_to_clipboard` only when the selection is at most `NovaTextArea.clipboard_cap` bytes.
`clipboard_cap` is 2 MiB (2,097,152 bytes).
The system text is the decoded selection with U+DC80 to U+DCFF replaced by U+FFFD, so the system copy is lossy for invalid bytes and the internal copy is exact.
Above the cap, `notify` shows a warning: the system clipboard was not updated, and pasting inside the editor still works.

**How the cap was chosen:**
The rule is the largest measured size whose copy plus terminal write stays clearly under 20 ms at p95 on the development machine.
Cap 2 MiB (2,097,152 bytes); rule: largest measured size whose p95 of copy plus terminal write stays clearly under 20 ms in repeated runs (2 MiB: worst p95 14.1 ms over three runs; 3 MiB peaked at 19.8 ms and 4 MiB at 32.1 ms, so they are not used).

---

## Streaming Save

A save writes the document from its pieces to a temporary file and replaces the target with `os.replace`.
`NovaTextArea.save` starts a `SaveJob` on a daemon thread named `nova-save`; the document is never loaded as a whole.

**Steps of `SaveJob.run`:**
1. The target is resolved with `os.path.realpath`.
   A target that exists and is not a regular file fails at stage `prepare`, and so does a file without write permission ("read-only file").
2. The mode is that of the target, or `0o666 & ~umask` for a new file.
3. `mkstemp` creates `.<name>.<random>.tmp` in the directory of the target; the name part is cut to 100 bytes.
   The name goes into a module set that an `atexit` hook unlinks.
4. `posix_fallocate` reserves the expected length; `ENOTSUP`, `EOPNOTSUPP`, `EINVAL` and `ENOSYS` are ignored and other errors fail at stage `prepare`.
5. The job writes chunks until the document length is reached.
   For each chunk it checks the cancel flag, asks the document for the parts (`LazyDocument.plan`), reads them, writes them (partial writes are looped), feeds the bytes to a `RowScanner` and records the parts in a `SaveLayout`.
6. A short read fails the save at stage `write`.
7. After the loop the job calls `fsync` and `fchmod`, takes `fstat` and opens the temp file read only as the future source.
8. The last cancel check comes next, then `os.replace(temp, target)`; after it a cancel is ignored.
9. A best-effort `fsync` of the directory follows, and its errors are ignored.
10. The result is a `SaveResult` with the target, length, source, line index, layout and mode.

**Constants (`SaveSettings`):**
- Chunk of 1 MiB (`CHUNK`).
- `fsync` every 256 MiB (`FSYNC_EVERY`) and once more at the end.
- `preallocate` and `build_index` are on by default; `build_index=False` exists for the measurement baseline.
- Measured numbers: see the activity evidence.

**Plan:**
`LazyDocument.plan(offset, limit, unverified)` takes the document lock, asks `PieceTable.layout_range` for the runs `(src, a, b)` and returns `PlanPart(src, source, a, b)` items.
No byte is read under the lock, so the plan cannot raise `SourceChanged`.
Parts of the original are read with `cache=False`.

**Progress:**
The job reports `writing` (at most 20 times per second), `flushing` and `finishing`; the save thread then reports `history` while it translates the records.
The widget keeps the latest report, posts at most one callback to the UI thread at a time, and turns it into `SaveProgress` at most 10 times per second.
The job accepts a `Foreground` gate (`PauseGate`) and sleeps `pause_seconds()` after every chunk.
The widget passes no gate: the measurements of the activity show no save step over 50 ms, so the UI thread is never starved and the save needs no way to give way.

**Cancel:**
`cancel_save()` sets the flag of the job.
The job stops at the next chunk or before the replace; the latency is one chunk plus, in the worst case, one `fsync`.
A cancel before the replace leaves the target byte for byte unchanged and removes the temp file.

**Failure:**
Every failure raises `SaveFailed` with a `stage` (`prepare`, `write`, `flush`, `replace` or `internal`) and the `errno` of the cause.
`ENOSPC` and `EDQUOT` from the write or from `fallocate` fail at once.
A failed `fchmod` fails the save at stage `flush`, except `ENOTSUP` and `EOPNOTSUPP`.
An unexpected exception becomes `SaveFailed` with stage `internal`.
The `finally` closes the descriptors and unlinks the temp file.

**Terminal message:**
Exactly one of `Saved`, `SaveFailed` and `SaveCancelled` is posted per save, and the handler on the UI thread posts it.
If the file was written but the switch to it fails, the outcome is `SaveFailed` with stage `internal` and `committed` set (on the core exception and on the widget message), although the target already holds the new bytes.
That covers a failure of the save thread after the replace, of the translation of the history, and of any step of the finish on the UI thread (`_finish_save`): whatever it raises, the lock is lifted and exactly one message is posted.
A committed failure leaves the document on the old file: `apply_rebase` builds the new table and the rebased long indexes before it assigns any state, and closes the saved file itself when it fails.
Only a failure after the swap (the translation of the history) clears the history, because a half-translated history must not survive; the document is then on the new file.
`nova_edit` tells the user that the file was written and that F5 reloads it.
Closing the widget during a save posts `SaveCancelled` at once, or `SaveFailed` with `committed` when the replace already happened (`SaveJob.committed`); a refused post while unmounting is ignored.
The document registers the save thread with its closer, so the source is closed only after the writer returned.

**Special targets:**
- A symlink is followed: the target is replaced and the link stays; a dangling link creates the file it names.
- A directory that is not writable fails at `prepare` with "cannot create a temporary file in <dir>; use Save As", and so does a read-only file system.
  An in-place write is not offered, because pieces refer to the original bytes.
- A new file is created with the mode `0o666 & ~umask`, and the document continues on it.

---

## Edit Lock

`LazyDocument.lock_edits(reason)` and `unlock_edits(reason)` refuse `replace_range`, `splice` and `splice_bytes` with `EditsLocked`, a subclass of `RowUnavailable`.
The widget treats it like any refusal: bell, warning, `EditRefused(reason)`, nothing changes and the history batch is restored.
The locks are a list of reasons; the latest one is reported, and `unlock_edits` lifts one reason, or all when none is given.
The reasons are `"saving"` and `"file changed on disk"`.
A save holds `"saving"` from its start until the terminal handler runs on the UI thread, so the lock cannot stay set.
Cursor, selection, scrolling and goto stay live during a save.
`load_text`, `reload`, `open` and replacing the document raise `RuntimeError` while a save runs.
A copy during a save updates the system clipboard but keeps no internal clipboard record, so a later paste inserts text.

---

## Rebase After a Save

After the replace the document must stand on the saved file, so that the old original and the add store can be released.
`prepare_rebase` runs on the save thread and does the heavy work; `apply_rebase` runs on the UI thread under the document lock and does bounded work.

**`apply_rebase`:**
1. Build a new `PieceTable` over the saved file, its complete line index and a fresh add store.
2. Carry the legacy generations of the old table, plus the old files when the plan keeps them.
3. Replace the source, line index and table, and clear the row and text caches.
4. Rebase every cached long-row index with `LongLineIndex.rebased`; an index that cannot be rebased is dropped and rebuilt on demand.
5. Cancel the old indexes and close the old source on a closer thread, unless a legacy generation keeps it.
   A closed document closes the new source instead.
6. The widget then writes the translated contents back into the history and the clipboard record, marks the state as saved, sets `file_path` and the held identity, lifts the stale state and repaints.

`LongLineIndex.rebased` is `spliced` with an identity edit at the end of the row.
Checkpoints are kept, an unfinished scan resumes from its frontier, and row numbers, columns and cached measurements stay valid.
`apply_rebase` raises `ValueError` when the document changed since the save started; the widget then closes the new source and reports `SaveFailed` with stage `internal`.

A document opened with `text=` or from a small file stands on an adopted file descriptor after its first save.

---

## Undo Across Saves

Undo records and the internal clipboard hold piece references, and after a save those would name the old original and the old add store.
`Rebaser` rewrites them, so undo after a save restores the pre-save content without copying text that is already in the file.

**Collection:**
When the save starts, the widget collects every `Content` of the undo and redo stacks (`Edit.contents`) and of the clipboard record.
`Edit.rewrite` takes the translated contents back in the same order.

**Layout:**
The writer records every run `(src, a, b, out)` in compact arrays, 32 bytes per run, while the save runs.
`SaveLayout.intervals(src)` returns disjoint ascending intervals; a source range that occurs several times keeps its first occurrence.

**Translation:**
A generation 0 piece is split against the intervals of its source.
A present part becomes a piece of the new original at the translated offset, and adjacent parts merge, so no text is copied.
An absent part is an orphan: text that was deleted or replaced before the save, or typed and deleted again.
The aggregate, `breaks` and `tail_chars` of a rewritten `Content` are copied from the old one, because the bytes are identical.
Pieces of legacy generations stay as they are.

**Orphans:**
- Up to `UNDO_COPY_LIMIT` (8 MiB) of orphan bytes in total are read from the old sources and copied into the new add store, and the old files are released.
- Above it, the old original with its line index and the old add store become a legacy generation `g` of the new table.
  Orphan pieces keep their ranges and get `src = (g << 24) | k`, where `k = 0` is the generation's original and `k >= 1` an add segment.
  `Piece.src` is a `uint32` column: 8 bits of generation and 24 bits of segment.
- At most 255 generations exist.
  A save that needs a 256th clears the history and the clipboard record, and the widget shows "Undo history cleared: too many saves with large deletions."
- An `OSError`, `LookupError`, `ValueError` or `SourceChanged` in the translation falls back to `RebasePlan.retain_all`: every old reference is kept under the next generation.
  This is always correct and only wasteful.

Redo records are translated the same way, so undo, save and redo works.
Present parts never need a generation, so repeated saves with small deletions retain nothing.

---

## Modified State

`EditHistory` keeps a revision counter and a branch id.
Every batch stores the revision before and after it, and undo and redo move the revision with the batch.
A record after an undo opens a new branch, so the revision counter alone cannot reach a state of the old branch.
`modified` is `(revision, branch) != saved`, so undo back to the saved state reads unmodified, and so does the undo of typing after a save.
A save forces a new batch (`checkpoint()` at its start and in `mark_saved()`), so coalesced typing never spans a save.
`EditHistory.clear` resets the saved state to the current one, so `modified` is false afterwards.

---

## Row Scanner and the Stream Index

`RowScanner(stride, threshold)` is the row-boundary scan that `LineIndex` used to hold privately.
`feed(block, base, final)` scans a block, `take()` returns the stride entries and long rows found since the last call, and `finish(length)` returns the last row when it is long.
A CR at a block edge is carried, so a CRLF split between two blocks is one terminator.
`LineIndex._scan` calls it, and the behaviour of the scan did not change.

The save thread feeds the scanner with the bytes it writes, so the index of the saved file costs no extra read.
`LineIndex.from_scan` creates a complete index over the saved file from the entries and long rows; it refuses `start()` and `scan_now()`.
The document switches to this index, so after a save there is no rescan and no blank view.
A test compares it with a fresh scan of the saved bytes.

---

## External Changes

The file behind a document can change while it is open.
`ChangeKind` names what differs:
- `UNCHANGED`.
- `MODIFIED`: the same inode with another mtime, and a size that is equal or larger.
- `TRUNCATED`: a smaller size, or a short read.
- `REPLACED`: the path names another inode.
- `DELETED`, and `CREATED` for a file expected to be new that exists.
- `EXISTS` for a save as onto an existing file, and `UNREADABLE`.

**Detection points:**
- Every read of uncached content of a large original checks the descriptor, as before, and the `SourceChanged` carries a kind.
  Cached blocks are served until the next miss.
- `save` compares the resolved path with the held identity before it starts.
  The held identity is that of the descriptor for a `PreadSource`, and the `stat` taken around the read for a small file held in memory.
- `NovaTextArea.check_external_change()` is public and synchronous: the descriptor check plus `check_path`.
  While a save runs it reports `UNCHANGED`, because the replace of the save would look like a change.
- The poll of `nova_edit` splits the check so that no widget state is touched off the UI thread.
  `begin_external_check()` (UI thread) captures the document, the path, the held identity and the save epoch; `ExternalCheck.run()` does only the `stat` calls and runs on a worker thread every 2 seconds; `apply_external_check()` (UI thread) applies the result.
  The poll is skipped while a save runs or while the previous check still runs.
  A result is dropped when a save began or ended since the check began (the save epoch counts both), so the replace of the app's own save is never reported as `REPLACED`.
- When the terminal gets the focus back (the Textual `AppFocus` event), `nova_edit` runs the same check at once (design 9.1).

A small document held in memory has no read-time detection; the poll and the save cover it.
Truncating a displayed file never ends the process, because the editor uses `pread` and no memory map.

**Stale view policy:**
After a detection the widget posts `SourceChanged` once and enters the failed state.
Rows that are cached keep showing, possibly stale, and uncached rows render blank.
The edit lock `"file changed on disk"` is set, so edits, undo and redo are refused with that reason.
Nothing is discarded: the pieces, the add store and the history stay, `modified` stays true, and selection and scrolling keep working.
The state ends by `reload`, by a successful save, or by `close`.
Later calls of `check_external_change` return the same kind without posting again.
A later report during the stale state keeps the more severe kind (`TRUNCATED`, `DELETED` and `REPLACED` over `MODIFIED`) and posts nothing.
`nova_edit` does not ask while a save runs: it remembers the `SourceChanged`, and when the save ends with a failure that did not commit, or with a cancel, it re-runs the check and announces what it finds.
A save that commits lifts the state itself, so the remembered change is dropped.

---

## Reload

`reload()` discards the edits and builds a new document the way `open` does; the language, highlight limit and configuration are remembered.
It replaces the document first and only then clears the history and the clipboard record, ends the stale state, keeps the cursor row when it still exists (else it goes to the last row) and posts `Reloaded`.
When the file cannot be read, it posts `ReloadFailed`, returns False and leaves the old document, the history and the stale state; whatever the failed attempt opened (the source or the new document) is closed on every failure path.
The UI thread waits for row 0 of the new document, bounded by one second (the scan resolves it within milliseconds), because the cursor, the scrollbar and the layout watchers cannot cope with an unresolved first row; the restore of the old cursor row is a pending jump.
It returns False without a file, and raises `RuntimeError` while a save runs.
In `nova_edit`, F5 on a modified document asks first, and F5 during a save shows a warning.

---

## Save As and Confirmations

`save(path=None, *, overwrite=False)` returns True when a save started.
It returns False, and posts nothing, when a save runs, the widget is closed, there is no target, or a plain save finds the document unmodified.
A save as writes even when nothing changed, and afterwards the document is bound to the new file.

| State of the file | Plain save (Ctrl+S) | Save as (F2) |
|---|---|---|
| `UNCHANGED` | Writes | Writes; an existing target needs confirmation (`EXISTS`) |
| `MODIFIED`, `TRUNCATED`, `REPLACED`, `DELETED`, `CREATED` | Posts `SaveNeedsConfirmation(kind, path)`; with `overwrite=True` it writes | Writes |

A save that needs consent posts `SaveNeedsConfirmation` and starts nothing.
After `MODIFIED` or `TRUNCATED` the job reads the original with `unverified=True`: only a short read fails, so the unchanged parts come from the file as it is now.
A truncation that removed bytes the document still needs fails with "the file was truncated; the unchanged parts cannot be read", and the target stays untouched.
After `REPLACED` or `DELETED` the held descriptor still names the original inode, so the save is exact.

**`nova_edit` guards:**
- A file that existed but could not be read refuses Ctrl+S and opens the path bar for a save as.
- A file expected to be new that exists at save time is `CREATED`, which asks for confirmation.
- Ctrl+S without a file opens the path bar.
  The first save of a new file creates it even when it is empty.
- Ctrl+S on an unmodified document that has a file shows "No changes to save".
- `nova_edit` refuses to open a file that is not regular, because opening a FIFO blocks.

---

## nova_edit Bars and Keys

| Key | Action |
|---|---|
| `Ctrl+S` | Save |
| `F2` | Path bar for save as, prefilled with the current path; Enter saves and Escape closes |
| `F5` | Reload; a modified document shows the confirm bar first |
| `Escape` | Cancel a running save; the binding is active only while a save runs |
| `Ctrl+Q` | Quit; during a save it cancels, waits at most 2 seconds and exits |
| `F7` | Search bar; Enter searches forward, Escape closes it |
| `F3` | Repeat the search forward, also with the bar closed |
| `Shift+F3` | Repeat the search backward, also with the bar closed |
| `Alt+C` | In the search bar: toggle between case-sensitive and ignore case |

`Ctrl+Q` does not ask about unsaved changes.

**SaveBar:**
A status line that is hidden when idle.
It shows `Saving  1.2 / 5.0 GiB  24 %  Esc cancels`, then `Flushing`, `Finishing` or `Preserving undo history`, and a result line for 4 seconds.
A failure shows the OS message and the stage and stays until a key is pressed.

**ConfirmBar:**
A key driven question for overwrite, reload and external change: `O` overwrite, `A` save as, `R` reload and `Esc` keep.
Only the keys that the question lists are active.

**PathBar:**
An `Input` like the goto bar.

All bars are plain widgets, not dialogs, so they have no entry in `src/tools/dialog_tester.py`.

---

## Search

`NovaTextArea.search` finds literal text in the whole document, forward or backward, in the original bytes and in the edited ones.
It runs on a worker thread with progress and cancellation, and the UI keeps answering while it runs.
The layers are `core/casefold.py` and `core/search.py` (Textual-free), `LazyDocument.search_plan`, `widget/_search_run.py` with the glue in `_text_area.py`, and `search_bar.py` in the app.

### The Key Move

`F7` opens the search, as in Midnight Commander.
The stock binding of `select_all` was `ctrl+shift+a,f7`; it is now `ctrl+shift+a,f8`.
`Ctrl+F` stays the stock cursor-right binding.

### Needle Model

The needle is a `str`, encoded as UTF-8 with `surrogateescape`.
An escaped byte (U+DC80 to U+DCFF) in the needle matches that raw byte, so invalid bytes are searchable (REQ-13).
A match is a byte range `[start, end)` that starts and ends on a character boundary of the decoding.
A raw byte such as 0xA9 therefore does not match inside a valid `é`.
A line break in the needle (`\n`, `\r\n` or `\r`) counts as one character and matches exactly one document terminator.
`compile_matcher` raises `SearchError` for an empty needle, a lone surrogate outside U+DC80 to U+DCFF, and a needle over the limits (see "Known Limit: Needle Size").

### Case Folding

Case-insensitive search folds each character on its own, one to one (`fold1`).
`fold1(c)` is `c.casefold()` when that is one character, else `c.lower()` when that is one character, else `c`.
The variants of a character are every code point with the same `fold1`, so `k` also matches the Kelvin sign and `s` also matches the long s.
ASCII letters use a static table; any other character builds the full table once, on first use, under a lock.
A test proves that the static table equals the one derived from the full scan.

### Tiers

The matcher works on one contiguous window of bytes and picks a tier per window.

| Tier | When | How |
|---|---|---|
| find | case-sensitive, no line break in the needle | `bytes.find` and `bytes.rfind` |
| ascii | case-insensitive, the folded needle is ASCII, the window is ASCII | `window.lower()`, then `find` or `rfind` |
| pattern | case-insensitive on a window with a non-ASCII byte, or any needle with a line break | a compiled byte pattern |

A case-insensitive needle with a non-ASCII class cannot match an ASCII window, so that window is skipped.
One non-ASCII window costs only that window; the rest of the file stays on the fast path.

The pattern is built from escaped bytes only: alternations of the encoded variants and fixed lookarounds for line breaks.
No user text is read as pattern syntax, and no pattern feature is exposed.

A backward window runs the reversed pattern on the reversed window, because `re` has no reverse search.
All matches of one needle have the same number of characters, so the match with the greatest end is also the one with the greatest start.

### Boundary Rules of the Matcher

A candidate is checked after the byte search, with the context bytes of the window.
A needle that starts or ends with an escaped byte rejects a candidate that begins or ends inside a valid multi-byte character.
A needle with two or more adjacent escaped bytes rejects a candidate whose bytes decode to fewer characters than the needle has (a line break counts as one).
Without that rule the bytes of one valid character would match two escaped bytes.
A rejected candidate continues one byte later.

### Throughput Depends on the Needle

The throughput of the pattern tier depends on the needle.
A needle that starts with a literal prefix is fast, because `re` skips to the prefix.
A needle that starts with a character that has case variants runs at about 420 MB/s (see "Search Measurements").

### Windows and Overlap

The document is never planned whole.
Each unit asks `search_plan(offset, limit)` for the parts of one window (no I/O under the lock) and reads them outside the lock.
Parts of the original are read with `cache=False`, so a search never evicts the blocks of the UI.
A window owns `CHUNK` (256 KiB) bytes of match starts (forward) or match ends (backward).
A forward window reads 3 bytes of head context, `Lmax - 1` bytes of overlap and 3 bytes of tail context.
`Lmax` is the sum over the needle characters of the longest encoding (a line break counts 2).
A backward window reads `Lmax - 1` bytes and 3 bytes of context before its range, and 3 bytes of context after it.
A match belongs to the window that owns its start (forward) or its end (backward), so none is found twice and none is lost.
Piece boundaries and chunk boundaries are the same case, because the matcher sees one contiguous buffer.

### Regions and Wrap

The origin is a byte offset on a character boundary.
Forward, region 1 is `[origin, length)` by match start, and region 2 is `[0, origin)`.
Backward, region 1 holds the matches that end at or before the origin, and region 2 holds the matches that end after it.
Region 2 runs only when `wrap` is true, which is the default.
A match found in region 2 is reported with `wrapped=True`.
`total` is the document length with wrap, and the size of region 1 without it.

### Repeat Rule

A forward search starts at the end of the selection (the cursor when empty), and a backward one at its start.
A match is selected with the cursor at its end (forward) or at its start (backward).
A repeat therefore continues from the match and never overlaps it in the search direction.
A needle `aa` finds `aaaa` twice, not three times.
When the only match is the current selection, the search finds it again after wrapping.
The default is case-sensitive, which is the fast path.

### Placing the Match

The match is placed through the jump machinery (`_Jump` with `select_to`), so the selection is always exact.
When both ends are below the frontier of the line index and in rows that are not long, the selection is set at once and the view scrolls to it.
An end beyond the frontier keeps the jump pending, with `JumpProgress`, until the scan reaches it.
A match in a long row waits until the long index has resolved both ends, then selects.
Until then the cursor does not move, and Escape cancels.
`SearchFound` is posted when the selection is set.

### Progress and Cancel

The core reports at most 20 times per second (`progress_interval` 0.05 s).
The widget keeps the latest report, posts at most one callback to the UI thread at a time, and turns it into `SearchProgress` at most 10 times per second, never after the terminal message.
`SearchJob.cancel()` sets an event that is checked before every unit, so the latency is one unit plus one read.
The `Foreground` gate of the document is asked before every unit, and the job sleeps `pause_seconds()` while the UI is busy.
`Escape` cancels a running search: `check_action` keeps `cancel_pending` active while a jump is pending or a search runs.

### Messages

Exactly one terminal message is posted per search, by the handler on the UI thread: `SearchFound`, `SearchNotFound`, `SearchCancelled` or `SearchFailed`.
The thread posts its outcome from a `finally`, so no defect leaves the widget in the searching state.
`SearchCancelled.reason` is `cancelled`, `replaced`, `text changed` or `reloaded`.
A second `search` replaces the running one and posts `SearchCancelled` with the reason `replaced` for it.
A needle that cannot be searched posts `SearchFailed`, and `search` returns False.
A change of the source during a read ends the search with `SearchFailed` and the stale view policy (see "External Changes").
When a result arrives for a document whose `revision` changed, it is dropped as `SearchCancelled("text changed")`.
Without a match, the cursor and the selection stay unchanged.

### API

`search(needle, *, backward=False, case_sensitive=True, wrap=True) -> bool` returns whether a search started.
`cancel_search()`, `searching` and `search_settings` complete the widget API (see "Public API").
`SearchSettings(chunk, progress_interval, tier)` is for tests and the harness; `tier` forces a tier.
The core exports `SearchSpec`, `SearchJob`, `SearchSettings`, `SearchPlan`, `SearchPlanner`, `SearchResult`, `SearchProgress` and the errors `SearchError`, `SearchCancelled` and `SearchStale` from `nova_editor.core`.

### nova_edit

`F7` opens the bar, Enter searches forward and `Escape` closes the bar.
`F3` and `Shift+F3` repeat the last needle forward and backward, also with the bar closed.
`Alt+C` toggles the case in the bar, and the placeholder shows `Search (case-sensitive)` or `Search (ignore case)`.
The status line shows `Searching 42% (2.1 of 5.0 GiB), Esc cancels` while it runs.
Then it shows `Found`, `Wrapped to the top`, `Wrapped to the bottom`, `Not found: <needle>`, `Search cancelled` (with `: <reason>` unless the user cancelled) or `Search failed: <error>` for 3 seconds.
A needle is shown at most 40 characters in `Not found`.
The bar and the status line are plain widgets, so they have no entry in `src/tools/dialog_tester.py`.

### Search Measurements

All numbers are from the development machine (i5-14600K, 62 GiB, Linux 7.0.0, Python 3.12, Textual 8.2.8), on the 5 GB file `normal-5g.txt` (5,368,709,120 bytes), warm cache, unless a line says stand-in.

| Case | Result |
|---|---|
| Miss, case-sensitive (find tier), full circle | 1.36 to 1.37 s, about 3.9 GB/s, 12 to 14 progress messages |
| Miss, case-insensitive, needle with a literal prefix | 1.6 s, 3.3 GB/s; backward 2.7 s, 2.0 GB/s |
| Miss, case-insensitive, first character with variants (pattern tier) | 12.8 s, 419 MB/s |
| Generated ASCII 1 GiB (stand-in), case-insensitive (ascii tier) | 0.51 to 0.53 s, about 2.1 GB/s |
| Generated non-ASCII 1 GiB (stand-in), pattern tier | 430 to 435 MB/s; with a line break in the needle 400 MB/s |
| Match in the first block, to the terminal message | 1.4 to 2.0 ms |
| Match in the last block | 1.87 to 2.0 s (it scans the file) |
| Cancel at 50 percent, to the terminal message | 0.33 to 0.51 ms |
| Memory, 5 GB search | RssAnon max 40.1 MiB, increase over the baseline under 0.01 MiB |
| 5 GB edited text (1,000 edits, 100 MB paste, needle across a piece boundary) | found at the right offsets, 1.49 s and 0.79 s, RssAnon max 50.4 MiB |
| 200 MB line, marker at column 100,000,000 | selected exactly, 1.9 to 2.0 ms from result to selection |
| Long index resolves byte 100,000,000 / 199,000,000 | 1.09 s / 2.19 s |

UI steps during a search on the 5 GB file stay under 50 ms with wrap off (maximum 36.6 ms sensitive, 30.2 ms pattern).
With wrap on, no step exceeds 50 ms except the first `ctrl+end` of a run (about 360 to 480 ms, with and without a search) and one `up` step of 53.6 ms in one of 4 pattern-tier runs.
The first `ctrl+end` is the cold layout of the wrapped end of the 5 GB document, which exists without a search.
The `up` step of 53.6 ms did not reproduce in 5 further runs.
On the 200 MB line the steps during the search peak at 28.8 ms.

The chunk sweep (one run each) measured 2.9, 3.6 and 4.4 GB/s for the find tier and 404, 425 and 422 MB/s for the pattern tier at 64 KiB, 256 KiB and 1 MiB.
The longest UI step was 12.6 to 18.5 ms, and the constant stays 256 KiB.
The fold table has 1,424 classes and 2,878 entries, the largest class has 4 members, and the first non-ASCII use builds it in 89 to 98 ms and about 268 KiB.

The harness subcommands are `search-5g`, `search-latency`, `search-cancel`, `search-edited`, `search-longline`, `search-sweep`, `search-gen` and `search-fold` of `tools.measure_view`.

---

## Memory Budget While Editing

REQ-2 limits the editor to 200 MB.
All figures are measured with `uv run python -m tools.measure_view` (subcommands `pieces`, `undo-record` and `edit-memory`) on the development machine.

**Pieces:**
A piece costs about 99 bytes in a tree of 10^6 pieces (98.2 bytes by `tracemalloc`, 99.4 bytes by `RssAnon`).
That is the total per piece, including the tree and the `Piece` object, and the design target of below 80 bytes was missed.

**Splice and row query at 10^6 pieces:**
One splice takes 0.20 ms (insert) and 0.15 ms (delete) at the median, and 0.23 ms and 0.17 ms at p95.
One `row_range` takes 0.10 ms.

**Undo records:**
A record costs about 1.0 to 2.3 KB.
Coalesced typing costs 15 bytes per keystroke.
A paste record is 977 bytes, a delete record 1,306 bytes, and a backspace run 2,256 bytes.

**Session peak:**
The peak `RssAnon` through select, a 1 GB delete, undo, redo, copy, paste and undo with window verification is 42.4 MiB on the 5 GB file and 50.3 MiB on the 200 MB line.
Both are far below the 200 MB limit.

---

## Wrap Semantics

Wrapping applies only when `soft_wrap` is on; it is off by default in `open()`, in `nova_edit` and in the constructor.
With soft wrap off, no row wraps and wide rows scroll horizontally.
With soft wrap on, the row class decides how a row wraps.

**Short rows (0 to 64 KiB):**
Stock word wrap (Textual's `compute_wrap_offsets`); fast because the row is decoded once.
Measured: 0.22 ms at 4 KiB, 0.90 ms at 16 KiB, 3.55 ms at 64 KiB.

**Medium rows (64 KiB to 1 MiB):**
Grid wrap: each section holds a fixed number of display columns.
The row is decoded whole and cached for measuring its width, and it is drawn through a window of at most 8192 characters.
Measured: 15.4 ms to compute wrap offsets of a 256 KiB row at width 113.

**Long rows (above 1 MiB):**
Grid wrap too, but the row is never decoded whole.
Its width comes from the long-row index, and sections are known only up to the scanned frontier.
Measured: 2.4 ms to render the window without wrapping at 256 KiB, 6.2 ms at 1 MiB and 21.5 ms at 4 MiB.

**Wrap measurement budget:**
One call that measures wrapped heights (`height`, `y_of_row`, `row_of_y`) decodes at most `MEASURE_MAX_BYTES` (4 MiB) of row bytes.
The same call measures at most `MEASURE_MAX_ROWS` (128) medium or long rows exactly.
Rows beyond the budget get a provisional height, and the estimate timer of the widget finishes them on later ticks.

**Estimates after an edit:**
The y of a block that is not measured is estimated from the mean extra height of the measured blocks.
An edit drops the measured blocks from the edit on, and the mean is pinned to its value before the edit while a block above the edit is still unmeasured.
Without the pin, measuring the blocks again would change the mean and move the estimated y of every unmeasured block above the edit, and the view would jump.
The pin is released when a block above the edit is measured, and it is not set when something was never measured or when every block above the edit is measured.
Until it is released, the estimate of the unmeasured blocks below the edit uses the mean from before the edit.

**Wrap toggle (F4 in `nova_edit`):**
`toggle_wrap()` flips `soft_wrap` and re-wraps the wrapped document for every row class.
A cursor on a long row keeps its byte: when wrap turns on, a provisional cursor becomes pending until the scan reaches it.

---

## Cursor State Machine (Long Rows)

A cursor on a long row is a byte position plus a column, and the byte is the truth.

**RESOLVED:**
The byte and the column are both exact.

**PROVISIONAL:**
The byte is exact, but the column is an estimate because the scan has not reached it.
The cursor still moves with left, right, word, home and end.
In no-wrap mode the row is drawn from a byte anchor until the column is exact.

**PENDING:**
A deferred operation or a wrap-mode jump waits for the scan.
The widget shows `JumpProgress` and Escape cancels.
While pending, every new operation is `IGNORED`; only a jump to a byte retargets.

**Start state:**
A machine starts `RESOLVED` when the column of its byte is exact, else `PROVISIONAL` at that byte.

**Transitions:**
- A jump to a byte with an exact column gives `RESOLVED`.
- A `RESOLVED` jump beyond the frontier gives `PROVISIONAL` without wrap and `PENDING` with wrap.
- Left, right, word, home and end move a `PROVISIONAL` cursor and stay `PROVISIONAL` unless the target column is exact.
- Up, down, page, select-exact, goto-column and wrap-toggle on a `PROVISIONAL` cursor defer: the machine becomes `PENDING` and the widget replays the operation after the cursor resolves.
- On a `RESOLVED` cursor these operations return `DONE` and the widget handles them exactly.
- `PENDING` becomes `RESOLVED` when the scan passes the target byte; a `PROVISIONAL` cursor resolves the same way.
- Turning wrap on makes a `PROVISIONAL` cursor `PENDING` on the same byte.

**Cancel:**
Cancelling a deferred operation keeps the provisional position.
Only a pending wrap jump to another byte restores the previous resolved anchor.

---

## Thread Model

Background scanning runs in daemon threads managed by `LineIndex` and `LongLineIndex`.

**Foreground gate (`nova_editor/core/foreground.py`):**
The widget calls `Foreground.touch()` on every key and mouse event.
A scan thread asks `pause_seconds()` between work pieces; if the UI was busy within the last 50 ms, the scan sleeps 1 ms before the next piece.
This hands the GIL to the UI without waiting for the 5 ms switch interval.

**Publishing to the UI:**
Scan callbacks run on the scan thread and call `post_message(events.Callback(...))`, not `call_from_thread`.
At most one such callback is outstanding; further progress is coalesced until the UI thread has handled it.
The UI thread then reconciles the cursor, refreshes sizes and posts `IndexProgress`, which is rate limited to 10 Hz.

**Shutdown:**
The UI thread never joins a scan.
`LazyDocument.close()` cancels every scan on the calling thread and starts a daemon closer thread.
The closer joins the scans and then closes the source; `wait_closed()` waits for it.
Cancelled long-row scans beyond the retired-list limit are joined on a daemon reaper thread.

**Save thread:**
A save runs on one daemon thread, `nova-save` (see "Streaming Save").
It reads and writes outside every lock and takes the document lock only for the plan of each chunk.
It posts its outcome with `post_message(events.Callback(...))`, like the scans, and the UI thread runs the terminal handler.
`LazyDocument.close()` waits for the save thread through its closer before it closes the source.

**Search thread:**
A search runs on one daemon thread per search, `nova-search` (see "Search").
It reads outside every lock and takes the document lock only for the plan of each window.
It reports progress and posts its outcome with `post_message(events.Callback(...))`, and the UI thread runs the terminal handler.
It is registered with `LazyDocument.join_on_close`, so `close` waits for it before the source is closed.
Unlike the save, it passes the `Foreground` gate and sleeps while the UI is busy.

---

## Performance Thresholds (Measured)

All measurements are on the developer's machine (i5-14600K) with the harness `uv run python -m tools.measure_view ...`.

**Syntax highlighting limit (1 MiB):**
Files above this size are not highlighted when opened lazily, to keep first-screen latency low.
First screen render: 97.6 ms at 64 KiB, 221 ms at 256 KiB, 790 ms at 1 MiB, 3.1 s at 4 MiB, 13.4 s at 16 MiB.

**Word-wrap limit (64 KiB):**
Above this threshold, soft wrap uses grid wrapping instead of word wrapping.
Word wrap of one row at width 113: 0.22 ms at 4 KiB, 0.90 ms at 16 KiB, 3.55 ms at 64 KiB, 15.4 ms at 256 KiB, 61.6 ms at 1 MiB.

**Long-row threshold (1 MiB):**
Rows above this are never decoded as a whole.
Whole-row decode render: 5.7 ms at 256 KiB, 21.4 ms at 1 MiB, 74 ms at 4 MiB.
Windowed render (up to 8192 characters): 2.4 ms at 256 KiB, 6.2 ms at 1 MiB, 21.5 ms at 4 MiB.

**Small-file limit (1 MiB):**
`open()` reads files up to this size into memory and scans them on the calling thread; larger files are read on demand and scanned in the background.
ACT3 measured the first screen latency of the lazy path against the stock eager path, which no longer exists: 18 vs 34 ms at 64 KiB, 45 vs 36 ms at 256 KiB, 139 vs 37 ms at 1 MiB, 680 vs 44 ms at 4 MiB.

**Key latency:**
GIL contention between the scan and the UI thread was the bottleneck.
The fixes are the ASCII/non-ASCII run splitting in `text_width._cells()` and the `Foreground` gate.
The down-key median is about 15 to 16 ms, which matches the 60 Hz render cadence; home and end take about 2 ms.
On the 5 GB file during indexing the maximum is 38 ms.
On a 200 MB line all steps stay under 50 ms.

---

## Known Limits

### Long Rows Before the Scan Has Finished

A provisional window assumes tab phase 0 at its left edge, so tabs may shift when the cursor resolves.
Word moves on an unresolved row are a fixed 8 characters.
`select_line` and `select_all` do nothing while the row length is unknown.
Mouse clicks on a long row beyond the scanned frontier are ignored, and so are clicks on a provisional row without wrap.

### Known Limit: Edits Refused at Unresolved Positions

An edit, undo, redo, cut or copy that needs a position that is not exactly resolved is refused and changes nothing.
Unresolved means that the cursor of a long row is provisional or pending, that a selection end lies beyond the scanned part of a long row, or that the row is not scanned yet.
The user hears the terminal bell and sees a warning notification.
Its text is `Edit refused: <reason>. It works once indexing reaches the position.`
The reason names the cause, for example "the cursor position in this long line is not indexed yet" or "column N of long row R is not resolved yet".
The widget also posts `EditRefused(reason)`.
The wait lasts until the scan covers the position: after the line index for a row that is not scanned yet, and after the long-row scan for a column inside a long row.
After that the same key works.
An edit at a position that the scan has already passed is not refused for this reason.
REQ-9 and REQ-3 are measured at resolved positions: after indexing on the 5 GB file and after the long-row scan on the 200 MB line.

### Known Limit: Tab Stops in Edited Rows Over 1 MiB

After an edit inside a long row (above `long_row_threshold`, 1 MiB), the display columns of the text behind the edit keep the tab alignment they had before the edit.
When the edit changes the display width by an amount that is not a multiple of the tab width, and tabs follow the edit, those columns can be off by up to `tab_width - 1` cells until the file is reopened.
Short and medium rows are always exact.
An edit that rebuilds the index of the row (see "Long Rows After Edits") measures the row again and is exact once the scan has passed it.

### Known Limit: Approximate End Column After a UTF-8 Merge

When an edit joins bytes into new characters (for example an inserted continuation byte next to a lead byte), `LazyDocument._relocate` decodes the row up to the end of the edit to find the end location.
It does so only when that stretch is at most `RELOCATE_LIMIT` (256 KiB), or when the long index of the edited row gives a character boundary close to the edit.
In other cases the end column is approximate and can be off by the characters that merged with the neighbours.
The text itself is exact; only the cursor column after such an edit is affected.

### Known Limit: The Add Store Shrinks Only at a Save

Bytes that were typed or pasted stay in the add store until the next save, even after undo or delete.
The history and the internal clipboard may still reference them.
A save rebases the add store: typed text that is in the file becomes part of the file, and only orphans are copied into the fresh store (see "Undo Across Saves").
Between saves memory grows only with typed and pasted text.

### Known Limit: `text` Above 8 MiB

`NovaTextArea.text` returns `""` for a document above `TEXT_LIMIT` (8 MiB), and `LazyDocument.read_all(limit)` raises `WholeLineAccess` there.
Reading `text` costs O(size), so the property never decodes a large document.
Code that needs a part of a large document uses `get_text_range` or the capability methods.
The streaming save does not use `text` and has no size limit (see "Streaming Save").

### Known Limit: Hard Links, Owner, Group, ACLs and Xattrs

A save creates a new inode and replaces the target, so a second hard link keeps the old content.
The owner, the group, ACLs and extended attributes of the target are not copied to the new file.
Only the permission bits are restored, with `fchmod`.

### Known Limit: The Retained Generation Keeps Disk Space

When a save has more than `UNDO_COPY_LIMIT` (8 MiB) of orphan bytes, the old file stays open as a legacy generation.
Its disk space stays allocated, although the file is unlinked, until the document closes or the history is cleared.
A save that needs more than 255 generations clears the history instead.
Typical sessions never reach either case.

### Known Limit: Edits Are Locked During a Save

Typing, deleting, pasting, undo and redo are refused for the length of a save, with the reason `saving`.
The notification text is the generic one of an edit refusal.
Cursor, selection, scrolling and goto keep working, and the lock is lifted on every outcome.

### Known Limit: `kill -9` Can Leave a Temp File

A killed process cannot run the `atexit` hook, so a `.name.xxxx.tmp` file can stay next to the target.
The original is intact, because the replace has not happened.
A normal exit, a failure and a cancel remove the temp file.

### Known Limit: A Save After an In-Place Change

When the file was changed in place (`MODIFIED` or `TRUNCATED`) and the user confirms the overwrite, the unchanged parts of the document are read from the file as it is now.
Bytes that were rewritten in place therefore appear in the saved file.
A truncation that removed needed bytes fails the save.

### Known Limit: `stat` on a Hung Mount

`save` checks the path on the UI thread before it starts the job, with one or two `stat` calls.
On a hung network mount this blocks the UI until the mount answers.
The periodic check of `nova_edit` runs on a worker thread and does not block.

### Known Limit: Non-Regular Files

`nova_edit` refuses to open a FIFO, a device or a directory, because opening a FIFO blocks.
A save refuses such a target at stage `prepare`.

### Known Limit: 255 Generations

At most 255 legacy generations exist per document.
A save that needs another one clears the undo history and the clipboard record, and shows "Undo history cleared: too many saves with large deletions."
This is the only case where a save drops undo.

### Known Limit: File Systems Without `fchmod`

`fchmod` that fails with `ENOTSUP` or `EOPNOTSUPP` is ignored, and the file keeps the mode `mkstemp` gave it (`0o600`).
Any other `fchmod` error fails the save at stage `flush`.
`posix_fallocate` is best effort and is skipped on file systems that do not support it.

### Known Limit: Search Case Folding

Case folding is simple and one to one, so `ß` does not match `ss`, and `ẞ` matches only `ß` and itself.
`İ` matches only itself, and `ı` matches only itself.
There is no Unicode normalisation, so the NFC and NFD forms of a letter are different text.
The tables follow the Unicode version of the running Python.

### Known Limit: Edits Cancel a Search

Any edit cancels a running search: typing, delete, paste, undo and redo.
The edit is accepted at once, and `SearchCancelled` has the reason `text changed`.
A save does not cancel a search, because the bytes and their offsets stay the same.
`reload` and `load_text` cancel it with the reason `reloaded`, and `close` cancels it without a message.
A result that arrives after an edit is dropped and never moves the cursor.

### Known Limit: The Search Is Literal

The needle is literal text.
The byte pattern that the pattern tier compiles is an implementation detail built from escaped bytes.
There are no regular expressions, no whole-word option and no replace.

### Known Limit: Needle Size

A plain needle is limited to 1 MiB of bytes (`MAX_NEEDLE_BYTES`).
A needle that needs the pattern tier (case-insensitive, or with a line break) is limited to 4,096 characters (`MAX_PATTERN_CHARS`).
A longer needle raises `SearchError` before any thread starts, and the widget posts `SearchFailed`.

### Known Limit: Search in an Unresolved Long Row

A match in an unresolved part of a long row is selected only when the long index has resolved both ends.
Until then the selection does not change, `JumpProgress` reports the long-index frontier, and Escape cancels.
A goto shows a provisional cursor instead, but a selection with estimated columns would highlight the wrong characters.

### Known Limit: Case-Insensitive Search of Non-ASCII Files

The case-insensitive search of a file with non-ASCII bytes in every window runs at the pattern-tier throughput.
That is 419 MB/s on the 5 GB reference file, 12.8 s for a miss, when the needle starts with a character that has case variants.
A needle with a literal prefix and a case-sensitive search are faster (see "Search Measurements").

### Known Limit: Line Breaks in a Needle

A line break in a needle matches exactly one document terminator (`\r\n`, `\n` or `\r`).
A needle `\n` never selects half of a CRLF, and a needle `\r\n` also matches a lone LF or a lone CR.
The search bar of `nova_edit` takes one line, so a line break reaches the search only through the API.

---

## Testing

Tests are located under `tests/nova_editor/`.

**Core layer:**
- `core/test_byte_source.py`, `core/test_line_index.py`, `core/test_line_index_budget.py`, `core/test_long_line_index.py` — sources and indexes, including property tests against reference implementations.
- `core/test_foreground.py`, `core/test_row_at_offset.py`, `core/test_text_width.py` — the foreground gate, `row_at_offset` and the width helpers.
- `core/test_piece_tree.py`, `core/test_add_store.py`, `core/test_piece_table.py`, `core/test_original_source.py`, `core/test_row_source.py`, `core/test_scan_now.py` — the piece table and its parts, fuzzed against plain `bytes` and a regular-expression split (`core/reference.py` holds the reference and the seeded `Rng`).
- `core/test_long_line_splice.py` — `LongLineIndex.spliced`, `resume` and `byte_to_char` against a fresh scan.
- `core/test_truncation.py` — truncation in a subprocess.
- `core/test_row_scanner.py` — `RowScanner` and `LineIndex.from_scan` against a fresh scan.
- `core/test_save_roundtrip.py`, `core/test_save_failure.py`, `core/test_save_cancel.py`, `core/test_save_targets.py` — `SaveJob` output, failure injection through `SaveIo`, cancel and special targets.
- `core/test_save_layout.py`, `core/test_rebase.py`, `core/test_piece_table_layout.py`, `core/test_long_line_rebased.py` — the layout, the translation, `layout_range` and `LongLineIndex.rebased`.
- `core/test_core_boundary.py` — the Textual import boundary.
- `core/test_casefold.py` — the fold, the variant classes and the static ASCII table against the full scan.
- `core/test_search_matcher.py`, `core/test_search_job.py`, `core/test_search_properties.py` — the matcher, the job (cancel, gate, stale, retry, progress) and hypothesis properties.
- `core/search_reference.py`, `core/test_search_reference.py`, `core/fake_planner.py` — the brute-force reference model, its own tests, and the fake planner with a revision.

**Document layer (`document/`):**
- `test_lazy_document.py`, `test_lazy_wrapped_document.py`, `test_lazy_window.py` — lazy documents, wrapping and windows.
- `test_lazy_edit.py`, `test_lazy_long_edit.py`, `test_lazy_bounded_edit.py`, `test_lazy_from_text.py` — document edits against a `str` reference (`reference_text.py`), long-row edits with lowered thresholds, and `text=` sources.
- `test_edit_history.py`, `test_history_modified.py` — byte-based undo and redo records, typing coalescing, and the modified state.
- `test_edit_lock.py`, `test_lock_audit.py` — the edit lock and the table accesses under the document lock.
- `test_search_plan.py` — `search_plan` and `revision`.
- `test_save_rebase.py`, `test_save_rebase_fuzz.py`, `test_rebase_holders.py` — the rebase and its seeded fuzz, and the holders that are re-pointed at the swap.
- `test_cursor_anchor.py`, `test_long_row_anchor.py` — the cursor machine and its index adapter.
- `test_capabilities.py`, `test_navigator_capabilities.py`, `test_navigator_long_rows.py` — capability methods and navigation.

**Widget and app:**
- `test_widget.py`, `test_widget_capabilities.py` — widget behaviour on small documents.
- `test_stock_characterization.py`, `test_lazy_characterization.py` — golden traces (`golden/`), recorded before the convergence, that the converged widget and the unedited lazy path must still match.
- `test_convergence.py` — `text=`, `load_text` and small files on the lazy document, and the removal of the stock widget path.
- `test_lazy_widget.py`, `test_lazy_cursor.py`, `test_jump.py`, `test_highlight_limit.py` — lazy rendering, cursor, goto and highlighting.
- `test_lazy_edit_widget.py`, `test_lazy_clipboard.py`, `test_lazy_edit_long_row_memory.py` — editing, refusal, undo, redo, clipboard and memory on long rows through the widget.
- `test_app.py`, `test_app_lazy.py`, `test_app_save.py`, `test_bindings.py` — the app, opening files, the save bars and key bindings.
- `test_widget_save.py`, `test_widget_save_rebase.py`, `test_widget_external_change.py`, `test_save_long_row_cursor.py` — the widget API of saving, the rebase of the widget, external changes and the cursor on a long row across a save.
- `test_widget_search.py`, `test_widget_search_events.py`, `test_widget_search_long_row.py` — the widget search: selection, wrap, repeat, cancel, edits, reload, save, truncation, close, and long rows with lowered thresholds.
- `test_app_search.py` — the keys, the bar, the status texts and the case toggle of `nova_edit`.
- `test_independence.py` — no dependency on `nova_navigator`.

**Search reference model:**
`core/search_reference.py` is written independently of the production matcher.
It decodes the bytes to `str` with `surrogateescape`, compares character by character with its own fold, and treats a line break as one unit.
It implements direction, origin, wrap and the owned-range rules by brute force.
The properties compare the job with it over random piece layouts, random needles (also substrings of the document), both case modes, both directions, wrap on and off, and chunk sizes from 1 to 17 bytes and large.
Further properties check that the tiers return the same result on ASCII data and that the reversed pattern agrees with the forward scan.

**Helpers and benchmarks:**
- `helpers_view.py`, `test_helpers_view.py` — synthetic file builder and oracle.
- `tests/tools/test_measure_view.py` and `tests/tools/test_measure_core.py` — smoke tests of the harnesses.

**Measurement harness (`uv run python -m tools.measure_view`):**
Subcommands: `first-screen`, `memory`, `latency`, `jump`, `oracle`, `calllog`, `sweep-yield`, `thresholds`, `summarise`, and for editing `edit-memory`, `verify`, `edit-latency`, `edit-scatter`, `segments`, `pieces`, `undo-record` and `clipboard`.
The search subcommands are `search-5g`, `search-latency`, `search-cancel`, `search-edited`, `search-longline`, `search-sweep`, `search-gen` and `search-fold`.
The save subcommands are `save-5g`, `save-latency`, `save-sweep`, `save-longline`, `save-retention`, `save-records`, `save-cancel` and `save-fulldisk`.
See `src/tools/measure_view.py` for usage.

Run all tests:
```sh
uv run pytest tests/nova_editor/ tests/tools/test_measure_core.py tests/tools/test_measure_view.py
```

---

## Vendoring Policy

Rather than subclassing Textual's `TextArea`, we vendor (copy) the implementation and its dependencies.

| Reason | Benefit |
|--------|---------|
| **Heavy modifications** | Large-file support is deep and pervasive; vendoring avoids subclass fragility. |
| **Minimize Textual coupling** | Textual's internals may change; vendoring gives us control over API stability. |
| **Performance optimization** | We can optimize for Nova Navigator's specific use cases. |
| **Version independence** | We pin the vendored code, not Textual. |

See `src/nova_editor/UPSTREAM.md` for the file inventory, the list of edits and the upgrade instructions.

---

## Public API

**Main exports** (via `nova_editor.__init__.py`):
- `NovaTextArea` — the editor widget.
- `LazyConfig` — thresholds of the lazily opened document.
- `DEFAULT_HIGHLIGHT_LIMIT` — default highlight limit of `open()`.

**Widget subpackage exports** (via `nova_editor.widget.__init__.py`):
- `NovaTextArea`, `DEFAULT_HIGHLIGHT_LIMIT`, `TextAreaLanguage`, `ThemeDoesNotExist`, `LanguageDoesNotExist`.

**Search API** (see "Search"):
- `NovaTextArea.search`, `cancel_search`, `searching` and `search_settings`.
- The messages `SearchProgress`, `SearchFound`, `SearchNotFound`, `SearchCancelled` and `SearchFailed`.
- `LazyDocument.search_plan` and `LazyDocument.revision`.
- `nova_editor.core`: `SearchSpec`, `SearchJob`, `SearchSettings`, `SearchPlan`, `SearchPlanner`, `SearchResult`, `SearchProgress`, `SearchError`, `SearchCancelled` and `SearchStale`.

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

```sh
uv run nova_edit                    # Open editor with no file
uv run nova_edit /path/to/file.txt  # Open a specific file
```

---

## Updating from Upstream

When Textual releases a new version and we want to update our vendored code, follow the procedure in `src/nova_editor/UPSTREAM.md`.

---

## Future Work

- **Selection:** Multi-line and multi-region selection on lazy documents.
- **Integration:** Embed in `nova_navigator` as an editor dialog for large file viewing and light editing.
- **Syntax highlighting on long rows:** Currently disabled for files above 1 MiB; tree-sitter queries could be windowed.
