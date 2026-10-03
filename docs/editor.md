# Text Editor Package (`nova_editor`)

This document describes the current architecture and behaviour of the `nova_editor` package, a standalone Textual-based text editor with a vendored TextArea widget.

---

## Overview

**Purpose:** Provide a Textual-based text editor widget and standalone application that handles large files and very long lines with minimal overhead.

The package is separate from `nova_navigator` and can be used independently.

**What it is:**
The widget is a vendored copy of Textual 8.2.8's `TextArea` (renamed `NovaTextArea`) with a lazy, editable document, long-row handling and windowed rendering.
The core layer scans large files in background threads and holds the edited document as a piece table over the original bytes.
The lazy document layer (`LazyDocument`, `LazyWrappedDocument`) uses the core to decode rows on demand.
Its capability methods never wait for a scan and return `None` or empty results beyond the scanned frontier.
Every document uses this one path: `text=`, `load_text` and files of every size all become a `LazyDocument` (see "Converged Document Model").
The widget is editable; undo, redo, cut, copy and paste work on piece references.
Saving streams the document to a temporary file on a worker thread and replaces the target atomically (see "Streaming Save").
After a save the document stands on the saved file, and undo still works (see "Rebase After a Save").
A change of the file on disk is detected and never overwritten silently (see "External Changes").
A literal search runs over the whole document on a worker thread (see "Search").
The standalone app `nova_edit` wraps the widget with a status line, a footer, bars and a quit confirmation (see "nova_edit").

**Where to look:**

| Topic | Section |
|---|---|
| Structure and imports | "Layering" |
| Classes, properties, messages | "Public API" |
| The standalone app | "nova_edit" |
| Numbers | "Performance (measured)" |
| What does not work, and what was not measured | "Known Limits" |
| Vendored code | "Vendoring Policy" and "Updating from Upstream" |

---

## Layering

The package is organized in four layers, with one-way dependencies: core, document, widget, app.

### Core Layer (`nova_editor/core/`)

Textual-free byte indexing, scanning and piece-table infrastructure.
The original file is immutable; edits live in the piece table above it.
No module in this package may import Textual; a boundary test enforces it.
A plain `import nova_editor.core` loads no Textual module, because the top-level exports are lazy (see "Public API").

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

**I/O strategy:**

`PreadSource` uses `os.pread` behind a small LRU block cache: 64 KiB blocks, 128 blocks (8 MiB total).
One shared cache per source instance; scan reads bypass it via `cache=False`.
A memory map was rejected: it is faster for warm window reads, but it dies with SIGBUS when the file is truncated and `pread` does not.

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
BOM is content; line endings are never normalised.

**Sparse line index:**

Every 64th row start is stored in an array (default stride), so the index costs a small fraction of a full row array.
A background thread appends and publishes `(count, complete)` snapshots under one lock.
Count is a lower bound until complete; UI-path calls never wait.
Callbacks run on the scan thread, outside locks.
Each row also checks the long-row rule.
Files with CR bytes scan more slowly per byte than files with LF only, because they take the regular-expression path instead of the byte-search fast path.

**UI-path byte budget:**

Every `row_range` and `lines` call reads at most 4 MiB (hard rule).
Returns `None` or a shorter list when the budget is exceeded.
Rows longer than 16 KiB are recorded during scan in a side table (cap 524,288 entries) so walks jump over them.
`lines` returns at most 128 rows per call.
The walk reads windows of min(64 KiB, `long_line_threshold` + 2) bytes, which is 16,386 bytes with the defaults.
A recorded long row costs no walk read, but its terminator is found with a two-byte read of the row tail.

**Long-line index:**

Checkpoints (character column, display column, byte offset) are stored relative to the row start.
Their spacing is at most the `checkpoint_chars` argument, 65,536 characters by default; a lazy document passes 8192 (see "Document Layer").
Each scan block of 1 MiB adds a shorter tail piece when its length is not a multiple of the spacing.
The width helper uses the non-caching `rich.cells.cell_len`, because a caching width function would retain every checkpoint piece.
UTF-8 resync on block boundaries; surrogateescape for invalid bytes; tabs, wide chars and combining marks via `rich.cells`.
Non-blocking `try_*` queries return `None` beyond the scanned frontier (no waiting).
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

### Document Layer (`nova_editor/document/`)

The layer holds the editable lazy documents built over the core piece table.
It also holds the documents and the edit history vendored from Textual 8.2.8.
The stock `Document` stays only as the base of `SyntaxAwareDocument`, the mirror that feeds syntax highlighting; the widget never uses it as its own document.
The package exports `EditsLocked`, `LazyDocument` and `RowUnavailable`.

**Lazy classes:**
- `LazyDocument` — editable document over a `PieceTable`; decodes rows on demand with capability methods that never wait.
  Its edit methods are `replace_range` (text), `splice` (piece references between locations) and `splice_bytes` (piece references between byte offsets, used by undo and redo).
  `selection_content` returns the bytes of a selection as piece references without reading them.
  `search_plan(offset, limit)` returns a `SearchPlan` (revision, length and parts) in one lock hold, and `revision` counts the committed edits (see "Search").
  `newline` is the terminator of the first row (the one that new line breaks use).
- `LazyWrappedDocument` — wrapping layer over `LazyDocument`; manages grid wrap sections and a sparse vertical size estimate.
- `LazyConfig` — tunable thresholds and cache sizes with defaults; `sync_scan_limit` (1 MiB) is the largest source scanned on the constructing thread.

**Edit classes (vendored, changed):**
- `Edit` — one edit; after `do` it holds `start_byte`, `removed` and `inserted` (piece references) and `end_location`.
- `EditHistory` — undo and redo stacks of batches of edits (see "Undo and Redo").

**Wrap helpers (vendored):**
`document/_wrap.py` holds `compute_wrap_offsets` and `cell_width_to_column_index`, copied from Textual (see "Vendoring Policy").

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
See "Cursor State Machine (Long Rows)" for the states and transitions.

### Widget Layer (`nova_editor/widget/`)

Textual widget and lazy rendering support.
The package exports `NovaTextArea`, `DEFAULT_HIGHLIGHT_LIMIT`, `ExternalCheck`, `TextAreaLanguage`, `ThemeDoesNotExist` and `LanguageDoesNotExist`.

**Core module:**
- `NovaTextArea` — the main editor widget (vendored from Textual 8.2.8, renamed from `TextArea`).
  It always uses a `LazyDocument`: rows are decoded on demand, medium and long rows are drawn through windows, and the cursor on a long row goes through the state machine.
  Bytes that are not valid UTF-8 are drawn as U+FFFD with the width of one cell; the document keeps the escaped character, so selection and delete act on the byte.

**Helper modules:**
- `_text_area_theme.py` — theme support for syntax highlighting (vendored).
- `_tree_sitter.py` — the optional tree-sitter loader (vendored).
- `_lazy_window.py` — windowed rendering for medium and long rows; decodes at most 8192 characters per window.
- `_long_row_cursor.py` — cursor machine integration and provisional window layout for long rows.
- `_search_run.py` — `SearchRun`, the state of one running search, and `run_search_thread`, the body of the `nova-search` thread.

The widget's members and messages are listed in "Public API".

### Application Layer (`nova_editor/app.py`, `status_line.py`, `search_bar.py`)

`app.py` holds the standalone Textual app `NovaEditApp`, the bars (`GotoBar`, `PathBar`, `SaveBar`, `ConfirmBar`), `TimedNovaTextArea` and the entry point `main()`.
`status_line.py` holds `StatusLine`, `StatusState` and the pure function `format_status`.
`search_bar.py` holds `SearchBar` and `SearchStatus`.
All three modules import the widget layer and Textual, and nothing from `nova_navigator`.
The app is described in "nova_edit".

---

## Public API

### Package exports

`nova_editor/__init__.py` exports three names, loaded lazily on first access:
- `NovaTextArea` — the editor widget.
- `LazyConfig` — thresholds of the lazily opened document.
- `DEFAULT_HIGHLIGHT_LIMIT` — default highlight limit of `open()`.

The module defines `__getattr__` (PEP 562), `__dir__` and `__all__`, and carries the imports under `TYPE_CHECKING` so type checkers still resolve the names.
The first access imports the real module and caches the value in the module globals.
An unknown name raises `AttributeError`.
`dir(nova_editor)` lists the three names.
The tests are in `tests/nova_editor/test_lazy_exports.py` and `tests/nova_editor/core/test_core_boundary.py`.

`nova_editor.core` also exports the search types `SearchSpec`, `SearchJob`, `SearchSettings`, `SearchPlan`, `SearchPlanner`, `SearchResult`, `SearchProgress`, `SearchError`, `SearchCancelled` and `SearchStale`.

### Usage example

```python
from nova_editor import NovaTextArea
from textual.app import App, ComposeResult

class MyEditorApp(App):
    def compose(self) -> ComposeResult:
        yield NovaTextArea(text="Hello, world!")
```

### Constructing and opening

- `open(source, language, soft_wrap, config, highlight_limit, **kwargs)` — open a file; returns the widget ready to mount.
  A file of up to `SMALL_FILE_LIMIT` (1 MiB) is read into memory and scanned on the calling thread, so its counts are exact at once and no file stays open.
  A larger file is read on demand and scanned in the background.
- `NovaTextArea(text=...)`, `load_text(text)` and the `text` setter build a `LazyDocument` over the UTF-8 bytes of the text, scanned on the calling thread whatever the size.
- `close()` — cancel the scans and close the source (also called on unmount); a running save is cancelled and its terminal message is posted at once.

### Document and text

- `document` — the `LazyDocument`.
- `text` (property) — the decoded document up to `TEXT_LIMIT` (8 MiB); `""` above it (see "Known Limits").
- `read_only` — as in stock; a lazy document is no longer read-only.
- `file_path` — the file the document is bound to; set by `open(path)` and by every successful save.
- `find_matching_bracket` — returns `None` for a document above `BRACKET_SEARCH_LIMIT` (1 MiB), because a search would decode arbitrarily many rows.

### Indexing and line count

- `line_count` (read-only) — a lower bound on the row count; exact when indexing is complete.
- `line_count_exact` and `indexing_complete` (read-only) — true when the line count is final.
- `indexing_progress` (read-only) — the fraction of the document that the line scan has covered, from 0.0 to 1.0; exactly 1.0 when the scan is complete or the document is empty.
- `line_ending` (read-only) — `"LF"`, `"CRLF"` or `"CR"`: the terminator of the first row, which new line breaks use; `"LF"` for an empty document.

### Cursor and navigation

- `cursor_state` (read-only) — the `CursorState` of the cursor; always `RESOLVED` off long rows.
- `peek_cursor_state()` — the cursor state and the byte offset of its anchor within the row, without creating or advancing anything (the status line uses it).
- `cursor_byte_offset` (read-only) — the absolute byte offset of the cursor; `None` while it is unknown (the line scan has not resolved the cursor row yet).
- `column_exact` (read-only) — true when the cursor column is exact (`RESOLVED`).
- `cursor_location` — the row and column of the cursor; an estimate while `PROVISIONAL`.
- `pending_progress` — fraction in [0, 1) of a deferred jump; `None` when nothing is pending.
- `goto_line(line)` — go to a 1-based line; returns `None`, and a line the scan has not reached stays pending.
- `goto_byte(offset)` — go to an absolute byte offset; returns `None`, and an offset beyond the scanned frontier stays pending.
- `cancel_pending()` — cancel a pending jump or deferred cursor operation; the action is bound to Escape while one is pending or a search runs.
- `toggle_wrap()` — flip `soft_wrap`; `nova_edit` binds it to F4.
- `select_all` is bound to `ctrl+shift+a` and `f8`; the stock binding was `f7` only, and F7 belongs to the search of `nova_edit`.

### Saving, reloading, external changes

- `modified` (read-only) — whether the text differs from the last saved state (see "Modified State").
- `saving` (read-only) — whether a save runs.
- `save(path=None, *, overwrite=False) -> bool` — start a save (see "Streaming Save" and "Save As and Confirmations").
- `cancel_save()` — ask a running save to stop; nothing happens when none runs.
- `reload() -> bool` — discard the edits and show the file as it is now (see "Reload").
- `check_external_change() -> ChangeKind` — synchronous check of the file on disk (see "External Changes").
- `begin_external_check()`, `ExternalCheck` and `apply_external_check()` — the three steps of the check that a poll runs off the UI thread.
- `refresh_after_rebase()` — re-point the cursor holders after a rebase; the widget calls it itself.
- `clipboard_cap` (class attribute) — largest selection in bytes that is also copied to the system clipboard (see "Clipboard").

### Search

- `search(needle, *, backward=False, case_sensitive=True, wrap=True) -> bool` — start a literal search (see "Search").
- `cancel_search()` — ask a running search to stop; nothing happens when none runs.
- `searching` (read-only) — whether a search runs.
- `search_settings` (class attribute) — `SearchSettings` (chunk, progress interval, tier switch); tests lower it.
- `LazyDocument.search_plan` and `LazyDocument.revision` support the search.

### Messages (posted by the widget)

- `IndexProgress(count, complete)` — posted at most 10 times per second while the line scan grows, and once at completion.
- `IndexingComplete` — posted once when the line scan finishes.
- `SourceChanged(reason, kind)` — posted once if the file behind a lazy document changed or could not be read; `kind` is a `ChangeKind` (see "External Changes").
- `JumpProgress(fraction)` — posted when a deferred jump starts or advances, at most 10 times per second.
- `JumpCompleted(row, column)` — posted when a goto has moved the cursor; `column` is an estimate while `PROVISIONAL`.
- `JumpRejected(reason)` — posted when a goto target is out of range.
- `EditRefused(reason)` — posted when an edit, undo, redo or copy is refused because a position is not resolved yet (see "Known Limits") or because edits are locked (see "Edit Lock").
- `SaveProgress(phase, done, total)` — posted at most 10 times per second while a save runs.
- `Saved(path, length)`, `SaveFailed(error, stage, path)` and `SaveCancelled` — the terminal messages of a save; exactly one is posted per save.
- `SaveNeedsConfirmation(kind, path)` — posted instead of starting a save that needs consent.
- `Reloaded` and `ReloadFailed(error, path)` — the outcome of `reload`.
- `SearchProgress(done, total, phase)` — posted at most 10 times per second while a search runs.
- `SearchFound(start, end, row, column, wrapped)`, `SearchNotFound(needle)`, `SearchCancelled(reason)` and `SearchFailed(error)` — the terminal messages of a search; exactly one is posted per search.

### Thresholds (defaults, tunable via `LazyConfig`)

- `word_wrap_limit = 65536` — rows above this use grid wrap instead of word wrap.
- `long_row_threshold = 1048576` — rows above this are never decoded as a whole.
- `highlight_limit = 1048576` (argument of `open()`) — files larger than this are not syntax highlighted.
- `sync_scan_limit = 1048576` — sources up to this size are scanned on the constructing thread.
- `max_long_indexes = 8` — cached long-row indexes.

### The app class

`NovaEditApp(file_path=None, *, soft_wrap=False, config=None, editor_class=TimedNovaTextArea)`:
- `file_path` is the only positional parameter; `None` opens an empty buffer.
- `soft_wrap` starts with soft wrapping.
- `config` is a `LazyConfig` (`None` means the defaults).
- `editor_class` is the editor widget class; a probe subclass can be passed in.
- `NovaEditApp.search(needle, *, backward=False, case_sensitive=None)` starts a search and remembers the needle for F3.

---

## Running the Editor

```sh
uv run nova_edit                    # Open editor with no file
uv run nova_edit /path/to/file.txt  # Open a specific file
```

A path that exists but is not a regular file (a FIFO, a device, a directory) is refused: `main()` prints `nova_edit: <path>: not a regular file` to stderr and exits with status 1.
A path that does not exist opens an empty buffer that the first save creates (see "EditorScreen").
Every file is opened through `NovaTextArea.open`; there is no eager path.
The environment variable `NOVA_EDIT_TIMING_FILE` makes `TimedNovaTextArea` write `FIRST_CONTENT <ns>` when content first renders (used by the benchmark harness).

---

## EditorScreen

### Overview

`EditorScreen` is a Textual `Screen` that provides a complete editing UI (REQ-1, REQ-2, ADR-3).
It can be used as a standalone app (`nova_edit`) or embedded in other Textual applications.

The screen holds:
- **Per-screen action registry** (18 editor actions, built fresh per screen instance)
- **Per-screen MenuBar** (File, Edit, Search, View menus)
- **Per-screen KeymapRegistry and HintBar** (for action key binding and hint display)
- **DocumentView** (holds the NovaTextArea widget and file metadata)
- **Bars** (GotoBar, PathBar, SaveBar, SearchBar, ConfirmBar)
- **StatusLine** (shows editor progress and file status)

The document itself is not stored in the screen; instead, EditorScreen holds a reference to a `DocumentView`.

### Constructor

```python
EditorScreen(
    path: Path | None = None,
    *,
    keybindings: KeybindingsConfig | None = None,
    file_provider: FileProvider | None = None,
    soft_wrap: bool = False,
    config: LazyConfig | None = None,
    editor_class: type[TimedNovaTextArea] = TimedNovaTextArea,
    standalone: bool = False,
) -> None:
```

**Parameters:**
- `path` — File to open, or `None` for an empty buffer.
- `keybindings` — User keybinding overrides from `~/.config/nova-navigator/keybindings.toml`; `None` for defaults.
- `file_provider` — FileProvider for the file dialog (used by Open action); defaults to `InMemoryFileProvider`.
- `soft_wrap` — Start with soft wrapping enabled (default `False`).
- `config` — Tunable thresholds of the lazy document (`LazyConfig`); `None` for defaults.
- `editor_class` — The editor widget class (default `TimedNovaTextArea`, allows injection for testing).
- `standalone` — `True` for standalone app, `False` for embedded (affects menu structure).

### Architecture

The screen builds these components on construction:

1. **ACTIONS** — Fresh list of 18 `Action` objects (built by `build_editor_actions()`).
2. **MenuBar** — Menu bar with File, Edit, Search, View menus (built by `build_menu_bar()`).
3. **HintBar** — Key hint display (one per screen).
4. **KeymapRegistry** — Manages keybindings and dispatches actions (one per screen).
5. **DocumentView** — Holder of the editor widget and file metadata.

Each screen instance has its own action registry, menu bar, and keybinding registry.
This allows multiple EditorScreen instances in different Textual screens to have independent key bindings and menu states (REQ-3).

### The 18 Editor Actions

**File operations:**
| Action | ID | Key | Description |
|--------|-----|-----|---|
| Open | `editor.open` | Ctrl+O | Open a file |
| Save | `editor.save` | Ctrl+S | Save the document |
| Save As | `editor.save_as` | Ctrl+Shift+S | Save under another name |
| Reload | `editor.reload` | F5 | Reload from disk |
| Close | `editor.close` | Ctrl+W | Close the editor |
| Quit | `editor.quit` | Ctrl+Q | Quit (in standalone mode) |

**Edit operations:**
| Action | ID | Key | Description |
|--------|-----|-----|---|
| Undo | `editor.undo` | Ctrl+Z | Undo the last edit |
| Redo | `editor.redo` | Ctrl+Y | Redo the last undone edit |
| Cut | `editor.cut` | Ctrl+X | Cut the selection |
| Copy | `editor.copy` | Ctrl+C | Copy the selection |
| Paste | `editor.paste` | Ctrl+V | Paste from the clipboard |
| Select All | `editor.select_all` | Ctrl+A | Select the whole document |

**Search operations:**
| Action | ID | Key | Description |
|--------|-----|-----|---|
| Find | `editor.find` | Ctrl+F | Search the document |
| Find Next | `editor.find_next` | F3 | Repeat the search forward |
| Find Previous | `editor.find_previous` | Shift+F3 | Repeat the search backward |
| Go to | `editor.goto` | Ctrl+G | Go to a line or byte offset |

**View options:**
| Action | ID | Key | Description |
|--------|-----|-----|---|
| Line Numbers | `editor.line_numbers` | F11 | Show/hide line numbers (checkable) |
| Wrap Mode | `editor.wrap_mode` | F10 | Toggle soft wrap (checkable) |

Actions with `show=True` appear in the hint bar; checkable actions maintain state with the widget.

### Key Bindings and Overrides

**Default keys** are defined in `build_editor_actions()`.

**User overrides** are loaded from `~/.config/nova-navigator/keybindings.toml`:

```toml
[bindings]
editor.save = "ctrl+s"
editor.find = "ctrl+f"
editor.wrap_mode = ""   # Empty string unmaps the action
```

When `EditorScreen` is constructed with a `KeybindingsConfig`, it applies overrides to each action before building the menus (REQ-4).

**Amendment B2 defect (workaround):** `KeymapRegistry.reload()` shows the default key for unmapped actions instead of showing nothing.
This is a known defect in `nova_widgets` (not this activity).
EditorScreen works around this locally by checking effective bindings.
The defect is scheduled for fix in ACT8 or ACT9.

### Menu Bar

The menu bar has four menus:

**File Menu:**
- Open… (Ctrl+O)
- Save (Ctrl+S)
- Save As… (Ctrl+Shift+S)
- Reload (F5)
- ─── (separator)
- Close (Ctrl+W)
- Quit (Ctrl+Q) — only in standalone mode

**Edit Menu:**
- Undo (Ctrl+Z)
- Redo (Ctrl+Y)
- ─── (separator)
- Cut (Ctrl+X)
- Copy (Ctrl+C)
- Paste (Ctrl+V)
- ─── (separator)
- Select All (Ctrl+A)

**Search Menu:**
- Find… (Ctrl+F)
- Find Next (F3)
- Find Previous (Shift+F3)
- ─── (separator)
- Go to… (Ctrl+G)

**View Menu:**
- Line Numbers (F11, checkable)
- Wrap Mode (F10, checkable)

Checkable menu items are kept in sync with the widget's reactive properties (`soft_wrap`, `show_line_numbers`).

### Bars

**GotoBar** — Go to a line or byte offset.
- Input: `N` for line, `@N` for byte offset.
- Show: Ctrl+G.
- Close: Escape.
- Action: Enter navigates to the target.

**PathBar** — Save the file under a new path.
- Placeholder: `Save as: path`.
- Show: When saving without a file path (Ctrl+S) or via Save As (Ctrl+Shift+S).
- Close: Escape.
- Action: Enter saves.

**SearchBar** — Enter the search term.
- Placeholder: `Search (case-sensitive)` or `Search (ignore case)`.
- Show: Ctrl+F.
- Close: Escape.
- Action: Enter starts the search.

**SaveBar** — Progress indicator for a running save.
- Shows phases: `Saving`, `Flushing`, `Finishing`, `Preserving undo history`.
- Progress: `X.X / Y.Y GiB  P%`.
- Cancel: Escape (while saving).
- Messages: `Saved`, `Save cancelled`, `Save failed`, `Reloaded`.

**ConfirmBar** — Confirmation of actions.
- Used for: overwrite after external change, reload of modified document, quit questions.
- Keys: `O` overwrite, `A` save as, `R` reload, `Q` quit, `Esc` stay.
- Only the keys that the question lists are active.

**SearchStatus** — Result indicator.
- Shows: `Searching N% (X of Y), Esc cancels`, `Found`, `Not found`, `Search cancelled`.
- Display: 3 seconds (then clears).

### StatusLine

`StatusLine` displays the editor state on one row.
The app builds a `StatusState` from the editor, and the pure function `format_status(state, width)` renders it.

Parts are added in order while text still fits the width; the tail is cut first on a narrow terminal:

| Order | Text | Meaning |
|---|---|---|
| 0 | `Goto P%  Esc cancels` | While a goto waits for the scan (highest priority) |
| 1 | `Ln N  Col N` | Line and column (1-based, shows `Ln N  Col ~N` when provisional, `Ln N  Col ...` when pending) |
| 2 | `Byte N` or `Byte ?` | Byte offset (? while line scan has not resolved the row) |
| 3 | `N lines` or `>= N lines (indexing P%)` | Exact or lower bound during indexing |
| 4 | `LF`, `CRLF` or `CR` | Line ending |
| 5 | `Modified` | Only when document is modified |
| 6 | `New file` | Only before first save of a missing path |
| 7 | `Wrap` or `No wrap` | Soft wrap state |

The status line reads from the editor on its own timer (0.05 s) to keep updates off the critical path.

### On Mount

When the screen mounts (`on_mount()`), it:
1. Sets up checkable actions (connects them to editor state).
2. Watches editor reactive properties (`soft_wrap`, `show_line_numbers`).
3. Updates action checked state when properties change.

### Document View

See the "DocumentView" section below.

---

## DocumentView

### Purpose

`DocumentView` is a holder of per-document state: the `NovaTextArea` widget, the file path, and the load outcome.

It is not part of `EditorScreen` directly; instead, `EditorScreen.document` holds a reference to one.
This allows the same document to be viewed in multiple screens without duplicating the widget.

### Class

```python
class DocumentView:
    editor: NovaTextArea         # The text editor widget
    file_path: Path | None       # The file path (None for new/unsaved)
    load_state: str              # "new", "opened", or "failed"
    error_message: str | None    # If failed, the reason
```

### Construction

```python
DocumentView.open(
    path: Path | None,
    *,
    editor_class: type[TimedNovaTextArea] = TimedNovaTextArea,
    soft_wrap: bool = False,
    config: LazyConfig | None = None,
    timing_file: str | None = None,
) -> tuple[DocumentView, str | None]
```

Returns a tuple of `(document_view, error_message)`.
- If the file opens successfully, `error_message` is `None`.
- If the file cannot be read, `error_message` is a user-friendly error string (e.g., "File not found: /path/to/file").
- If `path` is `None`, a new empty document is created (`load_state == "new"`).

### State Machine

| State | Meaning | `load_state` |
|-------|---------|---|
| New | Empty buffer, no file | `"new"` |
| Opened | File read successfully | `"opened"` |
| Failed | File could not be read | `"failed"` |

After a successful save, the document remains in the `"opened"` state.
On `reload()`, the document attempts to re-read the file; if it fails, `load_state` becomes `"failed"` again.

### Usage

`EditorScreen` gets the editor widget via `self.document.editor`:

```python
editor = self.document.editor
editor.save()
editor.reload()
editor.search(needle="foo")
```

The document reference is stable across the lifetime of the screen.

---

## nova_edit

### Layout

From top to bottom:
- `Header` — the title `nova_edit`, and the path as sub-title (updated after a save).
- The editor (`#editor`), which takes the remaining height.
- The bars, all hidden until needed: `GotoBar`, `PathBar`, `SaveBar`, `ConfirmBar`, `SearchBar` and `SearchStatus`.
- `StatusLine`, always visible, one row.
- Textual's `Footer(compact=True)`, one row, without the command palette.

The bars are plain widgets, not dialogs, so they have no entry in `src/tools/dialog_tester.py`.

### Status line

`StatusLine` shows the state of the editor.
The app builds a `StatusState` from the widget, and the pure function `format_status(state, width)` turns it into text.
Parts are added in the order below while the text still fits the width; the tail is cut first on a narrow terminal, and the first part is always kept.

| Order | Text | Meaning |
|---|---|---|
| 0 | `Goto P%  Esc cancels` | Only while a goto waits for the scan; comes first |
| 1 | `Ln N  Col N` | Line and column (1-based) |
| 2 | `Byte N` or `Byte ?` | Byte offset of the cursor; `?` while the line scan has not resolved the row |
| 3 | `N lines` or `>= N lines (indexing P%)` | Exact count, or a lower bound while the scan runs; `1 line` is singular |
| 4 | `LF`, `CRLF` or `CR` | `editor.line_ending` |
| 5 | `Modified` | Only when `editor.modified` |
| 6 | `New file` | Only before the first save of a path that did not exist |
| 7 | `Wrap` or `No wrap` | Soft wrap state |

The column has three states, from the cursor state read with `peek_cursor_state()`:
- `Col N` when the cursor is `RESOLVED`.
- `Col ~N` when it is `PROVISIONAL` (the column is an estimate).
- `Col ...` when it is `PENDING`.

The indexing percentage is `indexing_progress` as a whole percent, at most 99 while the count is not exact.

**Refresh:**
The app listens to `SelectionChanged`, `Changed`, `IndexProgress`, `IndexingComplete`, `JumpCompleted`, `Saved`, `Reloaded` and to the `pending_progress` and `soft_wrap` changes, and calls `StatusLine.request()`.
`request()` sets a dirty flag and starts a timer of `REFRESH_SECONDS` (0.05 s) when none is pending.
The timer reads the widget state once and updates the text.
At most 20 updates happen per second, the last state is always shown, and no work happens on the key's critical path.
`StatusLine.flushes` counts the updates.

A save and a search keep their own transient lines (`SaveBar`, `SearchStatus`); the status line does not repeat them.

### Footer and keys

The footer is Textual's `Footer`, generated from the app bindings.
It shows `^s Save`, `F2 SaveAs`, `F5 Reload`, `^g Goto`, `F7 Find`, `F3 Next`, `S-F3 Prev`, `F4 Wrap` and `^q Quit`.
Escape is bound but hidden, and is active only while a save runs.

| Key | Action |
|---|---|
| `Ctrl+S` | Save; without a file it opens the path bar; an unmodified document shows "No changes to save" |
| `F2` | Path bar for save as, prefilled with the current path; Enter saves and Escape closes |
| `F5` | Reload; a modified document shows the confirm bar first; during a save it shows a warning |
| `Ctrl+G` | Show or hide the goto bar: `N` for a line, `@N` for a byte offset |
| `F7` | Search bar; Enter searches forward, Escape closes it |
| `F3` | Repeat the search forward, also with the bar closed; without a needle it opens the bar |
| `Shift+F3` | Repeat the search backward |
| `Alt+C` | In the search bar: toggle between case-sensitive and ignore case |
| `F4` | Toggle soft wrap |
| `Ctrl+Q` | Quit (see "Quit flow") |
| `Escape` | Close the goto, path or search bar while it has the focus; cancel a running save, a pending jump and a running search |
| `Ctrl+Z`, `Ctrl+Y`, `Ctrl+X`, `Ctrl+C`, `Ctrl+V` | Stock undo, redo, cut, copy and paste |

### Bars

**GotoBar:**
An input that accepts `N` (line number) or `@N` (byte offset).
Enter navigates and Escape closes it.
Text that does not parse shows the warning `Invalid goto format`.

**PathBar:**
An input like the goto bar, with the placeholder `Save as: path`.
An empty path shows the warning `No path given`.

**SaveBar:**
A line that is hidden when idle.
It shows `Saving  1.2 / 5.0 GiB  24 %  Esc cancels`, then `Flushing`, `Finishing` or `Preserving undo history`, and a result line for 4 seconds (`Saved  <name>  <size>`, `Save cancelled`, `No changes to save`, `Reloaded`).
A failure shows `Save failed (<stage>): <OS message>` and stays until a key is pressed.
A committed failure adds that the file was written and that F5 reloads it.

**ConfirmBar:**
A key driven question that takes the focus.
The keys are `O` overwrite, `A` save as, `R` reload, `Q` quit and `Esc` keep or stay.
Only the keys that the question lists are active.
The questions are overwrite after an external change, reload of a modified document, and the two quit questions.

**SearchBar and SearchStatus:**
See "Search".

### Quit flow

`Ctrl+Q` works as follows:
- First, a running search is cancelled, and a pending jump is cancelled.
- Then a running save is cancelled, and the app waits without blocking the UI for its end, polling every 0.02 s for at most `QUIT_WAIT_SECONDS` (2 s).
- If the save is still running after the wait, the confirm bar asks the second question: `A save is still running.  Q quit anyway  Esc stay`.
- Otherwise, if the document is modified, the confirm bar asks the first question: `Discard the unsaved changes and quit?  Q quit  Esc stay`.
- Otherwise the app exits at once.

`Esc` returns to the editor with every edit intact, and the next `Ctrl+Q` asks again.
A quit question replaces any other open question of the bar.
A save that finished with `Saved` before the wait ended leaves the document unmodified, so the app exits; `SaveCancelled` leaves it modified, so the first question follows.
An untouched empty buffer for a missing path, and a file that failed to load, exit at once.
The tests are in `tests/nova_editor/test_app_quit.py`.

### A path that does not exist

`nova_edit <missing>` opens an empty buffer, and the status line shows `New file`.
The first `Ctrl+S` creates the file, even when it is empty, and the status line drops `New file`.
If the file appears on disk before that save, the save asks for confirmation (kind `CREATED`).
A missing parent directory makes the save fail visibly and creates nothing.
Typing then quitting asks the first quit question, and the file is not created.
A file that exists but cannot be read shows an error notification, refuses `Ctrl+S` ("Not saved: the file could not be loaded ...") and opens the path bar for a save as.
The tests are in `tests/nova_editor/test_app_req16.py`.

### Goto messages

`Ctrl+G` opens the bar; Enter goes to the target and hides the bar.
The widget posts `JumpRejected`, and the app shows its reason as a warning notification while the cursor stays where it was:
- `line N is not a line number (lines start at 1)` for a line below 1.
- `line N is beyond the last line (COUNT)` for a line above the final count.
- `byte offset N is negative`.
- `byte offset N is beyond the end of the file (L bytes)`.
- `byte offset N cannot be resolved`.

A target beyond the scanned part stays pending: the status line shows `Goto P%  Esc cancels`, and Escape cancels it.

### Search in nova_edit

`F7` opens the bar, Enter searches forward and `Escape` closes the bar.
`F3` and `Shift+F3` repeat the last needle forward and backward, also with the bar closed.
`Alt+C` toggles the case in the bar, and the placeholder shows `Search (case-sensitive)` or `Search (ignore case)`.
`SearchStatus` shows `Searching 42% (2.1 of 5.0 GiB), Esc cancels` while it runs.
Then it shows `Found`, `Wrapped to the top`, `Wrapped to the bottom`, `Not found: <needle>`, `Search cancelled` (with `: <reason>` unless the user cancelled) or `Search failed: <error>` for 3 seconds.
A needle is shown at most 40 characters in `Not found`.

### Other app behaviour

- The app polls the file every `POLL_SECONDS` (2 s) on a worker thread, and runs the same check when the terminal regains the focus (see "External Changes").
- `Ctrl+S` on a file whose last load failed does not overwrite it.
- `TimedNovaTextArea` is the editor subclass that writes the timing hook.

---

## Converged Document Model

There is one document path.
`NovaTextArea(text=...)`, `load_text` and every file opened with `NovaTextArea.open()` build a `LazyDocument`.
The stock `Document` is not the document of the widget, there is no eager branch, and the editor is not read-only.

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
Rows end at LF, CRLF or lone CR only, so the document differs from the stock widget, which split on `str.splitlines` separators.
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
The total per piece, with the inner nodes and the `Piece` object, is about 99 bytes (see "Performance (measured)" and "Known Limits"); the design target of below 80 bytes was missed.
Every node carries the piece count and an aggregate `(length, breaks, first_is_lf, last_is_cr)`.
The aggregates combine associatively, so offsets and breaks are found in O(log n).
`splice(start, end, content)` replaces a byte range, returns the removed `Content`, merges adjacent pieces that are contiguous in one source, and rebalances.
Typing therefore extends one piece instead of adding one piece per key.

**Bulk delete:**
`PieceTree.splice` removes the pieces of a range in one pass (`_delete_pieces`, `_delete_range`).
It slices the column arrays inside a boundary leaf, drops whole leaves and inner nodes between the boundaries, and refreshes and rebalances each cut node once.
The earlier form removed pieces one at a time, which made a delete cost linear in the number of pieces it spans.
The correctness is protected by `check_invariants(deep=True)` and by the tests `test_bulk_delete_matches_a_plain_bytes_reference`, `test_bulk_delete_shapes` and `test_a_wide_delete_does_not_delete_piece_by_piece` in `tests/nova_editor/core/test_piece_tree.py`.
The speed of the bulk delete is not measured (see "Unverified claims").

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
While the document is exactly the original (one original piece), every query goes straight to the `LineIndex`, so an unedited document costs what the index costs.

**Open tail:**
While the original scan is incomplete, the last original piece `[t, L)` stays outside the tree, because its break count is not final.
Its rows come from the original index on demand.
A position inside the scanned part cuts the tail at that position, which moves `[t, x)` into the tree with exact counts.
A position beyond the scanned part is not accepted for an edit.
When the scan completes, the next call folds the tail into the tree.
Row counts are lower bounds until then.

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
Bytes the user did not edit are never touched, so mixed line endings stay as they are.
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
A paste of outside text is held once in the add store, which is the only permitted growth.

**System clipboard:**
The system clipboard is written through `app.copy_to_clipboard` only when the selection is at most `NovaTextArea.clipboard_cap` bytes.
`clipboard_cap` is 2 MiB (2,097,152 bytes).
The system text is the decoded selection with U+DC80 to U+DCFF replaced by U+FFFD, so the system copy is lossy for invalid bytes and the internal copy is exact.
Above the cap, `notify` shows a warning: the system clipboard was not updated, and pasting inside the editor still works.

**How the cap was chosen:**
The rule is the largest measured size whose copy plus terminal write stays clearly under 20 ms at p95 on the development machine.
The p95 of 2 MiB was at most 14.1 ms over three runs; 3 MiB peaked at 19.8 ms and 4 MiB at 32.1 ms, so they are not used.

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
- `preallocate` and `build_index` are on by default; `build_index=False` exists for the measurement baseline and makes the save end as `SaveFailed` with stage `internal` after the file was written, because the rebase needs an index.
- Measured numbers: see "Performance (measured)".

**Plan:**
`LazyDocument.plan(offset, limit, unverified)` takes the document lock, asks `PieceTable.layout_range` for the runs `(src, a, b)` and returns `PlanPart(src, source, a, b)` items.
No byte is read under the lock, so the plan cannot raise `SourceChanged`.
Parts of the original are read with `cache=False`.

**Progress:**
The job reports `writing` (at most 20 times per second), `flushing` and `finishing`; the save thread then reports `history` while it translates the records.
The widget keeps the latest report, posts at most one callback to the UI thread at a time, and turns it into `SaveProgress` at most 10 times per second.
The job accepts a `Foreground` gate (`PauseGate`) and sleeps `pause_seconds()` after every chunk.
The widget passes no gate (see "Known Limits").

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

### Undo Across Saves

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
- Up to `UNDO_COPY_LIMIT` (8 MiB, in `core/rebase.py`) of orphan bytes in total are read from the old sources and copied into the new add store, and the old files are released.
- Above it, the old original with its line index and the old add store become a legacy generation `g` of the new table.
  Orphan pieces keep their ranges and get `src = (g << 24) | k`, where `k = 0` is the generation's original and `k >= 1` an add segment.
  `Piece.src` is a `uint32` column: 8 bits of generation and 24 bits of segment.
- At most 255 generations exist (`MAX_GENERATION`).
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

`RowScanner(stride, threshold)` is the row-boundary scan that `LineIndex` and the save thread share.
`feed(block, base, final)` scans a block, `take()` returns the stride entries and long rows found since the last call, and `finish(length)` returns the last row when it is long.
A CR at a block edge is carried, so a CRLF split between two blocks is one terminator.
`LineIndex._scan` calls it.

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
- Every read of uncached content of a large original checks the descriptor, and the `SourceChanged` carries a kind.
  Cached blocks are served until the next miss.
- `save` compares the resolved path with the held identity before it starts.
  The held identity is that of the descriptor for a `PreadSource`, and the `stat` taken around the read for a small file held in memory.
- `NovaTextArea.check_external_change()` is public and synchronous: the descriptor check plus `check_path`.
  While a save runs it reports `UNCHANGED`, because the replace of the save would look like a change.
- The poll of `nova_edit` splits the check so that no widget state is touched off the UI thread.
  `begin_external_check()` (UI thread) captures the document, the path, the held identity and the save epoch; `ExternalCheck.run()` does only the `stat` calls and runs on a worker thread every 2 seconds; `apply_external_check()` (UI thread) applies the result.
  The poll is skipped while a save runs or while the previous check still runs.
  A result is dropped when a save began or ended since the check began (the save epoch counts both), so the replace of the app's own save is never reported as `REPLACED`.
- When the terminal gets the focus back (the Textual `AppFocus` event), `nova_edit` runs the same check at once.

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
The UI thread waits for row 0 of the new document (`LazyDocument.wait_first_row`), bounded by `_FIRST_ROW_WAIT` (1 second), because the cursor, the scrollbar and the layout watchers cannot cope with an unresolved first row; the restore of the old cursor row is a pending jump.
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

## Search

`NovaTextArea.search` finds literal text in the whole document, forward or backward, in the original bytes and in the edited ones.
It runs on a worker thread with progress and cancellation, and the UI keeps answering while it runs.
The layers are `core/casefold.py` and `core/search.py` (Textual-free), `LazyDocument.search_plan`, `widget/_search_run.py` with the glue in `_text_area.py`, and `search_bar.py` in the app.

### The Key Move

`F7` opens the search, as in Midnight Commander.
The stock binding of `select_all` was `f7` only; it is now `ctrl+shift+a,f8`, and `ctrl+shift+a` is a new key.
`Ctrl+F` stays the stock cursor-right binding.

### Needle Model

The needle is a `str`, encoded as UTF-8 with `surrogateescape`.
An escaped byte (U+DC80 to U+DCFF) in the needle matches that raw byte, so invalid bytes are searchable.
A match is a byte range `[start, end)` that starts and ends on a character boundary of the decoding.
A raw byte such as 0xA9 therefore does not match inside a valid `é`.
A line break in the needle (`\n`, `\r\n` or `\r`) counts as one character and matches exactly one document terminator.
`compile_matcher` raises `SearchError` for an empty needle, a lone surrogate outside U+DC80 to U+DCFF, and a needle over the limits (see "Known Limits").

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
A needle that starts with a character that has case variants is much slower (see "Performance (measured)").

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

---

## Memory While Editing

Memory is bounded by the structures, not by the document size.

**Pieces:**
The piece table costs about 99 bytes per piece (see "Performance (measured)").
Typing extends one piece, so a piece is added by an edit that splits or separates text, not by a keystroke.

**Undo records:**
A record holds piece references, so its size does not depend on the size of the edit.
Coalesced typing costs a few bytes per keystroke.

**Add store:**
Typed and pasted bytes stay in the add store until the next save, even after undo or delete (see "Known Limits").

**Save and search:**
A save adds a stream index and the translated history while it runs.
A search adds almost nothing, because it reads outside the block cache.

---

## Wrap Semantics

Wrapping applies only when `soft_wrap` is on; it is off by default in `open()`, in `nova_edit` and in the constructor.
With soft wrap off, no row wraps and wide rows scroll horizontally.
With soft wrap on, the row class decides how a row wraps.

**Short rows (0 to 64 KiB):**
Stock word wrap (`compute_wrap_offsets`, vendored in `document/_wrap.py`); fast because the row is decoded once.

**Medium rows (64 KiB to 1 MiB):**
Grid wrap: each section holds a fixed number of display columns.
The row is decoded whole and cached for measuring its width, and it is drawn through a window of at most 8192 characters.

**Long rows (above 1 MiB):**
Grid wrap too, but the row is never decoded whole.
Its width comes from the long-row index, and sections are known only up to the scanned frontier.

**Wrap measurement budget:**
One call that measures wrapped heights (`height`, `y_of_row`, `row_of_y`) decodes at most `MEASURE_MAX_BYTES` (4 MiB) of row bytes.
The same call measures at most `MEASURE_MAX_ROWS` (128) medium or long rows exactly.
Rows beyond the budget get a provisional height, and the estimate timer of the widget finishes them on later ticks.
Blocks of `BLOCK_ROWS` (64) rows are measured and kept in `_blocks`; they are never evicted (see "Known Limits").

**Estimates after an edit:**
The y of a block that is not measured is estimated from the mean extra height of the measured blocks.
An edit drops the measured blocks from the edit on, and the mean is pinned to its value before the edit while a block above the edit is still unmeasured.
Without the pin, measuring the blocks again would change the mean and move the estimated y of every unmeasured block above the edit, and the view would jump.
The pin is released when a block above the edit is measured, and it is not set when something was never measured or when every block above the edit is measured.
Until it is released, the estimate of the unmeasured blocks below the edit uses the mean from before the edit.

**Stale height in `scroll_cursor_visible`:**
The estimated height of an unmeasured region is `rows x running mean`, and the mean changes every time a block is measured.
`_refresh_size()`, which sets `virtual_size`, runs at open and on the estimate timer, but not when the cursor moves.
A first move far into a huge wrapped document (`ctrl+end` at the end of a 5 GB file) computed a cursor y from the new mean while Textual still clamped the scroll offset to the stale virtual height.
The first render then asked for y values that map to blocks that are not measured, measuring moved the mean again, and the layout chased the mean through many blocks.
`scroll_cursor_visible` now refreshes the size first: after `_recompute_cursor_offset()` and before `scroll_to_region`, when soft wrap is on and the cursor y is not above `virtual_size.height`, it reads `wrapped_document.height` and, when that is larger than `virtual_size.height`, calls `_refresh_size()`.
A cursor inside the current virtual height scrolls correctly without a refresh, so the height is not read at all in that case, and `_refresh_size()` never calls `scroll_cursor_visible` back.
The virtual height then covers the cursor y before Textual clamps the scroll offset.
The same call covers goto and search placement at the end, and `pagedown` onto the last page.
With wrap off nothing changes.
The tests are in `tests/nova_editor/test_wrapped_cursor_end.py` (`test_move_cursor_to_the_last_row_measures_few_blocks`, `test_goto_the_last_line_measures_few_blocks`, `test_search_placement_at_the_last_row_measures_few_blocks`, `test_wrap_off_scroll_cursor_visible_does_not_refresh_the_size` and the tests that count reads of the height).
The time of this step on the 5 GB file with the final code is not measured (see "Unverified claims").

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

**Poll thread:**
The external-change poll of `nova_edit` runs the `stat` calls on a worker thread and applies the result on the UI thread (see "External Changes").

---

## Performance (measured)

All numbers in this section were measured earlier in the project, on earlier code: before the status line, the footer, the quit flow and the wrap-on `ctrl+end` fix existed.
They describe the widget and the core on the reference files, not the final app.
Machine: Intel Core i5-14600K, 62 GiB RAM, Linux 7.0.0-28-generic, Python 3.12, Textual 8.2.8, rich 14.2.0.
Files: `normal-5g.txt` (5,368,709,120 bytes) and `longline-200m.txt` (209,715,200 bytes, one row), generated by the harness `uv run python -m tools.measure_view ...`.
Unless a line says otherwise the cache is warm and the test used the widget without the app.
The numbers that were not measured on the final code are listed in "Unverified claims".

**First screen (open):**
Warm median 148.8 ms for the 5 GB file and 211.1 ms for the 200 MB line (7 runs); cold median 163.3 ms and 274.9 ms (5 runs).
The first screen appears before indexing finishes.

**Memory while viewing:**
Maximum `RssAnon` of a scripted session (open, wait for indexing, 300 page downs, jump to the end, 50 page ups), median of 3 runs: 44.2 MiB (5 GB, wrap off), 51.6 MiB (5 GB, wrap on), 43.0 MiB (200 MB line, wrap off), 47.5 MiB (200 MB line, wrap on).

**Step latency while viewing (direct key injection, 450 steps per cell):**
- 5 GB file, during and after indexing, wrap off and on: median 15 to 16 ms (the 60 Hz render cadence), 2 ms for home and end after indexing; maximum 44.6 ms; 0 steps over 50 ms out of about 12,000.
- 200 MB line at column 100,000,000, wrap off: maximum 39.1 ms; 0 steps over 50 ms out of about 5,800.
- 200 MB line, wrap on: median 16 ms; 6 of about 6,500 steps were over 50 ms (52, 51, 64, 61, 52 and 58 ms); see "Unverified claims".
- A jump to column 100,000,000 before the long scan completes: median 2.8 ms (maximum 8.8 ms) with wrap off and 13.8 ms (maximum 31.2 ms) with wrap on.

**Thresholds:**

| Threshold | Measurement | Value chosen |
|---|---|---|
| Syntax highlighting limit | First screen with tree-sitter highlighting: 97.6 ms (64 KiB), 221 ms (256 KiB), 790 ms (1 MiB), 3.12 s (4 MiB), 13.4 s (16 MiB) | 1 MiB (`highlight_limit`) |
| Word-wrap limit | Word wrap of one row at width 113: 0.22 ms (4 KiB), 0.90 ms (16 KiB), 3.55 ms (64 KiB), 15.4 ms (256 KiB), 61.6 ms (1 MiB) | 64 KiB (`word_wrap_limit`) |
| Long-row threshold | Render with whole-row decode: 5.7 ms (256 KiB), 21.4 ms (1 MiB), 74 ms (4 MiB); windowed render: 2.4 ms, 6.2 ms, 21.5 ms | 1 MiB (`long_row_threshold`) |
| Small-file limit | First screen of the former eager path against the lazy path: 18 and 34 ms (64 KiB), 45 and 36 ms (256 KiB), 139 and 37 ms (1 MiB), 680 and 44 ms (4 MiB) | 1 MiB (`SMALL_FILE_LIMIT`) |

The eager path of the small-file row no longer exists.

**Editing memory (REQ-2):**
Maximum `RssAnon` over the session select, delete of 1,000,000,000 bytes, undo, redo, copy, paste, undo with window verification: 40.0 MiB (5 GB, wrap off), 40.7 MiB (5 GB, wrap on), 48.0 MiB (200 MB line, wrap off), 48.4 MiB (200 MB line, wrap on).
1,000 scattered edits in the 200 MB row peaked at 112.7 MiB (after Enter at column 100,000,000) and left 57 MiB; the 5 GB file stayed at 53.1 MiB.
The delete of 1,000,000,000 bytes took 7.8 to 14.6 ms with wrap off and 11.8 to 12.7 ms with wrap on, on the earlier per-piece delete over a document with few pieces; the bulk delete is not measured.

**Pieces and records (synthetic document, 10^6 pieces):**
- A piece costs 98.2 bytes by `tracemalloc` and 99.4 bytes by `RssAnon`.
- One splice takes 0.250 ms (insert) and 0.190 ms (delete) at the median, and 0.341 ms and 0.271 ms at p95.
  From 1,000 to 1,000,000 pieces the insert grew from 0.130 to 0.250 ms.
- One `row_range` takes 0.115 ms at the median.
- An undo record costs about 1.0 to 2.3 KB: 15.2 bytes per coalesced keystroke (1,515.8 bytes per record of 100 keystrokes), 977.1 bytes for a paste, 1,306.2 bytes for a delete and 2,256.0 bytes for a backspace run.

**Edit latency (direct injection, 450 steps per cell):**
- 5 GB file after indexing, at the end: median 3.9 to 6.8 ms, maximum 37.6 ms (typing, wrap on); 0 steps over 50 ms out of 8,100.
- 200 MB line after the long-row scan, column 100,000,000: median 5.8 to 14.4 ms, maximum 46.6 ms (Enter); 0 steps over 50 ms.
- While indexing, on rows the scan already covered: 1 step over 50 ms out of 7,200 (54.4 ms, Backspace, wrap on, 200 MB line, 10.2 ms of it a garbage collection).
- After 1,000 scattered edits: 0 steps over 50 ms; maximum 44.0 ms (5 GB file) and 46.4 ms (200 MB line).
- Undo took 2.6 to 16.1 ms and redo 5.2 to 11.6 ms.

**Clipboard cap:**
Copy plus terminal write, p95 over three runs: 2 MiB at most 14.1 ms, 3 MiB up to 19.8 ms, 4 MiB up to 32.1 ms.

**Save (5 GB file with 1,000 scattered edits, a 1,000,000,003-byte delete and a 100,000,114-byte paste, saved length 4,468,710,231 bytes):**
- Save time 7.4 to 7.5 s on the final measured code (7,402, 7,543 and 7,501 ms), 595 to 600 MiB/s, 66 to 67 progress messages.
- Session `RssAnon` 88.2 MiB maximum.
- `apply_rebase` 0.22 to 0.32 ms; windows verified before and after the rebase with no mismatch.
- Building the stream index costs about 70 percent of the write throughput (about 560 MiB/s against 1,750 to 2,000 MiB/s without it), and no step latency.
- Steps during the save: 1,352 steps (wrap on and off), 0 over 50 ms, maximum 30.2 ms; 8,659 idle steps, 0 over 50 ms.
- Cancel at 50 percent: about 3 ms from the request to the terminal message, target unchanged, no temp file left.
- A save of the 200 MB line over a copy took 128 ms, with `apply_rebase` 2.29 ms; the cursor stayed `RESOLVED`, and typing one character after the save took 6.99 ms.
- `apply_rebase` with 8 cached long indexes: 1.13 to 1.75 ms for 1 to 10,000 undo records.
  `prepare_rebase` on the save thread took 213 ms for 1,000 records and 1,619 ms for 10,000 records (first matrix).
- The defaults of 1 MiB chunk and `fsync` every 256 MiB were chosen from a sweep: 4 MiB chunks were slower and used more memory.

**Search (5 GB file, full-circle miss, warm cache):**

| Case | Result |
|---|---|
| Case-sensitive (find tier) | Median 1.37 s, about 3.9 GB/s, 12 to 14 progress messages |
| Ignore case, needle with a literal prefix | 1.6 s, 3.3 GB/s; backward 2.7 s, 2.0 GB/s |
| Ignore case, first character with variants (pattern tier) | 12.8 s, 419 MB/s |
| Generated ASCII 1 GiB (stand-in), ignore case (ascii tier) | Median 0.514 s, about 2.1 GB/s |
| Generated non-ASCII 1 GiB (stand-in), pattern tier | 430 to 435 MB/s; with a line break in the needle 400 MB/s |
| Match in the first block, to the terminal message | 1.4 to 2.0 ms |
| Match in the last block | 1.87 to 2.0 s (it scans the file) |
| Cancel at 50 percent, to the terminal message | 0.33 to 0.51 ms |
| Memory, 5 GB search | `RssAnon` maximum 40.1 MiB, increase over the baseline at most 0.027 MiB |
| 5 GB edited text (1,000 edits, 100 MB paste, needle across a piece boundary) | Found at the right offsets, 1.49 s and 0.79 s, `RssAnon` maximum 50.4 MiB |
| 200 MB line, marker at column 100,000,000 | Selected exactly, 1.87 and 2.05 ms from result to selection (wrap on and off) |
| Long index resolves byte 100,000,000 / 199,000,000 | 1.09 s / 2.19 s |

The real 5 GB file is not pure ASCII (41 of 41 sampled windows contain a byte above 0x7F), so case-insensitive search of it runs the pattern tier in every window; the ascii tier is measured on the generated stand-in only.
UI steps during a search on the 5 GB file stayed under 50 ms with wrap off: maximum 36.6 ms case-sensitive, 22.96 ms for the default needle ignoring case and 30.19 ms for the pattern tier.
On the 200 MB line the steps during the search peaked at 28.8 ms.
The chunk sweep (one run each) measured 2.9, 3.6 and 4.4 GB/s for the find tier and 404, 425 and 422 MB/s for the pattern tier at 64 KiB, 256 KiB and 1 MiB.
The longest UI step was 12.6 to 18.5 ms, and the constant stays 256 KiB.
The fold table has 1,424 classes and 2,878 entries, the largest class has 4 members, and the first non-ASCII use builds it in 89 to 98 ms and about 268 KiB.

**Wrap measurements:**
The wrap-off numbers above hold for wrap off.
With wrap on, the earlier code measured 360 to 480 ms for the first `ctrl+end` of a process on the 5 GB file (359 to 378 ms with no search at all, and 2 to 5 ms for the second); that is the step that the stale-height refresh targets (see "Wrap Semantics").

**Harness:**
`uv run python -m tools.measure_view` has the subcommands `first-screen`, `memory`, `latency`, `jump`, `oracle`, `calllog`, `sweep-yield`, `thresholds`, `summarise`, `edit-memory`, `verify`, `edit-latency`, `edit-scatter`, `segments`, `pieces`, `undo-record` and `clipboard`.
Four more subcommands exist: `first-end` (the first `ctrl+end`, `ctrl+home`, a second `ctrl+end` and two gotos in a fresh process), `app-latency` (the step latency in the real `nova_edit` app with its status line and footer), `wrap-blocks` (the time of `wrap_range` and `RssAnon` for a number of measured blocks) and `reload` (the time of `reload()`, warm and after dropping the page cache).
Some subcommands take options: `pieces --delete-pieces` (pieces removed by one splice) and `pieces --checkpoints` (checkpoints of a long-row index whose splice and adopt are timed), `save-fulldisk --real-error efbig` (a real `EFBIG` through `RLIMIT_FSIZE`), and `latency --instrument` and `latency --no-pilot` (per-step garbage collection and segment times, and no Pilot phase).
These scenarios exist and are covered by smoke tests, and no results from them are quoted in this page (they are not part of any number here).
The search subcommands are `search-5g`, `search-latency`, `search-cancel`, `search-edited`, `search-longline`, `search-sweep`, `search-gen` and `search-fold`.
The save subcommands are `save-5g`, `save-latency`, `save-sweep`, `save-longline`, `save-retention`, `save-records`, `save-cancel` and `save-fulldisk`.
See `src/tools/measure_view.py` for usage.

---

## Known Limits

| Limit | Cause | Bound | Where measured |
|---|---|---|---|
| Edits refused at unresolved positions | The scan has not reached the position | Until the scan covers it | Tests; REQ numbers measured at resolved positions |
| Tab stops in edited rows over 1 MiB | Splice shifts checkpoints by the width delta only | `abs(stale - exact) < tab width` | Test `test_tab_alignment_behind_an_edit_is_kept_from_before_the_edit` |
| Mixed line endings show the first style | No per-row counting in the scan | Display only; bytes are never changed | Unmeasured by design |
| `text` above 8 MiB is `""` | Reading costs O(size) | `TEXT_LIMIT` = 8 MiB | Not applicable |
| Memory per piece | `Piece` object and tree nodes | About 99 bytes against the 80-byte design target | Measured on earlier code (98.2 and 99.4 bytes) |
| `wrap_range` is O(measured blocks) | Measured blocks are never evicted | Grows with the number of blocks scrolled through | Unmeasured |
| `LongLineIndex` adopt and splice copy the checkpoints | Both copy the checkpoint arrays of the row | Grows with the number of checkpoints | Not measured at scale; only the 200 MB line was measured |
| `apply_rebase` lock time grows with cached long indexes | It rebases every cached long-row index under the document lock | Grows with the number of cached long-row indexes | 1.13 to 1.75 ms with 8 cached indexes, 2.29 ms on the 200 MB line, measured on earlier code |
| Widget save has no `Foreground` gate | The save thread does not give way to the UI | No step over 50 ms during a measured 5 GB save | Measured on earlier code |
| `reload()` waits on the UI thread | It waits for row 0 of the new document | At most 1 s (`_FIRST_ROW_WAIT`) | Unmeasured |
| ENOSPC proven by injection | No privileges for a real full disk | Fault injection plus a real EFBIG error | Tests |
| 255 generations | `Piece.src` has 8 generation bits | A save that needs a 256th clears the undo history | Test with `MAX_GENERATION` lowered |
| Add store shrinks only at a save | Undo and clipboard may reference added bytes | Grows with typed and pasted text | Not applicable |
| Hard links, owner, group, ACLs, xattrs | A save creates a new inode | Only permission bits are restored | Not applicable |
| Retained generation keeps disk space | The old file stays open for undo | Until close or history clear | Measured on earlier code |
| Edits locked during a save | Pieces name the file being replaced | For the length of the save | Not applicable |
| `kill -9` leaves a temp file | `atexit` cannot run | One `.name.xxxx.tmp` file | Not applicable |
| `stat` on a hung mount | `save` checks the path on the UI thread | Until the mount answers | Not applicable |
| Search needle size | Pattern tier cost | 1 MiB plain, 4,096 characters for the pattern tier | Not applicable |
| Search is literal | By design | No regular expressions, whole word or replace | Not applicable |
| Unverified claims | The final benchmarks were skipped | Five items | Unmeasured on the final code |

### Unverified claims

The final performance benchmarks were skipped by owner decision.
No measurement exists for the final code of the app.
The following five items are UNMEASURED on the final code and must not be read as met:

1. **The wrap-on first `ctrl+end` fix.**
   A design probe took the step from 398 ms to about 10 ms, and a hermetic test passes (see "Wrap Semantics").
   It was not measured on the 5 GB file with the final code.
2. **The `RssAnon` rise with wrap on.**
   An earlier wrap-on latency matrix rose by about 9.2 MiB, above the 8 MiB search budget of the design.
   The rise was neither attributed nor measured again.
   It was seen only in runs that contained the slow first `ctrl+end`.
   The 200 MB limit holds with a wide margin in every case that was measured.
3. **The isolated 50 to 60 ms steps.**
   Isolated steps of 50 to 64 ms appeared with wrap on in earlier latency matrices (a 53.6 ms `up` during a search, a 54.4 ms Backspace, and the six steps on the 200 MB line listed above).
   Garbage collection explained part of one of them.
   They were not measured again and not attributed.
4. **The app-level latency of `nova_edit`.**
   Every latency number above was measured on the widget without the status line and the footer.
   The step latency of the real app with both, in both wrap modes, is not measured.
   The design target that `cursor_byte_offset`, which the status line reads on its timer, stays under 2 ms is not measured either.
5. **The bulk-delete target.**
   The design target is 10,000 pieces deleted in under 20 ms.
   The bulk delete is implemented and tested for correctness and call count only.
   The bulk form exists because the earlier delete removed pieces one at a time, so its cost grew with the number of pieces in the range.

Every number in "Performance (measured)" not named here is measured, on earlier code.

### Edits Refused at Unresolved Positions

An edit, undo, redo, cut or copy that needs a position that is not exactly resolved is refused and changes nothing.
Unresolved means that the cursor of a long row is provisional or pending, that a selection end lies beyond the scanned part of a long row, or that the row is not scanned yet.
The user hears the terminal bell and sees a warning notification.
Its text is `Edit refused: <reason>. It works once indexing reaches the position.`
The reason names the cause, for example "the cursor position in this long line is not indexed yet" or "column N of long row R is not resolved yet".
The widget also posts `EditRefused(reason)`.
The wait lasts until the scan covers the position: after the line index for a row that is not scanned yet, and after the long-row scan for a column inside a long row.
After that the same key works.
An edit at a position that the scan has already passed is not refused for this reason.
The edit requirements were measured at resolved positions: after indexing on the 5 GB file and after the long-row scan on the 200 MB line.

### Long Rows Before the Scan Has Finished

A provisional window assumes tab phase 0 at its left edge, so tabs may shift when the cursor resolves.
Word moves on an unresolved row are a fixed 8 characters.
`select_line` and `select_all` do nothing while the row length is unknown.
Mouse clicks on a long row beyond the scanned frontier are ignored, and so are clicks on a provisional row without wrap.

### Tab Stops in Edited Rows Over 1 MiB

After an edit inside a long row (above `long_row_threshold`, 1 MiB), the display columns of the text behind the edit keep the tab alignment they had before the edit.
When the edit changes the display width by an amount that is not a multiple of the tab width, and tabs follow the edit, those columns can be off by less than the tab width until the file is reopened.
The bound is `abs(stale - exact) < tab width`, and the test `test_tab_alignment_behind_an_edit_is_kept_from_before_the_edit` in `tests/nova_editor/document/test_lazy_long_edit.py` pins it: columns up to the edit are exact, columns behind it differ from the exact value by less than a tab, and a reopened document is exact.
A width change by a multiple of the tab width keeps tabs exact (`test_a_width_change_by_a_multiple_of_the_tab_width_keeps_tabs_exact`).
Short and medium rows are always exact.
An edit that rebuilds the index of the row (see "Long Rows After Edits") measures the row again and is exact once the scan has passed it.
Bytes, saved content and cursor byte offsets are exact.

### Mixed Line Endings Show the First Style

`line_ending` and the status line show the style of the first terminator of the document.
A file with mixed endings is shown with one style, and new line breaks use the terminator of the row that holds the insertion point.
No mixed-ending detection exists, because it needs a counter in the scan loop.
The bytes are never changed.

### Memory per Piece

A piece costs about 99 bytes in a tree of 10^6 pieces, against a design target of below 80 bytes.
The tree stores 37 bytes per leaf slot; the rest is the `Piece` object and the tree nodes.
10^6 pieces cost about 99 MB, inside the 200 MB limit but not negligible.
The earlier evidence matrices never exceeded 2,006 pieces.

### `wrap_range` Is O(Measured Blocks)

`LazyWrappedDocument.wrap_range` runs after every edit and walks the measured blocks (it sums over all of them and drops those from the edit on).
Blocks are measured as the user scrolls and are never evicted, so the cost grows with the number of blocks scrolled through.
The cost and the memory slope per block are not measured.

### `LongLineIndex` Adopt and Splice Grow with the Checkpoints

`LongLineIndex.spliced` and the adopt step copy the checkpoint arrays of the row, so their cost is proportional to the number of checkpoints behind the edit.
A longer row or a smaller checkpoint step means more checkpoints.
Only the 200 MB line was measured, and the cost at a much larger number of checkpoints is not measured.

### `apply_rebase` Lock Time Grows with the Cached Long Indexes

`apply_rebase` rebases every cached long-row index while it holds the document lock on the UI thread.
The measured time was 1.13 to 1.75 ms with 8 cached indexes for 1 to 10,000 records requested, and 2.29 ms on the 200 MB line.
Those numbers are from earlier code, and the time grows with the number of cached long-row indexes.

### The Widget Save Has No `Foreground` Gate

`SaveJob` accepts a `Foreground` gate, but the widget passes none.
The measured step latency during a 5 GB save was at most 30.2 ms, so the UI thread was not starved on the earlier code.
That matrix was not repeated after the last small save fixes.
If a step over 50 ms is traced to the save thread, the gate exists in `core/foreground.py`.

### `reload()` Can Wait on the UI Thread

`reload()` blocks the UI thread until the scan has resolved row 0 of the new document, bounded by `_FIRST_ROW_WAIT` (1 second).
In practice the first block resolves in milliseconds, but a cold cache or a hung mount can use the whole second.
The time of `reload()` on the reference files is not measured.
A deferred swap would remove the wait and needs a larger rework.

### Full Disk Is Proven by Injection and a Real EFBIG

A real `ENOSPC` cannot be produced here without privileges (an unprivileged mount namespace is not permitted).
The full-disk path is proven by fault injection: `ENOSPC` through `SaveIo.write` at 50 percent of a 5 GB edited save fails with stage `write` and errno 28, the target is unchanged and the temp file is removed.
Failure-injection tests cover every write call, `fallocate`, `fsync`, `fchmod`, `replace` and `mkstemp`.
A real kernel write error is tested too: `test_a_real_efbig_leaves_the_original_removes_the_temp_file_and_shows_the_error` in `tests/nova_editor/test_widget_save_efbig.py` runs a child process with `RLIMIT_FSIZE` below the output size and `SIGXFSZ` ignored, so the write fails with `EFBIG` through the real widget save.
It asserts that the original is unchanged, the temp file is gone and the error is shown.
The injection tests are in `tests/nova_editor/core/test_save_failure.py`.

### 255 Generations

At most 255 legacy generations exist per document (`MAX_GENERATION`).
A save that needs another one clears the undo history and the clipboard record, and shows "Undo history cleared: too many saves with large deletions."
This is the only case where a save drops undo.
255 saves of a large file are not practical, so the path is tested with the constant lowered: `test_at_the_last_generation_the_history_is_cleared_and_the_old_files_are_released` in `tests/nova_editor/document/test_save_rebase.py` sets `MAX_GENERATION` to 1 (and `UNDO_COPY_LIMIT` to 0) through `monkeypatch`.
`tests/nova_editor/core/test_rebase.py` asserts that the real constant is 255.

### Approximate End Column After a UTF-8 Merge

When an edit joins bytes into new characters (for example an inserted continuation byte next to a lead byte), `LazyDocument._relocate` decodes the row up to the end of the edit to find the end location.
It does so only when that stretch is at most `RELOCATE_LIMIT` (256 KiB), or when the long index of the edited row gives a character boundary close to the edit.
In other cases the end column is approximate and can be off by the characters that merged with the neighbours.
The text itself is exact; only the cursor column after such an edit is affected.

### The Add Store Shrinks Only at a Save

Bytes that were typed or pasted stay in the add store until the next save, even after undo or delete.
The history and the internal clipboard may still reference them.
A save rebases the add store: typed text that is in the file becomes part of the file, and only orphans are copied into the fresh store (see "Undo Across Saves").
Between saves memory grows only with typed and pasted text.

### `text` Above 8 MiB

`NovaTextArea.text` returns `""` for a document above `TEXT_LIMIT` (8 MiB), and `LazyDocument.read_all(limit)` raises `WholeLineAccess` there.
Reading `text` costs O(size), so the property never decodes a large document.
Code that needs a part of a large document uses `get_text_range` or the capability methods.
The streaming save does not use `text` and has no size limit.

### Hard Links, Owner, Group, ACLs and Xattrs

A save creates a new inode and replaces the target, so a second hard link keeps the old content.
The owner, the group, ACLs and extended attributes of the target are not copied to the new file.
Only the permission bits are restored, with `fchmod`.

### The Retained Generation Keeps Disk Space

When a save has more than `UNDO_COPY_LIMIT` (8 MiB) of orphan bytes, the old file stays open as a legacy generation.
Its disk space stays allocated, although the file is unlinked, until the document closes or the history is cleared.
A save that needs more than 255 generations clears the history instead.
Typical sessions never reach either case.

### Edits Are Locked During a Save

Typing, deleting, pasting, undo and redo are refused for the length of a save, with the reason `saving`.
The notification text is the generic one of an edit refusal.
Cursor, selection, scrolling and goto keep working, and the lock is lifted on every outcome.

### `kill -9` Can Leave a Temp File

A killed process cannot run the `atexit` hook, so a `.name.xxxx.tmp` file can stay next to the target.
The original is intact, because the replace has not happened.
A normal exit, a failure and a cancel remove the temp file.

### A Save After an In-Place Change

When the file was changed in place (`MODIFIED` or `TRUNCATED`) and the user confirms the overwrite, the unchanged parts of the document are read from the file as it is now.
Bytes that were rewritten in place therefore appear in the saved file.
A truncation that removed needed bytes fails the save.

### `stat` on a Hung Mount

`save` checks the path on the UI thread before it starts the job, with one or two `stat` calls.
On a hung network mount this blocks the UI until the mount answers.
The periodic check of `nova_edit` runs on a worker thread and does not block.

### Non-Regular Files

`nova_edit` refuses to open a FIFO, a device or a directory, because opening a FIFO blocks.
A save refuses such a target at stage `prepare`.

### File Systems Without `fchmod`

`fchmod` that fails with `ENOTSUP` or `EOPNOTSUPP` is ignored, and the file keeps the mode `mkstemp` gave it (`0o600`).
Any other `fchmod` error fails the save at stage `flush`.
`posix_fallocate` is best effort and is skipped on file systems that do not support it.

### Search Limits

- **Case folding** is simple and one to one, so `ß` does not match `ss`, and `ẞ` matches only `ß` and itself.
  `İ` matches only itself, and `ı` matches only itself.
  There is no Unicode normalisation, so the NFC and NFD forms of a letter are different text.
  The tables follow the Unicode version of the running Python.
- **Edits cancel a search.**
  Any edit cancels a running search: typing, delete, paste, undo and redo.
  The edit is accepted at once, and `SearchCancelled` has the reason `text changed`.
  A save does not cancel a search, because the bytes and their offsets stay the same.
  `reload` and `load_text` cancel it with the reason `reloaded`, and `close` cancels it without a message.
  A result that arrives after an edit is dropped and never moves the cursor.
- **The search is literal.**
  The needle is literal text.
  The byte pattern that the pattern tier compiles is an implementation detail built from escaped bytes.
  There are no regular expressions, no whole-word option and no replace.
- **Needle size.**
  A plain needle is limited to 1 MiB of bytes (`MAX_NEEDLE_BYTES`).
  A needle that needs the pattern tier (case-insensitive, or with a line break) is limited to 4,096 characters (`MAX_PATTERN_CHARS`).
  A longer needle raises `SearchError` before any thread starts, and the widget posts `SearchFailed`.
- **Pattern tier cap.**
  A unit that may use the pattern tier owns at most `PATTERN_WORK_BUDGET // token_count` bytes (budget 4,000,000, minimum 256 bytes), where `token_count` is the number of needle characters.
  The cap keeps a repetitive needle from holding the GIL for long in one `re.search` call.
  A long needle therefore reads smaller windows than `CHUNK`, and its throughput is not measured on the 5 GB file.
  A case-sensitive needle without a line break uses the plain `find` tier and is not cut.
- **Validation and compilation.**
  `SearchJob` validates the needle in its constructor, which the widget calls on the UI thread, so a needle that cannot be searched fails at once with `SearchFailed`.
  The compilation (the fold table and the regexes) runs on the search thread, at the start of `run`.
- **Search start in an unindexed long row.**
  In a long row whose index does not yet resolve the column, the exact byte of the column is unknown.
  The cursor end of the selection uses the anchor of the long cursor.
  A search whose start is the other end of the selection starts at column 0 of that row, because the exact byte needs the long-index scan.
- **A pending placement and a failing source.**
  A match that waits to be placed (beyond the frontier or in a long row) keeps the jump pending.
  When the source fails in that time, the pending search placement ends with `SearchFailed`, and the cursor and the selection stay unchanged.
- **Search in an unresolved long row.**
  A match in an unresolved part of a long row is selected only when the long index has resolved both ends.
  Until then the selection does not change, `JumpProgress` reports the long-index frontier, and Escape cancels.
  A goto shows a provisional cursor instead, but a selection with estimated columns would highlight the wrong characters.
- **Case-insensitive search of non-ASCII files** runs at the pattern-tier throughput: 419 MB/s on the 5 GB reference file when the needle starts with a character that has case variants.
- **Line breaks in a needle.**
  A line break in a needle matches exactly one document terminator (`\r\n`, `\n` or `\r`).
  A needle `\n` never selects half of a CRLF, and a needle `\r\n` also matches a lone LF or a lone CR.
  The search bar of `nova_edit` takes one line, so a line break reaches the search only through the API.

---

## Testing

Tests are located under `tests/nova_editor/` and `tests/tools/`.

**Core layer:**
- `core/test_byte_source.py`, `core/test_line_index.py`, `core/test_line_index_budget.py`, `core/test_long_line_index.py` — sources and indexes, including property tests against reference implementations.
- `core/test_foreground.py`, `core/test_row_at_offset.py`, `core/test_text_width.py` — the foreground gate, `row_at_offset` and the width helpers.
- `core/test_piece_tree.py`, `core/test_add_store.py`, `core/test_piece_table.py`, `core/test_original_source.py`, `core/test_row_source.py`, `core/test_scan_now.py` — the piece table and its parts, fuzzed against plain `bytes` and a regular-expression split (`core/reference.py` holds the reference and the seeded `Rng`).
- `core/test_long_line_splice.py` — `LongLineIndex.spliced`, `resume` and `byte_to_char` against a fresh scan.
- `core/test_truncation.py`, `core/test_change_detection.py` — truncation in a subprocess and the change kinds.
- `core/test_row_scanner.py`, `core/test_line_index_from_scan.py` — `RowScanner` and `LineIndex.from_scan` against a fresh scan.
- `core/test_save_roundtrip.py`, `core/test_save_failure.py`, `core/test_save_cancel.py`, `core/test_save_targets.py`, `core/test_save_committed.py`, `core/test_save_order.py` — `SaveJob` output, failure injection through `SaveIo`, cancel, special targets and the committed state.
- `core/test_save_layout.py`, `core/test_rebase.py`, `core/test_piece_table_layout.py`, `core/test_long_line_rebased.py` — the layout, the translation, `layout_range` and `LongLineIndex.rebased`.
- `core/test_core_boundary.py` — the Textual import boundary (a plain subprocess import plus an AST scan).
- `core/test_casefold.py` — the fold, the variant classes and the static ASCII table against the full scan.
- `core/test_search_matcher.py`, `core/test_search_job.py`, `core/test_search_properties.py`, `core/test_search_context.py` — the matcher, the job (cancel, gate, stale, retry, progress), hypothesis properties and the context bytes.
- `core/search_reference.py`, `core/test_search_reference.py`, `core/fake_planner.py` — the brute-force reference model, its own tests, and the fake planner with a revision.

**Document layer (`document/`):**
- `test_lazy_document.py`, `test_lazy_wrapped_document.py`, `test_lazy_window.py` — lazy documents, wrapping and windows.
- `test_lazy_edit.py`, `test_lazy_long_edit.py`, `test_lazy_bounded_edit.py`, `test_lazy_from_text.py` — document edits against a `str` reference (`reference_text.py`), long-row edits with lowered thresholds, and `text=` sources.
- `test_edit_history.py`, `test_history_modified.py` — byte-based undo and redo records, typing coalescing, and the modified state.
- `test_edit_lock.py`, `test_lock_audit.py`, `test_lazy_document_locking.py` — the edit lock and the table accesses under the document lock.
- `test_search_plan.py`, `test_search_document_properties.py` — `search_plan`, `revision` and search properties over the real document.
- `test_save_rebase.py`, `test_save_rebase_fuzz.py`, `test_rebase_holders.py` — the rebase and its seeded fuzz, and the holders that are re-pointed at the swap.
- `test_cursor_anchor.py`, `test_long_row_anchor.py` — the cursor machine and its index adapter.
- `test_capabilities.py`, `test_navigator_capabilities.py`, `test_navigator_long_rows.py` — capability methods and navigation.
- `test_wrap_parity.py` — the drift test of the vendored wrap code (see "Vendoring Policy").

**Widget:**
- `test_widget.py`, `test_widget_capabilities.py` — widget behaviour on small documents, including `line_ending` and `indexing_progress`.
- `test_stock_characterization.py`, `test_lazy_characterization.py` — golden traces (`golden/`) that the converged widget and the unedited lazy path must still match.
- `test_convergence.py` — `text=`, `load_text` and small files on the lazy document, and the removal of the stock widget path.
- `test_lazy_widget.py`, `test_lazy_cursor.py`, `test_jump.py`, `test_highlight_limit.py` — lazy rendering, cursor, goto and highlighting.
- `test_wrapped_cursor_end.py` — the stale-height refresh in `scroll_cursor_visible`.
- `test_lazy_edit_widget.py`, `test_lazy_clipboard.py`, `test_lazy_edit_long_row_memory.py` — editing, refusal, undo, redo, clipboard and memory on long rows through the widget.
- `test_widget_save.py`, `test_widget_save_rebase.py`, `test_widget_save_efbig.py`, `test_widget_external_change.py`, `test_save_long_row_cursor.py` — the widget API of saving, the rebase of the widget, a real write error, external changes and the cursor on a long row across a save.
- `test_widget_search.py`, `test_widget_search_events.py`, `test_widget_search_long_row.py` — the widget search: selection, wrap, repeat, cancel, edits, reload, save, truncation, close, and long rows with lowered thresholds.
- `test_lazy_exports.py` — the lazy exports.
  `tests/test_package_independence.py` guards that `nova_editor` and `nova_widgets` do not depend on `nova_navigator`, and that `nova_editor` does not use the VFS.

**App:**
- `test_app.py`, `test_app_lazy.py`, `test_app_save.py`, `test_bindings.py` — the app, opening files, the save bars and key bindings (including key collisions).
- `test_app_status.py` — the status line text, its states and its coalescing.
- `test_app_footer.py` — the footer keys.
- `test_app_quit.py` — the quit flow and its two questions.
- `test_app_search.py` — the keys, the bar, the status texts and the case toggle of `nova_edit`.
- `test_app_req16.py` — a missing path, goto messages, search through F7 and the wrap toggle.

**Search reference model:**
`core/search_reference.py` is written independently of the production matcher.
It decodes the bytes to `str` with `surrogateescape`, compares character by character with its own fold, and treats a line break as one unit.
It implements direction, origin, wrap and the owned-range rules by brute force.
The properties compare the job with it over random piece layouts, random needles (also substrings of the document), both case modes, both directions, wrap on and off, and chunk sizes from 1 to 17 bytes and large.
Further properties check that the tiers return the same result on ASCII data and that the reversed pattern agrees with the forward scan.

**Helpers and benchmarks:**
- `helpers_view.py`, `test_helpers_view.py` — synthetic file builder and oracle.
- `tests/tools/test_measure_view.py` and `tests/tools/test_measure_core.py` — smoke tests of the harnesses.

Run all tests:
```sh
uv run pytest tests/nova_editor tests/tools -q
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

**Upstream-derived files:**
Every file that is derived from Textual 8.2.8 is listed with its upstream path in `src/nova_editor/UPSTREAM.md`.
They are:
- `widget/_text_area.py` and `widget/_text_area_theme.py`.
- `widget/_tree_sitter.py` — the optional tree-sitter loader, from `textual/_tree_sitter.py`.
- `document/_document.py`, `_document_navigator.py`, `_edit.py`, `_history.py`, `_syntax_aware_document.py` and `_wrapped_document.py`.
- `document/_wrap.py` — `compute_wrap_offsets` from `textual/_wrap.py`, `cell_width_to_column_index` from `textual/_cells.py` and the `loop_last` helper from `textual/_loop.py`; `cell_len` comes from `rich.cells`.

Everything else in the package is native to `nova_editor`.

**No private Textual import remains.**
The package imports only public Textual modules.
The proving command prints nothing:

```sh
git grep -nE "^\s*(from|import) +textual\.(_|document|widgets\._text_area)" -- src/nova_editor src/tools
```

Docstring cross-references to `textual.widgets._text_area` are text, not imports.
Vendoring the three private modules removed the risk that a Textual release (the dependency has no upper bound) renames or removes them and breaks `import nova_editor.widget` for every embedding app.

**Drift test:**
`tests/nova_editor/document/test_wrap_parity.py` compares the vendored code with the installed Textual:
- `test_compute_wrap_offsets_matches_upstream` on generated strings, widths and tab sizes.
- `test_cell_width_to_column_index_matches_upstream`.
- `test_get_language_agrees_with_upstream` for the tree-sitter loader.

The test looks at upstream; the shipped code does not.
It is skipped when upstream removes the module (`pytest.importorskip`).

---

## Updating from Upstream

When Textual releases a new version and we want to update our vendored code, follow the procedure in `src/nova_editor/UPSTREAM.md`.
After the update run the drift test and the proving command above:

```sh
uv run pytest tests/nova_editor/document/test_wrap_parity.py -q
git grep -nE "^\s*(from|import) +textual\.(_|document|widgets\._text_area)" -- src/nova_editor src/tools
```

A failing drift test means that the upstream wrap code changed and the vendored copy must be updated.
A non-empty grep means that a private import came back.

---

## Future Work

- **Integration:** Embed in `nova_navigator` as an editor dialog for large file viewing and light editing.
