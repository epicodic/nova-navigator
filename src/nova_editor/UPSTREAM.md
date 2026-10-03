# Upstream Reference

This file documents the vendored code from upstream Textual and how to manage updates.

## Upstream Source

**Project:** Textual  
**License:** MIT  
**Version:** 8.2.8  
**Repository:** https://github.com/Textualize/textual  
**Homepage:** https://textual.textualize.io/

## Vendored Files

The following files are vendored from Textual 8.2.8.
The Notes column says what differs from upstream, and what ACT3, ACT4, ACT5, ACT6 and ACT7 changed on top of the ACT2 state.

| Vendored Path | Upstream Path | Notes |
|---|---|---|
| `widget/_text_area.py` | `src/textual/widgets/_text_area.py` | Class renamed `TextArea` to `NovaTextArea`; imports updated; lazy document support, cursor state machine, long-row handling and new messages added; ACT4 removed the stock `Document` path and added editing on piece tables, refusal and the clipboard; ACT5 added saving, reload, external change detection and the save messages; ACT6 added the search glue, its messages and the select-all key move; ACT7 imports `TREE_SITTER` and `get_language` from `widget/_tree_sitter.py`, guards `_refresh_size` in `scroll_cursor_visible`, added `line_ending` and `indexing_progress`, and made `cursor_byte_offset` return `None` while the row is unresolved (see below) |
| `widget/_text_area_theme.py` | `src/textual/_text_area_theme.py` | Imports updated to reference `NovaTextArea`; unchanged by ACT3 |
| `document/_document.py` | `src/textual/document/_document.py` | Capability methods added; `cell_len` now comes from `rich.cells`; `EditResult` gained `removed`, `start_byte` and `inserted` (ACT4); unchanged by ACT5 |
| `document/_document_navigator.py` | `src/textual/document/_document_navigator.py` | Lazy-document branches added (see below); `cell_len` now comes from `rich.cells` |
| `document/_edit.py` | `src/textual/document/_edit.py` | Imports updated to reference `NovaTextArea`; ACT4 made `Edit` record piece references and added coalescing; ACT5 added `contents` and `rewrite` (see below) |
| `document/_history.py` | `src/textual/document/_history.py` | Imports updated to use the vendored document package; ACT4 changed the batching defaults, `record` and added restore helpers; ACT5 added revisions, branches and the modified state (see below) |
| `document/_syntax_aware_document.py` | `src/textual/document/_syntax_aware_document.py` | Imports updated to use the vendored document package; unchanged by ACT3 and ACT4; now only the highlighting mirror of a lazy document |
| `document/_wrapped_document.py` | `src/textual/document/_wrapped_document.py` | Imports use the vendored document package; ACT7 took `cell_len` from `rich.cells` and the wrap helpers from `document/_wrap.py` |
| `document/_wrap.py` | `src/textual/_wrap.py`, `src/textual/_cells.py::cell_width_to_column_index`, `src/textual/_loop.py::loop_last` | Carries `chunks`, `compute_wrap_offsets` and the `cell_width_to_column_index` helper; docstrings written for ruff, `cell_len` and `get_character_cell_size` from `rich.cells`, `loop_last` inlined as `_loop_last`, and the prefix sum and the folding of an over-wide chunk split out of `compute_wrap_offsets` as `_cumulative_tab_widths` and `_fold_chunk` (ACT7) |
| `widget/_tree_sitter.py` | `src/textual/_tree_sitter.py` | Provides `TREE_SITTER` and `get_language`; the optional `tree_sitter` import goes through `import_module` behind a guard (so ty needs no suppression), and the module and `TREE_SITTER` docstrings were added (ACT7) |
| `__init__.py` (package root) | none | Native: exports `NovaTextArea`, `LazyConfig` and `DEFAULT_HIGHLIGHT_LIMIT` lazily through PEP 562 `__getattr__` and `__dir__`, so importing `nova_editor.core` does not load Textual (ACT7) |
| `document/__init__.py` | `src/textual/document/__init__.py` | Empty at the end of ACT4; ACT5 exports `EditsLocked`, `LazyDocument` and `RowUnavailable` (not upstream's exports); unchanged by ACT6 |

## New Files (Not Vendored)

The following modules are native to `nova_editor` and are not copies of upstream Textual.

| Path | Purpose |
|---|---|
| `core/` | Textual-free byte source, line index, long-line index and width helpers (see `docs/editor.md`). |
| `core/foreground.py` | `Foreground` gate that lets the background scans give way to the UI thread (ACT3). |
| `core/pieces.py` | `Piece`, `Aggregate`, `Content`, the `PieceSource` protocol and the junction rules (ACT4). |
| `core/piece_tree.py` | `PieceTree`, the counted B+ tree of pieces (ACT4); a replace deletes its piece range in one pass through `_delete_pieces`, `_delete_range` and `_repair_children` (ACT7). |
| `status_line.py` | `StatusState`, `format_status` and `StatusLine`, the one-row status line of `nova_edit` (ACT7). |
| `core/add_store.py` | `AddStore` and `AddSegment`, the append-only store of added bytes (ACT4). |
| `core/original_source.py` | `OriginalSource` and `RowNotIndexed`, the row oracle of the original file on top of `LineIndex` (ACT4). |
| `core/piece_table.py` | `PieceTable`, the editable document with row queries and the open tail (ACT4). |
| `core/row_source.py` | `RowSource`, a `ByteSource` over the pieces of one row (ACT4). |
| `core/memory_source.py` | `BytesSource`, a `ByteSource` over bytes in memory (ACT4). |
| `core/row_scanner.py` | `RowScanner`, the row-boundary scan moved out of `LineIndex` (ACT5). |
| `core/save.py` | `FileIdentity` and `ChangeKind` re-exports, `check_path`, `SaveSettings`, `SaveIo`, `SaveJob`, `SaveFailed`, `SaveCancelled`, `SaveResult` (ACT5). |
| `core/save_layout.py` | `SaveLayout`, the runs a save wrote, by source (ACT5). |
| `core/rebase.py` | `Rebaser`, `RebasePlan` and `UNDO_COPY_LIMIT` (ACT5). |
| `core/casefold.py` | `fold1`, the static ASCII table and the lazily built table of case variants (ACT6). |
| `core/search.py` | `compile_matcher`, `Matcher`, `SearchJob`, `SearchSpec`, `SearchSettings`, `SearchPlan`, `SearchPlanner`, `SearchResult`, `SearchProgress` and the errors (ACT6). |
| `widget/_search_run.py` | `SearchRun`, `SearchOutcome` and `run_search_thread`, the state and thread body of one widget search (ACT6). |
| `search_bar.py` | `SearchBar` and `SearchStatus`, the inline widgets of the `nova_edit` search (ACT6). |
| `document/_lazy_config.py` | Tunable thresholds for lazy documents (`LazyConfig` dataclass). |
| `document/_lazy_document.py` | Editable document over `PieceTable` (read-only in ACT3); ACT5 added the edit lock, `plan`, `prepare_rebase`, `apply_rebase` and the save registration; ACT6 added `search_plan` and `revision`. |
| `document/_lazy_wrapped_document.py` | Wrapping layer for lazy documents with grid wrap and a sparse height estimate; imports `compute_wrap_offsets` from `document/_wrap.py` (ACT7). |
| `document/_cursor_anchor.py` | Cursor state machine for long rows (`RESOLVED`, `PROVISIONAL`, `PENDING`). |
| `document/_long_row_anchor.py` | Adapter from a long-line index to the cursor machine; ACT5 added `rebind`. |
| `widget/_lazy_window.py` | Windowed text of medium and long rows. |
| `widget/_long_row_cursor.py` | Cursor machine holder and provisional window layout for long rows; ACT5 added `rebase`. |

ACT3 also extended the core, which stays Textual-free:
- `LineSnapshot.scanned_bytes` and `LineIndex.row_at_offset`.
- The `Foreground` gate and its `foreground=` argument on both indexes.
- Width helpers in `core/text_width.py`: `locate_cover` and the ASCII/non-ASCII run splitting in `_cells`.

ACT4 extended the core, which stays Textual-free:
- The piece table modules listed above.
- `LineIndex.scan_now()`, which runs the scan on the calling thread.
- `LongLineIndex.spliced`, the `Edit` tuple, the `resume=` argument, `byte_to_char`, `quiescent` and `SPLICE_SYNC_BYTES`.

ACT5 extended the core, which stays Textual-free:
- The modules `row_scanner`, `save`, `save_layout` and `rebase` listed above.
- `ChangeKind`, `FileIdentity`, `SourceChanged.kind`, `PreadSource.from_fd`, `identity`, `check` and `unverified_reader` in `byte_source.py`.
- `LineIndex.from_scan`, `clear_subscribers` and the use of `RowScanner` in the scan.
- `LineIndexSettings`, the `TypedDict` of the keyword settings of `LineIndex`.
- `_UnverifiedReader` in `byte_source.py`, the reader that `PreadSource.unverified_reader` returns (it checks short reads only).
- `LongLineIndex.clear_subscribers`.
- `PlanPart`, `SavePlanner` and `PauseGate` in `core/save.py`: the parts of a planned range, what the save job needs from the document, and the gate it asks for a pause.
- `SaveJob.committed` and `SaveFailed.committed`.
- `PieceTable.source_of`.
- `LongLineIndex.rebased`.
- `PieceTable.layout_range`, `register_legacy` and `legacy`, and the generation constants and helpers in `pieces.py` (`MAX_GENERATION`, `make_src`, `generation_of`, `segment_of`).

ACT6 extended the core, which stays Textual-free:
- The modules `casefold` and `search` listed above, exported from `core/__init__.py` (`SearchSpec`, `SearchJob`, `SearchSettings`, `SearchPlan`, `SearchPlanner`, `SearchResult`, `SearchProgress`, `SearchError`, `SearchCancelled`, `SearchStale`).
- `LazyDocument.search_plan` and `LazyDocument.revision` in the document layer.

## Nova Editor Changes to Vendored Code

### `widget/_text_area.py` (status line and import changes)

**Imports:** `TREE_SITTER` and `get_language` come from `widget/_tree_sitter.py` instead of `textual._tree_sitter`.
**`scroll_cursor_visible`:** when `soft_wrap` is on and `wrapped_document.height` exceeds `virtual_size.height`, it calls `_refresh_size()` first, because the estimated height of an unmeasured region moves with the measurements and Textual clamps the scroll offset to `virtual_size`.
**New properties:** `line_ending` (`"LF"`, `"CRLF"` or `"CR"`, from the newline of the document) and `indexing_progress` (the fraction of the document that the line scan covered, exactly 1.0 when indexing is complete or the document is empty).
**`cursor_byte_offset`:** the `_track_cursor()` call moved inside the `try`, and `RowUnavailable` and `IndexError` give `None`, so the status line shows `Byte ?` while the row cannot be resolved.

### `document/_wrapped_document.py` and `document/_lazy_wrapped_document.py`

`cell_len` comes from `rich.cells`.
`cell_width_to_column_index` and `compute_wrap_offsets` come from `document/_wrap.py`, which replaces the imports of `textual._cells` and `textual._wrap`.

### `document/_wrap.py` and `widget/_tree_sitter.py`

Both are vendored from Textual 8.2.8 (see the file table) so that no private Textual module is imported.
`cell_width_to_column_index` is the helper that was imported from `textual._cells`.
In `_wrap.py`, every function has a docstring, `cell_len` and `get_character_cell_size` come from `rich.cells`, and `loop_last` is the private `_loop_last`.
In `_tree_sitter.py`, the optional `tree_sitter` package is imported with `import_module` inside `try`, and the module keeps `TREE_SITTER` and a cache of loaded languages.

### `__init__.py`

The public names are resolved on first access by a module `__getattr__`, and `__dir__` lists them.
`TYPE_CHECKING` imports keep the type checkers working.

### `core/piece_tree.py`

Replacing a range no longer calls `_delete_at` once per piece.
`_delete_pieces(index, count)` clears the tree when it holds exactly those pieces.
Otherwise `_delete_range` slices the boundary leaves, drops the nodes that lie wholly inside the range and lets `_repair_children` rebalance each cut node.
This is native code, not vendored.

### `app.py`

- `NovaEditApp` yields `Footer(compact=True, show_command_palette=False)` instead of the removed `EditorFooter`, and its bindings are `Binding` objects (Escape for `cancel_save` is hidden).
- It yields a `StatusLine` built from `_status_state()` and refreshes it on selection, text, index progress, indexing complete, jump completed and reload messages, and on changes of `pending_progress` and `soft_wrap`.
- `JumpRejected` is shown as a warning notification.
- The header sub title holds the file path, and a save to a new path updates it.
- `action_quit` cancels a search, a pending jump and a running save, then asks through `ConfirmBar.ask_quit` when a save still runs or the document is modified.
- The new `quit` choice of the confirm bar (key `Q`) exits.
- The constructor takes the keywords `soft_wrap`, `config` (a `LazyConfig`) and `editor_class` (a `TimedNovaTextArea` subclass, used by the benchmark probe).

### `widget/_text_area.py`

**Imports:**
- Added: `functools`, `threading`, `time`, `Callable`, `Any`, `TypeVar`, `cast`, `import textual`, `textual.timer.Timer`.
- Added from the core: `ByteSource`, `SourceChanged` (as `CoreSourceChanged`), `text_width.utf8_len`.
- Added from the document layer: `CursorMachine`, `CursorState`, `Op`, `Verdict`, `LazyConfig`, `LazyDocument`, `RowUnavailable`, `LazyWrappedDocument`.
- Added from the widget layer: `WindowText`, `section_window`, `window_text`, `PROGRESS_BELOW_ONE`, `LongRowCursor`.
- Removed: `textual._cells` (`cell_len`, `cell_width_to_column_index`) and `expand_tabs_inline`.

**New constants:**
- `_WORD_WINDOW = 8192` — characters scanned on each side of the cursor by the word locators.
- `DEFAULT_HIGHLIGHT_LIMIT = 1_048_576` — syntax highlighting is off above this file size.
- `_DEFAULT_INDENT_WIDTH = 4`.
- `_PREFIX_CHARS = 1024` — characters returned by `get_line()` for a medium or long row of a lazy document.
- `_ESTIMATE_INTERVAL = 0.25` — seconds between size re-estimates while indexes grow.
- `_MESSAGE_INTERVAL = 0.1` — seconds between `IndexProgress` (and `JumpProgress`) messages.
- `_PLACEHOLDER_CELL` — fills the part of a window that the scan has not reached.
- `PROGRESS_BELOW_ONE` is defined in `widget/_long_row_cursor.py` and imported here.

**Constructor defaults:**
- `soft_wrap` of the constructor and `code_editor()` changed from `True` to `False`; the class-level reactive default is unchanged.
- `indent_width` uses `_DEFAULT_INDENT_WIDTH`.
- New parameter `_prebuilt_document: LazyDocument | None` skips `Document(text)` (used by `open()`).

**New members:**
- Reactive `pending_progress`.
- Classmethod `open()` and the properties `is_estimating`, `cursor_state`, `column_exact`, `cursor_byte_offset`, `line_count`, `line_count_exact`, `indexing_complete`, `highlight_active`.
- Methods `close`, `peek_cursor_state`, `toggle_wrap`, `cancel_pending`, `goto_line`, `goto_byte`, `check_action`, `action_cancel_pending`, `on_event`, `validate_read_only`, `_on_unmount`, and the private helpers of the jump, estimate, cursor and mouse code.
- Messages `SourceChanged`, `JumpProgress`, `IndexProgress`, `IndexingComplete`, `JumpCompleted`, `JumpRejected`.
- Decorator `_guard_source`, which turns a core `SourceChanged` into the failed state of the widget.
- Binding `Escape` to `cancel_pending`, active only while a jump is pending.
- Instance attributes `_lazy` and `_long_cursor`.

**Edited upstream members:**
- Construction and documents: `__init__`, `code_editor`, `_set_document`, `_resolve_language` (factored out of `_set_document`), `_watch_language`, `load_text`, `text`.
- Selection and cursor: `_watch_selection`, `select_line`, `select_all`, `clamp_visitable`, `cursor_at_end_of_line`, `scroll_cursor_visible`, `move_cursor`, `move_cursor_relative`, `_recompute_cursor_offset`, `find_matching_bracket` (returns `None` for a lazy document).
- Cursor actions: `action_cursor_left`, `action_cursor_right`, `action_cursor_up`, `action_cursor_down`, `action_cursor_line_start`, `action_cursor_line_end`, `action_cursor_word_left`, `action_cursor_word_right`, `action_cursor_page_up`, `action_cursor_page_down`.
- Word and column helpers: `get_cursor_word_left_location`, `get_cursor_word_right_location`, `get_column_width`, `cell_width_to_column_index`, `action_delete_word_right` (edited only to avoid a whole-row read).
- Rendering and size: `get_line`, `render_line`, `_render_line`, `_watch_soft_wrap`, `_refresh_size`.
- Mouse and events: `_on_mouse_down`, `_on_mouse_move`, `_on_mount`.
- Also changed: `_TREE_SITTER_PATH` (see below).

Together these routes cursor moves, rendering and row access for lazy documents through the capability methods and the cursor machine.

**ACT4: removal of the stock widget path.**
The widget has one document type, `LazyDocument`.
- Removed imports: `Document`, `DocumentBase` and `WrappedDocument`.
- Removed members: `is_lazy`, `validate_read_only` (a lazy document is no longer read-only), `_drive_stock` and `_enable_lazy_highlighting` (replaced by `_attach_highlighting`).
- `text=`, `load_text` and the `text` setter build a lazy document over a `BytesSource` through `_text_document` and `_replace_document`.
  `_finish_document` applies the language and resets the layout, and `_wire_scan` observes the scan of the current document.

**ACT4: new imports, constants and members.**
- Added imports: `stat`, `BytesSource`, `PreadSource`, `Content` (as `PieceContent`), `SURROGATE_ESCAPE`, `WholeLineAccess`.
- New constants: `SMALL_FILE_LIMIT = 1_048_576` (`open()` reads a file up to this size into memory), `TEXT_LIMIT = 8 MiB` (the `text` property returns `""` above it), `BRACKET_SEARCH_LIMIT = 1_048_576`, `_INVALID_BYTE_TABLE` (U+DC80 to U+DCFF to U+FFFD for rendering and the system clipboard) and `_PAD_SLACK_CELLS = 1024` (a rendered line is padded at most this far beyond the region width).
- New function `_open_source(path)`, which returns a `BytesSource` up to `SMALL_FILE_LIMIT` and a `PreadSource` otherwise.
- New message `EditRefused`.
- New class attribute `clipboard_cap = 2_097_152` and the dataclass `_ClipboardRecord`.
- New members: `_unresolved_reason`, `_refuse_edit`, `_reset_cursor_machine`, `_roll_back`, `_store_clipboard`, `_internal_clipboard`, `_search_matching_bracket`.
- New attributes: `_clipboard_record` and `_edit_refused`.

**ACT4: edited upstream members.**
- Edit paths: `edit` (refusal, cursor machine reset, `wrap_range`), `undo`, `redo`, `_undo_batch`, `_redo_batch` (both return whether the batch was applied; a refused batch is restored in the history), `_replace_via_keyboard`, `_delete_via_keyboard`.
- Clipboard: `action_cut`, `action_copy`, `action_paste`.
- Documents and text: `__init__`, `code_editor`, `_set_document`, `load_text`, `text`, `open` (small files in memory, scan on the calling thread), `close`, `find_matching_bracket` (bounded by `BRACKET_SEARCH_LIMIT`).
- Rendering: `render_line`, `_render_line`, `get_line` (invalid bytes are drawn as U+FFFD in the strip text only).
- Lazy machinery that lost its stock branch: `_drive_lazy`, `_run_jump`, `_lazy_move`, `_reconcile_cursor`, `_track_cursor`, `_refresh_size`, `_estimate_tick`, `_reestimate` and the other cursor and estimate helpers.

### `widget/_text_area.py` (ACT5)

**New imports:** `os`, `Iterator`, `ChangeKind`, `FileIdentity`, `RebasePlan`, and `PlanPart`, `SaveIo`, `SaveJob`, `SaveResult`, `SaveSettings`, `check_path` and the core `SaveCancelled`, `SaveFailed` and `SaveProgress` (aliased with a `Core` prefix) from `core/save`.
**New constants and helpers:** `STALE_REASON`, `_FIRST_ROW_WAIT`, `_stat_identity`, `_DocumentPlanner` and `_SaveRun`.
**New messages:** `Reloaded`, `ReloadFailed`, `SaveProgress`, `Saved`, `SaveFailed`, `SaveCancelled` and `SaveNeedsConfirmation`; `SourceChanged` gained `kind`.
**New class attributes:** `save_settings`, `save_io` and `save_clock`.
**New names:** `ExternalCheck` (the pure part of an external-change check), `_SEVERITY` (the order of the kinds that `_fail_source` keeps) and `_save_epoch`.
**New members:** `modified`, `saving`, `save`, `check_external_change`, `reload`, `cancel_save`, `begin_external_check`, `apply_external_check`, `refresh_after_rebase`, and the private `_reachable_edits`, `_reachable_contents`, `_apply_translated`, `_start_save`, `_save_thread`, `_post_save_outcome`, `_on_save_report`, `_announce_save_progress`, `_end_save`, `_finish_save`, `_conclude_save`, `_discard_reloaded`, `_lift_stale` and `_abandon_save`.
**New attributes:** `file_path`, `_held_identity`, `_stale_kind`, `_save_run` and `_open_config`.
**Edited members:** `__init__`, `open` (records the path, the held identity and the configuration), `close` (abandons a running save), `_replace_document` and `load_text` (refused during a save), `_fail_source` and `_guard_source` (carry the `ChangeKind` and set the edit lock), `edit`, `_render_line_guarded`, `_reestimate`, `cursor_byte_offset`, `_run_jump`, `_track_cursor`, `_reconcile_cursor` and `_mouse_target` (pass the kind of a `SourceChanged`), and `_store_clipboard` (keeps no record during a save).

### `widget/_text_area.py` (ACT6)

**New imports:** `SearchError`, `SearchJob`, `SearchPlan`, `SearchSettings` and `SearchSpec` from `core/search`, the core `SearchProgress` (aliased `CoreSearchProgress`), and `SearchOutcome`, `SearchRun` and `run_search_thread` from `widget/_search_run.py`.
**Glue:** the `# --- Search` block holds `search`, `cancel_search`, `searching`, the origin and outcome helpers, and the `nova-search` thread start (registered with `join_on_close`).
`_DocumentSearchPlanner` adapts the document to the `SearchPlanner` protocol.
**New messages:** `SearchProgress`, `SearchFound`, `SearchNotFound`, `SearchCancelled` and `SearchFailed`.
**New class attributes:** `search_settings` and `search_clock`; new attribute `_search_run`.
**`_Jump.select_to`:** the jump gained `select_to`, `backward` and `wrapped`, and the property `is_search`.
`_drive_lazy` sends a jump with `select_to` to the new `_drive_search`, which selects the match exactly and posts `SearchFound`, and `_resolve_exact` waits for the long index.
`_resolve_byte` gained `allow_end` (a match may end at the document length).
`_drop_jump` posts the `SearchCancelled` of a dropped search placement, and `_abort_jump` posts `SearchFailed` for a search placement.
**New private types:** the frozen dataclasses `_Pending` (a byte that cannot be placed yet), `_Rejection` (a byte that cannot be placed at all) and `_LongRowTarget` (a byte inside a long row); `_resolve_byte` and `_resolve_exact` return them.
**New private methods:** `_end_search_placement` (ends a pending search placement with `SearchFailed`; `_fail_source` calls it) and `_after_text_change` (the one hook after every edit, undo, redo and roll back).
**`check_action`:** `cancel_pending` is also active while a search runs, and `action_cancel_pending` cancels the search first.
**Cancel hooks:** `_after_text_change` (every edit, undo, redo and roll back) cancels the search with the reason `text changed`; `_replace_document` cancels it with the reason `reloaded`; `close` calls `_abandon_search`.
**Key binding:** `select_all` is bound to `ctrl+shift+a,f8` instead of upstream's `f7` only (`ctrl+shift+a` is a new key), so that F7 opens the search in `nova_edit`; the binding table in the docstring says the same.

### `document/_edit.py` (ACT5)

**New members:** `contents()` lists every `Content` the edit holds, and `rewrite(contents)` replaces them in the same order after a rebase.
**New import:** `Iterator`.

### `document/_history.py` (ACT5)

**New names:** the class `Batch` (an edit list with `revision_before`, `revision_after`, `branch_before` and `branch`).
**New members:** `revision`, `branch`, `modified` and `mark_saved()`.
**Modified members:** `record` (revision and branch), `_pop_undo`, `_pop_redo`, `_restore_undo`, `_restore_redo` (move the revision), `clear` (resets the revision, the branch and the saved state), and the types of `undo_stack` and `redo_stack`.

### `document/_document.py`

**Import:** `cell_len` comes from `rich.cells`, and `advance_disp` and `locate_cover` from `core/text_width`.

**Capability methods added (default implementations; lazy documents override):**
- `is_long(row)` — return whether the row is never decoded as a whole.
- `line_length(row)` — return the character count or `None`.
- `row_byte_length(row)` — return the UTF-8 byte length.
- `column_slice(row, start, stop)` — return a substring.
- `has_char_at(row, column)` — return whether a character exists.
- `display_column(row, column, tab_width)` — return the display column.
- `column_at_display(row, x, tab_width)` — return the character column at a display position.
- `byte_offset(row, column)` — return the absolute byte offset.

### `document/_document_navigator.py`

**Imports:** `bisect` is no longer imported, `Protocol` and `runtime_checkable` are added, `cell_len` comes from `rich.cells`, and `LazyDocument` and `LazyWrappedDocument` are imported.

**New names:**
- `LazyOffsets` — protocol of lazily computed wrap offsets.
- `_SMART_HOME_WINDOW = 8192` — characters scanned by smart home when the row length is unknown.
- `_bisect_right(sequence, value)` — module helper that accepts `LazyOffsets`.
- `_line_length(row, fallback)`, `_long_wrapped(row)`, `_known_or_estimated_length(row)`, `end_is_known(row)`, `_is_section_start(wrapped, row, column)` — navigator helpers.

**Modified members (lazy-document branches):**
- `is_start_of_wrapped_line`, `is_end_of_document_line`, `is_end_of_wrapped_line`, `is_first_wrapped_line`, `is_last_wrapped_line`.
- `get_location_left`, `get_location_above`, `get_location_below`, `get_location_end`, `get_location_home`, `get_location_at_y_offset`, `clamp_reachable`.
- Module function `index` (lazy branch).

### `document/_edit.py`

**Import:** `Content` and `merge_pieces` from `core/pieces`, and `LazyDocument`.

**New fields:** `insert_content` (insert piece references byte for byte, used by paste of internal content), `start_byte`, `removed`, `inserted` and `end_location`.
**New members:** `coalesce(later)` merges adjacent insertions and adjacent backspace or delete runs, `characters` is the size the history counts for batching, and the module helpers `_has_escape` and `_join`.
**Modified members:** `do` splices piece references for a lazy document (redo uses `splice_bytes`) and records them; `undo` replaces the inserted span by the removed pieces without reading text.

### `document/_history.py`

**`__post_init__`:** besides the undo and redo stacks it initialises the ACT5 state: `_revision`, `_branch`, `_next_branch` and `_saved`; the undo stack holds `Batch` lists.
**Defaults:** `max_checkpoints` is `int | None = None` (keep every batch; upstream required it), and `checkpoint_timer` (2.0) and `checkpoint_max_characters` (100) have defaults.
**Modified member:** `record` decides replacement and newline from the piece lengths and break counts when the edit has pieces, and tries `Edit.coalesce` before it appends to the latest batch.
**New members:** `_restore_undo` and `_restore_redo`, which put back a batch that the widget refused.
**ACT5:** batches are `Batch` lists that store `revision_before`, `revision_after`, `branch_before` and `branch`; new members `revision`, `branch`, `mark_saved()` and `modified`; a record after an undo opens a new branch.
The batching rules are unchanged.

### `document/_document.py` (ACT4)

`EditResult` gained `removed`, `start_byte` and `inserted`, and `replaced_text` is empty above 64 KiB for a lazy document.

### `document/_lazy_wrapped_document.py` (ACT4)

`wrap_range(start, old_end, new_end)` is implemented: it drops the row caches and the measured blocks from the block of the start row.

## Additional Changes

### `_TREE_SITTER_PATH` Fix

- **Old:** `_TREE_SITTER_PATH = Path(__file__).parent / "../../tree-sitter/"`.
  This pointed to a non-existent path, so syntax highlighting was empty.
- **New:** `_TREE_SITTER_PATH = Path(textual.__file__).parent / "tree-sitter"`.
  It now uses the tree-sitter directory of the installed `textual` package.

### `soft_wrap` Default

- Changed from `True` to `False` in the constructor and in `code_editor()`.
- Rationale: starting without wrapping is simpler for large files, and lazy documents grid-wrap rows above the word-wrap limit anyway.
- Users toggle it with F4 in `nova_edit`.

### Remaining Private Textual Imports

None remain.
ACT7 vendored `textual._wrap`, `textual._cells.cell_width_to_column_index` and `textual._tree_sitter` as `document/_wrap.py` and `widget/_tree_sitter.py`.
No file in `src/nova_editor` or `src/tools` imports a private Textual module, `textual.document` or `textual.widgets._text_area`.
Re-derive this with `git grep -nE "^\s*(from|import) +textual\.(_|document|widgets\._text_area)" -- src/nova_editor src/tools` after every upgrade, which must print nothing.
The drift test is `tests/nova_editor/document/test_wrap_parity.py`.
After a Textual upgrade run `uv run pytest tests/nova_editor/document/test_wrap_parity.py -q`.
The test skips when upstream removes a module.
When it fails, diff the upstream file against the vendored one and merge the change.
Replace a vendored helper when Textual offers a public equivalent.

## Why Vendored?

The TextArea widget is vendored (copied) rather than subclassed because:

1. **Heavy modifications**: Large-file support, long-row handling and custom buffer management touch many internals.
2. **Minimize coupling**: Vendoring avoids tight coupling to Textual's internal APIs, which may change between versions.
3. **Control over performance**: We can optimize the implementation for Nova Navigator's use cases.
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
5. Test thoroughly using `uv run qa`, `uv run pytest tests/nova_editor/document/test_wrap_parity.py -q` (merge any drift into `document/_wrap.py` and `widget/_tree_sitter.py`) and manual testing.
6. Update the version reference at the top of this file.

## License

Textual is licensed under the MIT License.
See `LICENSE.textual` in this directory.
