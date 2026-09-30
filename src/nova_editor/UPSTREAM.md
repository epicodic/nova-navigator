# Upstream Reference

This file documents the vendored code from upstream Textual and how to manage updates.

## Upstream Source

**Project:** Textual  
**License:** MIT  
**Version:** 8.2.8  
**Repository:** https://github.com/Textualize/textual  
**Homepage:** https://textual.textualize.io/

## Vendored Files

The following files are vendored from Textual 8.2.8 with modifications:

| Vendored Path | Upstream Path | Notes |
|---|---|---|
| `widget/_text_area.py` | `src/textual/widgets/_text_area.py` | Class renamed: `TextArea` → `NovaTextArea`; imports updated; 1200+ lines added for lazy document support, cursor state machine, long-row handling, and new messages |
| `widget/_text_area_theme.py` | `src/textual/_text_area_theme.py` | Imports updated to reference `NovaTextArea`; no functional changes |
| `document/_document.py` | `src/textual/document/_document.py` | Capability methods added (row access API for lazy documents) |
| `document/_document_navigator.py` | `src/textual/document/_document_navigator.py` | Imports updated; methods added for lazy-wrapped-document support and smart home on long rows |
| `document/_edit.py` | `src/textual/document/_edit.py` | Imports updated to reference `NovaTextArea`; no functional changes |
| `document/_history.py` | `src/textual/document/_history.py` | Imports updated to use vendored document package; no functional changes |
| `document/_syntax_aware_document.py` | `src/textual/document/_syntax_aware_document.py` | Imports updated to use vendored document package; no functional changes |
| `document/_wrapped_document.py` | `src/textual/document/_wrapped_document.py` | Imports updated to use vendored document package; no functional changes |
| `document/__init__.py` | `src/textual/document/__init__.py` | No changes |

## New Files (Not Vendored)

The following modules are native to `nova_editor` (not copies of upstream Textual):

| Path | Purpose |
|---|---|
| `document/_lazy_config.py` | Tunable thresholds for lazy documents (`LazyConfig` dataclass). |
| `document/_lazy_document.py` | Read-only document backed by `ByteSource` and `LineIndex`. |
| `document/_lazy_wrapped_document.py` | Wrapping layer for lazy documents with windowed rendering. |
| `document/_cursor_anchor.py` | Cursor state machine for long rows (RESOLVED, PROVISIONAL, PENDING). |
| `document/_long_row_anchor.py` | Long-line index adapter with byte-anchored cursor integration. |
| `widget/_lazy_window.py` | Windowed text rendering for medium and long rows. |
| `widget/_long_row_cursor.py` | Cursor machine and provisional layout for long rows in the widget. |

## Nova Editor Changes to Vendored Code

### `widget/_text_area.py`

**Imports added:**
- Core layer: `ByteSource`, `SourceChanged`, `text_width.utf8_len`.
- Document layer: `LazyConfig`, `LazyDocument`, `LazyWrappedDocument`, cursor machine (`CursorMachine`, `CursorState`, `Op`, `Verdict`).
- Widget layer: `_lazy_window` (windowed rendering), `_long_row_cursor.LongRowCursor`.

**New constants:**
- `_WORD_WINDOW = 8192` — characters scanned on each side of cursor by word locators.
- `DEFAULT_HIGHLIGHT_LIMIT = 1_048_576` — syntax highlighting disabled above this file size.
- `_DEFAULT_INDENT_WIDTH = 4`.
- `_PREFIX_CHARS = 1024` — characters returned by `get_line()` for lazy documents.
- `_ESTIMATE_INTERVAL = 0.25` — seconds between size re-estimates while indexes grow.
- `_MESSAGE_INTERVAL = 0.1` — seconds between `IndexProgress` messages (max 10 per second).
- `_PROGRESS_BELOW_ONE = 0.999999` — pending jump progress clamped below 1.

**Soft-wrap default changed:**
- Old: `soft_wrap: bool = True`.
- New: `soft_wrap: bool = False` (no wrapping by default; F4 toggles wrap).

**New reactive properties:**
- `pending_progress: Reactive[float | None]` — fraction of a deferred jump; `None` when idle.

**New message classes:**
- `SourceChanged(reason)` — file changed or unreadable (lazy documents only).
- `JumpProgress(fraction)` — deferred jump progress.
- `IndexProgress(count, complete)` — line scan progress.
- `IndexingComplete` — line scan finished.
- `JumpCompleted(row, column)` — jump finished; column is estimate if PROVISIONAL.
- `JumpRejected(reason)` — jump out of range.

**New constructor parameter:**
- `_prebuilt_document: LazyDocument | None` — skip `Document(text)` when provided (used by `open()`).

**New class method:**
- `open(source, language, soft_wrap, config, highlight_limit, **kwargs)` — open a file lazily.

**New/modified instance attributes:**
- `_lazy: LazyDocument | None` — the lazy document (or `None` for eager mode).
- `_long_cursor: LongRowCursor | None` — cursor machine for the current long row.

**Modified render path:**
- Row rendering routes through capability methods for lazy documents (windowed rendering for long rows).
- Cursor state machine integration: provisional cursors show estimated columns until exact.

**New binding:**
- `Escape` → `action_cancel_pending` (active only during pending jumps).

### `document/_document.py`

**Capability methods added (all default implementations; lazy documents override):**
- `is_long(row)` — return whether row is never decoded as a whole.
- `line_length(row)` — return character count or `None`.
- `row_byte_length(row)` — return UTF-8 byte length.
- `column_slice(row, start, stop)` — return substring.
- `has_char_at(row, column)` — return whether character exists.
- `display_column(row, column, tab_width)` — return display column.
- `column_at_display(row, x, tab_width)` — return character column at display position.
- `byte_offset(row, column)` — return absolute byte offset.

### `document/_document_navigator.py`

**New helper methods:**
- `_line_length(row, fallback)` — return row length or fallback.
- `_long_wrapped(row)` — return `LazyWrappedDocument` if row is long, else `None`.
- `_known_or_estimated_length(row)` — exact length or estimate for unscanned long rows.
- `end_is_known(row)` — return whether row length is known (affects `get_location_end`).
- `_is_section_start(wrapped, row, column)` — check if column is a wrap-section boundary.

**Modified methods (to support lazy documents):**
- `is_start_of_wrapped_line()` — added long-row case.
- `is_end_of_document_line()` — uses `line_length()` instead of indexing; handles unknown lengths.
- `is_end_of_wrapped_line()` — added long-row case.
- `is_start_of_wrapped_line()` — uses bisect helper instead of direct list lookup.

## Additional Changes

### `_TREE_SITTER_PATH` Fix

- **Old:** `_TREE_SITTER_PATH = Path(__file__).parent / "../../tree-sitter/"`
  This pointed to a non-existent path; syntax highlighting was empty.
- **New:** `_TREE_SITTER_PATH = Path(textual.__file__).parent / "tree-sitter"`
  Now uses the tree-sitter directory from the installed `textual` package.

### `soft_wrap` Default

- Changed from `True` to `False`.
- Rationale: lazy documents do grid-wrap above the word-wrap limit anyway; starting with no-wrap is simpler for large files.
- Users can toggle with F4 in `nova_edit`.

### Remaining Private Textual Imports

These imports remain (unavoidable; part of Textual's public vendoring API):

```
textual._cells (cell_len, cell_width_to_column_index) — only the untouched vendored _wrapped_document.py; the other document classes use rich.cells.cell_len
textual._wrap (compute_wrap_offsets) — used by WrappedDocument and LazyWrappedDocument
textual._tree_sitter (TREE_SITTER, get_language) — used by SyntaxAwareDocument
```

These should be replaced if Textual provides public equivalents in future versions.

## Why Vendored?

The TextArea widget is vendored (copied) rather than subclassed because:

1. **Anticipated heavy modifications**: Future versions will add support for very large files, extremely long lines, and custom buffer management.
2. **Minimize coupling**: By vendoring, we avoid tight coupling to Textual's internal APIs, which may change between versions.
3. **Control over performance optimizations**: We can optimize the implementation for Nova Navigator's specific use cases.
4. **Freedom from Textual version constraints**: We pin our own vendored code independently.

## Updating from Upstream

To upgrade from a newer version of Textual:

1. Check the Textual repository for the target version.
2. Compare the vendored files against the upstream version using `diff`:
   ```bash
   diff -u src/nova_editor/widget/_text_area.py \
     /path/to/textual/src/textual/widgets/_text_area.py
   ```
3. For each file, carefully merge upstream changes while preserving:
   - The `NovaTextArea` class name (instead of `TextArea`).
   - All import paths pointing to `nova_editor` packages.
   - All capability method additions (on `DocumentBase` and `DocumentNavigator`).
4. Re-apply the lazy branches guarded by `is_long` and the capability method calls listed in "Nova Editor Changes to Vendored Code" above.
5. Test thoroughly using `uv run qa` and manual testing.
6. Update the version reference at the top of this file.

## License

Textual is licensed under the MIT License. See `LICENSE.textual` in this directory.
