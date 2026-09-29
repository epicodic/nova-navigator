# Text Editor Package (`nova_editor`)

This document describes the architecture and design of the `nova_editor` package, a standalone Textual-based text editor with vendored TextArea widget implementation.

---

## Overview

**Purpose:** Provide a modern, Textual-based text editor widget and standalone application designed to handle large files and very long lines with minimal overhead.

The package is separate from `nova_navigator` and can be used independently.

**Status:** The core layer exists and is tested; the widget currently uses a vendored copy of Textual 8.2.8's `TextArea` (renamed to `NovaTextArea`) with no modifications to core behavior.

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
Re-exports `rich.cells` without importing Textual.
Functions `advance_disp`, `locate_cover`, `safe_cut`, `resync`, `utf8_len` support the long-line index.

`line_index` — Sparse background line index over the file.
`LineIndex` scans rows in a daemon thread, appending stride entries (every 64th row by default).
`RowRange` describes a row as a byte range: `(start, content_end, end)` where the terminator is bytes from `content_end` to `end`.
`LineSnapshot` gives `(count, complete, error)`: count is a lower bound until complete.

`long_line_index` — Lazy checkpoint index for one very long row.
`LongLineIndex` records character column, display column and byte offset checkpoints every 65,536 characters.
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

**UI-path byte budget (DEC-14):**

Every `row_range` and `lines` call reads at most 4 MiB (hard rule).
Returns `None` or a shorter list when the budget is exceeded.
Rows longer than 16 KiB are recorded during scan in a side table (cap 524,288 entries, about 12 MiB worst case) so walks jump over them.
`lines` returns at most 128 rows per call.
Default walk window is 64 KiB, filled in min(64 KiB, threshold+2) byte chunks.
With defaults, worst case is about 3.1 MB per call; measured worst 0.31 ms on a 5 GB file and 8 ms / 2.5 MB on files with 15–17 KiB lines.

**Long-line index:**

Checkpoints (character column, display column, byte offset) record every 65,536 characters relative to the row start.
For a 200 MB line: 3,190 checkpoints, about 77 KB of arrays, peak 77.5 MiB RssAnon.
UTF-8 resync on block boundaries; surrogateescape for invalid bytes; tabs, wide chars and combining marks via `rich.cells`.
Non-blocking `try_*` queries return `None` beyond the scanned frontier (no waiting; a far query returned in 0.003 ms vs ACT1's 1.8–4.3 s blocking).
Blocking is opt-in via `wait_until_known` with timeout/cancel support.
Estimates (character count, display width) available immediately once complete.

**Concurrency:**

Append-only publication under one lock per index (LineIndex uses a threading.Lock; LongLineIndex uses threading.Condition).
Callbacks registered via `subscribe()` run on the scan thread outside all locks.
No lock is held across I/O.

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
The boundary test therefore stubs the parent package and loads core modules with it; lazy exports are planned for the final activity.

### 2. **Document Layer** (`nova_editor/document/`)

The document interface consumed by the widget, built on core.
Provides the cursor navigation, edit tracking and view API that the widget layer expects.
Currently vendored from Textual 8.2.8, refactored to use core indexes for large files.

### 3. **Widget Layer** (`nova_editor/widget/`)

Textual widget implementation containing:
- **`NovaTextArea`** — the main editor widget (vendored from Textual 8.2.8, renamed from `TextArea`)
- **`_text_area_theme.py`** — theme support for syntax highlighting

The widget currently behaves exactly like Textual's stock `TextArea`; all public APIs are preserved.

### 4. **Application Layer** (`nova_editor/app.py`)

Standalone Textual app (`NovaEditApp`) providing:
- File loading/saving via `Ctrl+S` and `Ctrl+Q`
- Footer showing file path and keyboard shortcuts
- Entry point `main()` for the `nova_edit` CLI command

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

Run all tests:
```sh
uv run pytest tests/nova_editor/
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

- **Lazy loading:** Stream file content into buffers as needed
- **Long lines:** Handle lines longer than the viewport
- **Integration:** Embed in `nova_navigator` as an editor dialog
- **Performance:** Profile and optimize for very large files (>1GB)
- **Syntax highlighting:** Full tree-sitter support
