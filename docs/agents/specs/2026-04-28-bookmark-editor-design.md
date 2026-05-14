# Bookmark Editor Dialog — Design Spec

## Overview

A `ManageBookmarksDialog` is added to allow users to create, edit, reorder, and delete bookmark groups and entries.
It is a full `ModalScreen` (same pattern as `CopyMoveFilesDialog`) opened via `Ctrl+Shift+B`.
All mutations are held in a working copy of `BookmarkConfig`; they are only persisted when the user presses OK.

---

## Layout

```
┌─ Edit Bookmarks ──────────────────────────────────┐
│ ┌─ Tree ──────────────────────────────────────┐   │
│ │  ▼ Computer                                 │   │
│ │      Home  ~/                               │   │
│ │      Documents  ~/Documents                 │   │
│ │  ▼ Bookmarks                                │   │
│ └─────────────────────────────────────────────┘   │
│  [Add Group] [Add Entry] [Remove]                  │
│  [Move Up ↑] [Move Down ↓] [Move to Group…]        │
│ ┌─ Edit ──────────────────────────────────────┐   │
│ │  Name: [______________]  Icon: [__________] │   │
│ │  Path: [______________________________]     │   │
│ └─────────────────────────────────────────────┘   │
│                            [Cancel]      [OK]     │
└───────────────────────────────────────────────────┘
```

The Path row is hidden when a group node is selected.
All form fields are disabled when nothing is selected.

---

## Files Changed

| File | Change |
|---|---|
| `src/nova_navigator/dialogs/manage_bookmarks_dialog.py` | New: `ManageBookmarksDialog`, `MoveToGroupDialog` |
| `src/nova_navigator/dialogs/__init__.py` | Export `ManageBookmarksDialog` |
| `src/nova_navigator/main.py` | Add binding `Ctrl+Shift+B`, action, menu entry |
| `tests/widgets/test_manage_bookmarks_dialog.py` | New: all tests |

---

## State Management

On open, the dialog deep-copies `conf_.bookmarks` into a `_working: BookmarkConfig`.
All mutations (add, remove, reorder, form edits) operate on `_working`.
On OK: `conf_.bookmarks.groups = _working.groups; conf_.bookmarks.save()`.
On Cancel or Escape: the working copy is discarded; `conf_.bookmarks` is unchanged.

---

## Tree

Built from `_working` on mount and fully rebuilt after every mutation via a `_rebuild_tree()` helper.
Each node carries a tag of type `tuple[str, ...]`:

- `("group", group_index: int)` for a group node
- `("entry", group_index: int, entry_index: int)` for a bookmark entry node

Using integer indices rather than object references keeps node tags valid across rebuilds.
After a rebuild, the previously-selected node is re-selected by matching its tag.

---

## Form Behaviour

When the tree selection changes:

- Group selected → Name + Icon inputs shown and populated; Path input hidden.
- Entry selected → Name, Icon, Path inputs shown and populated.
- Nothing selected → all inputs cleared and disabled.

Every `Input.Changed` event syncs the value back into `_working` immediately so the working copy stays consistent at all times.

---

## Action Buttons

| Button | Keyboard | Enabled when |
|---|---|---|
| Add Group | — | always |
| Add Entry | `Insert` | ≥ 1 group exists |
| Remove | `Delete` / `F8` | something is selected |
| Move Up ↑ | `Alt+Up` | selected item is not first in its list |
| Move Down ↓ | `Alt+Down` | selected item is not last in its list |
| Move to Group… | — | an entry is selected and ≥ 2 groups exist |

"Add Group" creates a new `Group(name="New Group")` appended to `_working.groups` and selects its node.
"Add Entry" creates a new `Bookmark(name="New Entry")` appended to the selected group's (or currently selected entry's parent group's) bookmark list and selects its node.
"Remove" splices the selected item out of `_working`.
"Move Up / Down" swaps adjacent items in the relevant list and reselects the moved item.

---

## Move to Group Sub-dialog

`MoveToGroupDialog` is a minimal `Dialog(ModalScreen)` defined in the same file as `ManageBookmarksDialog`.
It receives the list of candidate group names (excluding the entry's current group) and returns the index of the chosen group within `_working.groups`.
It contains a `ListView` of group names.
Confirmed with Enter; cancelled with Escape.

When confirmed, the entry is removed from its current group and appended to the chosen group.
The tree is rebuilt and the moved entry is reselected.

---

## Menu & Key Binding Integration (`main.py`)

```python
Binding("ctrl+shift+b", "edit_bookmarks", "Edit Bookmarks")
```

A new `async def _action_edit_bookmarks(self) -> None` pushes `ManageBookmarksDialog` onto the screen stack via `await self.app.push_screen_wait(...)`.
After the modal returns, `conf_.bookmarks` is already updated (the dialog handles the save).

A new "Edit Bookmarks…" menu entry is added to the existing Commands menu, alongside the "Bookmarks" entry.

---

## Validation

- "Add Entry" is a no-op (disabled) when no groups exist.
- Name fields are not validated; empty names are allowed.
- Path is not validated at edit time; variable substitution (e.g. `$HOME`) and filesystem checks happen at navigation time as they do today.
- Removing a non-empty group requires no confirmation prompt; the whole dialog can be cancelled to undo.

---

## Testing

Tests live in `tests/widgets/test_manage_bookmarks_dialog.py`.
They use `App.run_test()` with a minimal wrapper `App`, patching `conf_.bookmarks` with a known fixture config.

| Test | What it checks |
|---|---|
| `test_add_group` | Add a group → appears in tree and `_working.groups` |
| `test_add_entry` | Add an entry to a group → appears in tree and group's bookmark list |
| `test_remove_entry` | Select entry, remove → gone from tree and working copy |
| `test_remove_group` | Select group, remove → group and all its children gone |
| `test_move_up` | Add two entries, move the second up → order inverted |
| `test_move_down` | Add two entries, move the first down → order inverted |
| `test_move_to_group` | Entry in group A moved to group B → correct parent in working copy |
| `test_ok_saves` | Press OK → `conf_.bookmarks.save()` called, `conf_.bookmarks.groups` updated |
| `test_cancel_discards` | Make edits, press Cancel → `conf_.bookmarks` unchanged |
| `test_form_hidden_path_for_group` | Select a group node → Path input not visible |
| `test_form_shows_path_for_entry` | Select an entry node → Path input visible |
