# Directory Browser

`DirectoryBrowser` is the main file-listing widget used in the dual-pane layout.
It extends Textual's `ScrollView` and renders a directory's contents as a scrollable, sortable, filterable list.

## Columns

Each row displays four columns defined in `_columns: list[Column]`.

| Index | Title | Fixed width | Sorter |
|-------|-------|-------------|--------|
| 0 | *(empty — icon)* | 3 | `column_sorter_name` |
| 1 | Name | dynamic | `column_sorter_name` |
| 2 | Size | 10 | `column_sorter_size` |
| 3 | Modified | 12 | `column_sorter_modified` |

The Name column (index 1) has `width=0` in the `Column` dataclass.
Its actual render width is computed per frame as `widget_width − 30`, where 30 accounts for the icon column, size, modified, padding, and scrollbar.

Column 0 (icon) and column 1 (name) both embed `{"column": 1}` in their Segment metadata (see [Hit-testing](#hit-testing)).
Clicking either area sorts by `column_sorter_name`.
Clicking the same column header again reverses the sort direction.

### Column formatters

`column_formatter_icon` resolves the icon in this order:

1. Start with `ico_("folder")` for directories, or the config filetype icon (falling back to `ico_("file")`) for regular files.
2. If the file has no detectable MIME type and is executable, override with `ico_("executable")`.
3. Append `~` for symlinks (or space otherwise).
4. If the path is a broken symlink, replace the whole string with `ico_("broken link") + "!"`.

`column_formatter_size` returns `"-"` for directories and a decimal-magnitude abbreviation (`K`, `M`, `G`, `T`, `P`) for files.

`column_formatter_modified` formats timestamps as:
`Today HH:MM` / `Mon DD HH:MM` (same year) / `Mon DD YYYY` (other years).

### Column sorters

Each sorter returns a tuple `(group, value)` so that primary grouping is always stable before secondary ordering.

`column_sorter_name` groups: `..` (0) → hidden dirs (1) → visible dirs (2) → hidden files (3) → visible files (4).

## Keyboard Bindings

| Key | Action |
|-----|--------|
| `Enter` | Select item under cursor (navigate into directory or post `PathSelected`) |
| `Up` / `Down` | Move cursor one row |
| `Page Up` / `Page Down` | Move cursor one page |
| `Home` / `End` | Jump to first / last item |
| `Insert` | Toggle selection on cursor item and advance cursor |
| `Ctrl+A` | Select all visible items |
| `Ctrl+F` | Open the filter bar |
| `Left` / `Right` | *Bound but unimplemented* — `action_cursor_left` / `action_cursor_right` do not exist |

## Mouse Interaction

- **Click** — move the cursor to the clicked row.
- **Double-click** — navigate into a directory or post `PathSelected` for a file.
- **Right-click** — post `ContextMenu` with the clicked item (or `None` for an empty area).
- **Ctrl+click** — toggle the clicked item's selection.
- **Ctrl+drag** — add dragged-over items to the selection.
- **Scroll wheel** — page up or down (full page, same as `Page Up`/`Page Down`).

## Hit-testing

`render_line` embeds Rich `Style` metadata in every `Segment`:

- `{"row": row}` — the data row index (−1 for the header row).
- `{"column": col}` — the column index (1, 2, or 3; icon and name both use 1).

Mouse event handlers recover these values via `event.style.meta["row"]` and `event.style.meta["column"]`.
Any click outside a cell (no `row`/`column` key in metadata) is treated as an empty-area click.
This is the only mechanism connecting the rendered output to mouse interactions, so any changes to `render_line` must preserve this metadata.

## Internal State

```
_path          — current directory VPath
_all_items     — raw list from _path.iterdir(), no UpPath, unsorted copy after sort
_shown_items   — UpPath prepended (if not root) + filtered subset of _all_items
_selected_items — set of explicitly selected VPaths (subset of _shown_items)
```

`UpPath` is prepended to `_shown_items` when `_path.parent != _path`.
VFS implementations signal "this is a root" by returning `self` from `parent`.

## Public Properties

```python
path: VPath                      # current directory
path_item_under_cursor: VPath    # _shown_items[cursor_row]
selected_path_items: list[VPath] # effective selection used by operations
```

`selected_path_items` returns:
- The explicit selection set when non-empty.
- `[path_item_under_cursor]` when the set is empty and cursor is not on `UpPath`.
- `[]` when the set is empty and cursor is on `UpPath`.

## Selection

The browser maintains `_selected_items: set[VPath]`.
`Insert` calls `action_insert_select` which toggles the cursor item and advances the cursor.
`UpPath` is never added to `_selected_items`.

Selection helpers:

- `action_select_all` — all visible non-UpPath items.
- `action_select_none` — clear.
- `action_invert_selection` — flip.

## Filtering

Pressing `Ctrl+F` focuses `_filter_widget`, a `FilterWidget` overlay mounted in `on_mount`.
`FilterWidget` calls `browser.on_filter_widget_input_changed(event)` directly (not via a Textual message) whenever the input changes.
`DirectoryBrowser.on_filter_widget_input_changed` calls `update(WhatChanged.FILTERING)`.

Filtering hides any item whose name does not contain the filter string (case-insensitive substring match).
`Escape` clears the filter and hides the overlay; submitting a non-empty value returns focus to the browser without closing.

`show_hidden_files: Reactive[bool]` (default `False`, `repaint=False`, `always_update=False`) controls dotfile visibility independently of filtering.
Changing it triggers `watch_show_hidden_files` → `update(WhatChanged.FILTERING)`.

## Reactives

| Reactive | Type | Default | Notes |
|----------|------|---------|-------|
| `show_hidden_files` | `Reactive[bool]` | `False` | `repaint=False`, `always_update=False` |
| `cursor_row` | `Reactive[int]` | `0` | `repaint=False`, `always_update=True` |
| `sort_column` | `var` | `0` | triggers `update(SORTING)` |
| `sort_ascending` | `var` | `True` | triggers `update(SORTING)` |

`cursor_row` uses `always_update=True` so `watch_cursor_row` fires even when the value does not change.
It is gated by `old_row != new_row` internally to avoid needless refreshes.
`validate_cursor_row` clamps the value to `[0, len(_shown_items) − 1]`.

## Styling

`COMPONENT_CLASSES` defines the CSS component class names available for theming via TCSS:

| Class | Applied when |
|-------|-------------|
| `cursor` | Row under the cursor |
| `highlight-directory` | Item is a directory |
| `highlight-hidden` | Item is hidden (dotfile) |
| `highlight-executable` | Item is executable and not a directory |
| `highlight-symlink` | Item is a symlink |
| `highlight-broken-symlink` | Item is a broken symlink |
| `highlight-up` | *(defined but not applied in `_highlight_style`)* |
| `highlight-selected` | Item is in `_selected_items` |

`_highlight_style` applies two independent layers per row:

1. Per-filename foreground/background from `conf_.filetypes.get_colors_for_filename()`.
2. Component class styles for file type and selection state.

On cursor rows, `render_line` captures `style.bgcolor` before applying item styles and then restores it as a `force_bgcolor` override so the cursor background is never overridden by file-type colors.

## Messages

`DirectoryBrowser` posts these Textual messages:

| Message | When posted | Key attributes |
|---------|-------------|----------------|
| `PathSelected` | User activates an item | `browser`, `path` |
| `ItemChanged` | Cursor row changes | `browser`, `path` |
| `ContextMenu` | Right-click on item or empty area | `browser`, `path` (may be `None`) |
| `Focus` | Widget receives keyboard focus | `browser` |

`DirectoryBrowser` also handles its own `PathSelected` in `_on_directory_browser_path_selected`.
If `path.stat.is_directory` is true it calls `set_path(path)` to navigate automatically.

## Filesystem Watching

For local paths, `set_path` registers a `watchdog` observer on the directory.
`_FileSystemEventHandler` listens only for `DirModifiedEvent` and debounces with a 200 ms `threading.Timer`.
The debounce uses `asyncio.call_soon_threadsafe` to schedule a coroutine on the main event loop.
When navigating away from a local path, the previous watch is unscheduled.
SSH and archive paths are not watched.

The `watchdog.observers.Observer` is started in `__init__` and never stopped.
This is a known resource leak — there is currently no `on_unmount` cleanup.

## `set_path`

```python
browser.set_path(path: VPath) -> None
```

Navigates to a new directory.
If `path` equals `_path.parent` (navigating up), the cursor tries to land on the subdirectory just exited by name.
Otherwise the cursor resets to row 0.
No-ops if `path` equals `_path`.

## `update`

```python
browser.update(what_changed: WhatChanged) -> None
```

Refreshes the item list.
`WhatChanged` is an `int` enum; stages cascade using `<=`:

| Value | Re-reads VFS | Re-sorts | Re-filters |
|-------|:---:|:---:|:---:|
| `ALL` (0) | yes | yes | yes |
| `SORTING` (1) | no | yes | yes |
| `FILTERING` (2) | no | no | yes |

After each update, cursor position and `_selected_items` are restored by identity match against the new `_shown_items`.

## `UpPath`

`UpPath` is a sentinel `VPath` subclass representing `..`.
It is instantiated once as the module-level constant `UP_PATH`.
`UpPath.parent` returns `self`, and `UpPath.__eq__` matches any other `UpPath`.
`stat` reports `is_directory=True` and all other fields at their defaults.
Code that must skip the `..` entry checks `isinstance(item, UpPath)`.
