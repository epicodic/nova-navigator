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
`nova_edit` saves only through an interim Ctrl+S that streaming save (ACT5) will replace (see "Interim Save").

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
`SourceChanged` exception signals file mutation (size/mtime changed, short read, or OS error).

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

`long_line_index` — Lazy checkpoint index for one very long row.
`LongLineIndex` records character column, display column and byte offset checkpoints at most 65,536 characters apart by default.
`Frontier` shows how far the background scan has reached: chars, disp (display columns), byte_rel (bytes relative to row start), and complete flag.
Queries beyond the scanned frontier return `None` (non-blocking); blocking is opt-in via `wait_until_known`.
`LongLineIndex.spliced(old, new_source, edit)` builds the index of an edited row without rescanning it (see "Long Rows After Edits").

`foreground` — The `Foreground` gate that lets the scans give way to the UI thread (see "Thread Model").
Both indexes accept it through a `foreground=` argument.

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
- `close()` — cancel the scans and close the source (also called on unmount).

**Message classes (posted by the widget):**
- `IndexProgress(count, complete)` — posted at most 10 times per second while the line scan grows, and once at completion.
- `IndexingComplete` — posted once when the line scan finishes.
- `SourceChanged(reason)` — posted once if the file behind a lazy document changed or could not be read; the view stays blank.
- `JumpProgress(fraction)` — posted when a deferred jump starts or advances, at most 10 times per second.
- `JumpCompleted(row, column)` — posted when a goto has moved the cursor; `column` is an estimate while `PROVISIONAL`.
- `JumpRejected(reason)` — posted when a goto target is out of range.
- `EditRefused(reason)` — posted when an edit, undo, redo or copy is refused because a position is not resolved yet (see "Known Limit: Edits Refused at Unresolved Positions").

**Thresholds (defaults, tunable via `LazyConfig`):**
- `word_wrap_limit = 65536` — rows above this use grid wrap instead of word wrap.
- `long_row_threshold = 1048576` — rows above this are never decoded as a whole.
- `highlight_limit = 1048576` (argument of `open()`) — files larger than this are not syntax highlighted.
- `sync_scan_limit = 1048576` — sources up to this size are scanned on the constructing thread.

### 4. **Application Layer** (`nova_editor/app.py`)

Standalone Textual app (`NovaEditApp`) providing:
- Every file is opened through `NovaTextArea.open`; there is no eager path and no `--lazy` flag.
- `Ctrl+S` runs the interim save (see "Interim Save").
- `Ctrl+Z`, `Ctrl+Y`, `Ctrl+X`, `Ctrl+C` and `Ctrl+V` are the stock bindings for undo, redo, cut, copy and paste.
- `Ctrl+Q` quits.
- `F4` toggles soft wrap.
- `Ctrl+G` shows or hides the goto bar for line or byte offset navigation (`@N` syntax for byte offsets).
- `Escape` closes the goto bar while it has the focus, and cancels a pending jump in the editor.
- Footer showing the file path and keyboard shortcuts.
- Entry point `main()` for the `nova_edit` CLI command.
- Timing hook via the `NOVA_EDIT_TIMING_FILE` environment variable (writes `FIRST_CONTENT <ns>` when content first renders).

**GotoBar:** Inline input field that accepts `N` (line number) or `@N` (byte offset); Enter navigates and Escape closes it.

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

## Interim Save

Ctrl+S in `nova_edit` is an interim save that ACT5 replaces with a streaming save.
For a document of up to `TEXT_LIMIT` (8 MiB) it writes `text.encode("utf-8", "surrogateescape")` to a temporary file next to the target, copies the permissions, and moves it over the target with `os.replace`.
The temporary file is created next to the resolved target with a unique name (`tempfile.mkstemp`), so it cannot replace another file, and it is flushed and synced before `os.replace`.
A symlink is followed: the target is replaced and the link stays.
A temporary file is removed when the save fails.
When the file existed at start but could not be read, the editor shows an empty document and Ctrl+S refuses with "Not saved" so that the file is not replaced.
A file that did not exist at start is created with the mode its umask allows, and later saves replace it like any other file.
If such a file appears from elsewhere before the first save, Ctrl+S refuses with "Not saved".
It then shows the notification "Interim save (replaced by streaming save in ACT5)".
For a larger document it writes nothing and shows "Saving large files arrives with ACT5".
No data is lost silently and no partial file replaces the original.
Ctrl+Q quits without asking about unsaved changes.

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

### Known Limit: The Add Store Never Shrinks

Bytes that were typed or pasted stay in the add store for the whole session, even after undo or delete.
The history and the internal clipboard may still reference them.
ACT5 rebases the add store after a save.
Until then memory grows only with typed and pasted text.

### Known Limit: `text` Above 8 MiB

`NovaTextArea.text` returns `""` for a document above `TEXT_LIMIT` (8 MiB), and `LazyDocument.read_all(limit)` raises `WholeLineAccess` there.
Reading `text` costs O(size), so the property never decodes a large document.
Code that needs a part of a large document uses `get_text_range` or the capability methods.
The interim save refuses documents above this size (see "Interim Save").

---

## Testing

Tests are located under `tests/nova_editor/`.

**Core layer:**
- `core/test_byte_source.py`, `core/test_line_index.py`, `core/test_line_index_budget.py`, `core/test_long_line_index.py` — sources and indexes, including property tests against reference implementations.
- `core/test_foreground.py`, `core/test_row_at_offset.py`, `core/test_text_width.py` — the foreground gate, `row_at_offset` and the width helpers.
- `core/test_piece_tree.py`, `core/test_add_store.py`, `core/test_piece_table.py`, `core/test_original_source.py`, `core/test_row_source.py`, `core/test_scan_now.py` — the piece table and its parts, fuzzed against plain `bytes` and a regular-expression split (`core/reference.py` holds the reference and the seeded `Rng`).
- `core/test_long_line_splice.py` — `LongLineIndex.spliced`, `resume` and `byte_to_char` against a fresh scan.
- `core/test_truncation.py` — truncation in a subprocess.
- `core/test_core_boundary.py` — the Textual import boundary.

**Document layer (`document/`):**
- `test_lazy_document.py`, `test_lazy_wrapped_document.py`, `test_lazy_window.py` — lazy documents, wrapping and windows.
- `test_lazy_edit.py`, `test_lazy_long_edit.py`, `test_lazy_bounded_edit.py`, `test_lazy_from_text.py` — document edits against a `str` reference (`reference_text.py`), long-row edits with lowered thresholds, and `text=` sources.
- `test_edit_history.py` — byte-based undo and redo records and typing coalescing.
- `test_cursor_anchor.py`, `test_long_row_anchor.py` — the cursor machine and its index adapter.
- `test_capabilities.py`, `test_navigator_capabilities.py`, `test_navigator_long_rows.py` — capability methods and navigation.

**Widget and app:**
- `test_widget.py`, `test_widget_capabilities.py` — widget behaviour on small documents.
- `test_stock_characterization.py`, `test_lazy_characterization.py` — golden traces (`golden/`), recorded before the convergence, that the converged widget and the unedited lazy path must still match.
- `test_convergence.py` — `text=`, `load_text` and small files on the lazy document, and the removal of the stock widget path.
- `test_lazy_widget.py`, `test_lazy_cursor.py`, `test_jump.py`, `test_highlight_limit.py` — lazy rendering, cursor, goto and highlighting.
- `test_lazy_edit_widget.py`, `test_lazy_clipboard.py`, `test_lazy_edit_long_row_memory.py` — editing, refusal, undo, redo, clipboard and memory on long rows through the widget.
- `test_app.py`, `test_app_lazy.py`, `test_bindings.py` — the app, opening files, saving and key bindings.
- `test_independence.py` — no dependency on `nova_navigator`.

**Helpers and benchmarks:**
- `helpers_view.py`, `test_helpers_view.py` — synthetic file builder and oracle.
- `tests/tools/test_measure_view.py` and `tests/tools/test_measure_core.py` — smoke tests of the harnesses.

**Measurement harness (`uv run python -m tools.measure_view`):**
Subcommands: `first-screen`, `memory`, `latency`, `jump`, `oracle`, `calllog`, `sweep-yield`, `thresholds`, `summarise`, and for editing `edit-memory`, `verify`, `edit-latency`, `edit-scatter`, `segments`, `pieces`, `undo-record` and `clipboard`.
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

- **Streaming save (ACT5):** Replace the interim Ctrl+S, rebase the add store after a save, and remove the 8 MiB limit of saving.
- **Search:** Add find and replace that works on lazy documents.
- **Selection:** Multi-line and multi-region selection on lazy documents.
- **Integration:** Embed in `nova_navigator` as an editor dialog for large file viewing and light editing.
- **Syntax highlighting on long rows:** Currently disabled for files above 1 MiB; tree-sitter queries could be windowed.
