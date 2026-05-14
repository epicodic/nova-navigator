# Bookmark Editor Dialog Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use skills:subagent-driven-development (recommended) or skills:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Add a `ManageBookmarksDialog` modal that lets users create, edit, reorder, and delete bookmark groups and entries, triggered via `Ctrl+Shift+B`.

**Architecture:** `ManageBookmarksDialog` extends `ModalScreen[None]` directly (not the `Dialog` base class, which assumes a single button row and returns `str`). It has three stacked areas: a `Tree` widget, an action button row, and a form panel below. All mutations operate on a deep-copied working `BookmarkConfig`; changes persist to disk only on OK. `MoveToGroupDialog` extends `ModalScreen[int | None]` and handles moving an entry between groups.

**Tech Stack:** Python 3.12, Textual, pytest, pytest-asyncio

**Coding Conventions:** `docs/coding_conventions.md` — read before implementing

**Spec:** `docs/agents/specs/2026-04-28-bookmark-editor-design.md`

---

## File Map

| File | Action | Responsibility |
|---|---|---|
| `src/nova_navigator/dialogs/manage_bookmarks_dialog.py` | Create | `ManageBookmarksDialog`, `MoveToGroupDialog` |
| `src/nova_navigator/dialogs/__init__.py` | Modify | Export `ManageBookmarksDialog` |
| `src/nova_navigator/main.py` | Modify | Binding, action, menu entry |
| `tests/widgets/test_manage_bookmarks_dialog.py` | Create | All tests |

---

## Task 1: `MoveToGroupDialog` — sub-dialog for moving entries between groups

**Files:**
- Create: `src/nova_navigator/dialogs/manage_bookmarks_dialog.py`
- Test: `tests/widgets/test_manage_bookmarks_dialog.py`

---

- [ ] **Step 1.1: Write the failing test**

```python
# tests/widgets/test_manage_bookmarks_dialog.py
from __future__ import annotations

import pytest
from textual.app import App, ComposeResult

from nova_navigator.dialogs.manage_bookmarks_dialog import MoveToGroupDialog


class _MoveToGroupApp(App[int | None]):
    def compose(self) -> ComposeResult:
        return iter([])

    async def action_open(self) -> None:
        result = await self.push_screen_wait(
            MoveToGroupDialog(group_names=["Work", "Personal"])
        )
        self.exit(result)


@pytest.mark.asyncio
async def test_move_to_group_dialog_lists_groups() -> None:
    app = _MoveToGroupApp()
    async with app.run_test() as pilot:
        await app.run_action("open")
        await pilot.pause()
        from textual.widgets import ListView
        items = list(app.query(ListView))
        assert len(items) == 1
        assert items[0].item_count == 2
```

- [ ] **Step 1.2: Run test to verify it fails**

```
uv run pytest tests/widgets/test_manage_bookmarks_dialog.py::test_move_to_group_dialog_lists_groups -v
```
Expected: FAIL — `manage_bookmarks_dialog` module does not exist yet.

- [ ] **Step 1.3: Implement `MoveToGroupDialog`**

Create `src/nova_navigator/dialogs/manage_bookmarks_dialog.py`:

```python
"""Bookmark editor dialog."""

from __future__ import annotations

from typing import ClassVar

from textual.app import ComposeResult
from textual.binding import Binding, BindingType
from textual.containers import Horizontal, Vertical
from textual.screen import ModalScreen
from textual.widgets import Button, Label, ListItem, ListView


class MoveToGroupDialog(ModalScreen[int | None]):
    """Minimal modal listing candidate groups; returns the chosen index into _working.groups."""

    DEFAULT_CSS = """
    MoveToGroupDialog {
        align: center middle;

        #mtg_box {
            border: round $accent;
            width: 40;
            height: auto;
            padding: 1;
        }

        #mtg_buttons {
            height: auto;
            align-horizontal: center;
            margin-top: 1;
        }

        #mtg_buttons Button {
            width: auto;
            margin: 0 1;
        }
    }
    """

    BINDINGS: ClassVar[list[BindingType]] = [
        Binding("escape", "cancel", "Cancel", priority=True),
    ]

    _group_names: list[str]
    _list_view: ListView

    def __init__(self, group_names: list[str]) -> None:
        super().__init__()
        self._group_names = group_names

    def compose(self) -> ComposeResult:
        self._list_view = ListView(
            *[ListItem(Label(name)) for name in self._group_names],
            id="group_list",
        )
        with Vertical(id="mtg_box"):
            yield Label("Move to Group")
            yield self._list_view
            with Horizontal(id="mtg_buttons"):
                yield Button("Cancel", id="mtg_cancel", variant="default")
                yield Button("OK", id="mtg_ok", variant="primary")

    def on_button_pressed(self, event: Button.Pressed) -> None:
        if event.button.id == "mtg_ok":
            self.dismiss(self._list_view.index)
        else:
            self.dismiss(None)

    def action_cancel(self) -> None:
        self.dismiss(None)
```

- [ ] **Step 1.4: Run test to verify it passes**

```
uv run pytest tests/widgets/test_manage_bookmarks_dialog.py::test_move_to_group_dialog_lists_groups -v
```
Expected: PASS

- [ ] **Step 1.5: Coding-guideline follow-up checklist**

- [ ] `docs/coding_conventions.md` read
- [ ] All symbols use `snake_case` / `UpperCamelCase` as required
- [ ] All functions/methods have full type annotations
- [ ] `uv run pytest tests/widgets/test_manage_bookmarks_dialog.py -v` passes
- [ ] `uv run ruff check src/nova_navigator/dialogs/manage_bookmarks_dialog.py` passes

---

## Task 2: `ManageBookmarksDialog` — skeleton, tree rendering, and form

**Files:**
- Modify: `src/nova_navigator/dialogs/manage_bookmarks_dialog.py`
- Test: `tests/widgets/test_manage_bookmarks_dialog.py`

---

- [ ] **Step 2.1: Write the failing tests**

Add to `tests/widgets/test_manage_bookmarks_dialog.py`:

```python
import copy

from nova_navigator.config.bookmarks import Bookmark, BookmarkConfig, Group
from nova_navigator.dialogs.manage_bookmarks_dialog import ManageBookmarksDialog
from textual.app import App, ComposeResult
from textual.widgets import Tree, Input


def _fixture_config() -> BookmarkConfig:
    return BookmarkConfig(
        groups=[
            Group(
                name="Computer",
                icon=None,
                bookmarks=[
                    Bookmark(name="Home", path="/home/user", icon=None),
                    Bookmark(name="Docs", path="/home/user/Documents", icon=None),
                ],
            ),
            Group(name="Work", icon=None, bookmarks=[]),
        ]
    )


class _EditorApp(App[None]):
    def __init__(self, config: BookmarkConfig) -> None:
        super().__init__()
        self._config = config

    def compose(self) -> ComposeResult:
        return iter([])

    async def action_open(self) -> None:
        await self.push_screen_wait(ManageBookmarksDialog(self._config))


@pytest.mark.asyncio
async def test_tree_shows_groups_and_entries() -> None:
    cfg = _fixture_config()
    dialog = ManageBookmarksDialog(cfg)

    class _App(App[None]):
        def compose(self) -> ComposeResult:
            return iter([])

        async def on_mount(self) -> None:
            await self.push_screen(dialog)

    app = _App()
    async with app.run_test() as pilot:
        await pilot.pause()
        tree = app.query_one(Tree)
        # root has 2 group children
        assert len(list(tree.root.children)) == 2
        # first group has 2 entry children
        first_group = list(tree.root.children)[0]
        assert len(list(first_group.children)) == 2


@pytest.mark.asyncio
async def test_form_path_hidden_for_group_selection() -> None:
    cfg = _fixture_config()
    dialog = ManageBookmarksDialog(cfg)

    class _App(App[None]):
        def compose(self) -> ComposeResult:
            return iter([])

        async def on_mount(self) -> None:
            await self.push_screen(dialog)

    app = _App()
    async with app.run_test() as pilot:
        await pilot.pause()
        tree = app.query_one(Tree)
        # select the first group node
        tree.root.children[0].select()  # type: ignore[union-attr]
        await pilot.pause()
        path_input = app.query_one("#input_path", Input)
        assert not path_input.display


@pytest.mark.asyncio
async def test_form_path_visible_for_entry_selection() -> None:
    cfg = _fixture_config()
    dialog = ManageBookmarksDialog(cfg)

    class _App(App[None]):
        def compose(self) -> ComposeResult:
            return iter([])

        async def on_mount(self) -> None:
            await self.push_screen(dialog)

    app = _App()
    async with app.run_test() as pilot:
        await pilot.pause()
        tree = app.query_one(Tree)
        # select first entry of first group
        list(list(tree.root.children)[0].children)[0].select()  # type: ignore[union-attr]
        await pilot.pause()
        path_input = app.query_one("#input_path", Input)
        assert path_input.display
```

- [ ] **Step 2.2: Run tests to verify they fail**

```
uv run pytest tests/widgets/test_manage_bookmarks_dialog.py::test_tree_shows_groups_and_entries tests/widgets/test_manage_bookmarks_dialog.py::test_form_path_hidden_for_group_selection tests/widgets/test_manage_bookmarks_dialog.py::test_form_path_visible_for_entry_selection -v
```
Expected: FAIL — `ManageBookmarksDialog` not yet defined.

- [ ] **Step 2.3: Implement `ManageBookmarksDialog` skeleton, tree, and form**

Add to `src/nova_navigator/dialogs/manage_bookmarks_dialog.py` (append after `MoveToGroupDialog`):

```python
import copy
from typing import ClassVar

from textual import on
from textual.app import ComposeResult
from textual.binding import Binding, BindingType
from textual.containers import Horizontal, Vertical
from textual.widgets import Button, Input, Label, Tree
from textual.widgets.tree import TreeNode

from nova_navigator.config.bookmarks import Bookmark, BookmarkConfig, Group
from nova_navigator.icons import ICONS

# Tag types stored in tree node data
type _GroupTag = tuple[str, int]          # ("group", group_index)
type _EntryTag = tuple[str, int, int]     # ("entry", group_index, entry_index)
type _NodeTag = _GroupTag | _EntryTag


class ManageBookmarksDialog(ModalScreen[None]):
    """Full-screen modal for editing bookmark groups and entries."""

    DEFAULT_CSS = """
    ManageBookmarksDialog {
        align: center middle;

        #editor_box {
            border: round $accent;
            width: 70;
            height: 30;
            padding: 1;
        }

        #tree_container {
            height: 1fr;
            border: inner $surface;
        }

        #action_row {
            height: auto;
            margin-top: 1;
        }

        #action_row Button {
            width: auto;
            margin-right: 1;
        }

        #form_container {
            height: auto;
            margin-top: 1;
            border: inner $surface;
            padding: 0 1;
        }

        #form_row_name {
            height: auto;
        }

        #form_row_path {
            height: auto;
        }

        #ok_cancel_row {
            height: auto;
            align-horizontal: right;
            margin-top: 1;
        }

        #ok_cancel_row Button {
            width: auto;
            margin-left: 1;
        }
    }
    """

    BINDINGS: ClassVar[list[BindingType]] = [
        Binding("escape", "cancel", "Cancel", priority=True),
        Binding("alt+up", "move_up", "Move Up", show=False),
        Binding("alt+down", "move_down", "Move Down", show=False),
        Binding("delete", "remove_item", "Remove", show=False),
        Binding("f8", "remove_item", "Remove", show=False),
    ]

    _working: BookmarkConfig
    _tree: Tree[_NodeTag]
    _input_name: Input
    _input_icon: Input
    _input_path: Input

    def __init__(self, config: BookmarkConfig) -> None:
        super().__init__()
        self._working = copy.deepcopy(config)

    # ------------------------------------------------------------------ compose

    def compose(self) -> ComposeResult:
        self._tree = Tree("Bookmarks", id="bookmark_tree")
        self._tree.show_root = False

        self._input_name = Input(placeholder="Name", id="input_name")
        self._input_icon = Input(placeholder="Icon", id="input_icon")
        self._input_path = Input(placeholder="Path", id="input_path")

        with Vertical(id="editor_box"):
            yield Label("Edit Bookmarks", id="editor_title")
            with Vertical(id="tree_container"):
                yield self._tree
            with Horizontal(id="action_row"):
                yield Button("Add Group", id="btn_add_group")
                yield Button("Add Entry", id="btn_add_entry", disabled=True)
                yield Button("Remove", id="btn_remove", disabled=True)
                yield Button("↑ Move Up", id="btn_move_up", disabled=True)
                yield Button("↓ Move Down", id="btn_move_down", disabled=True)
                yield Button("Move to Group…", id="btn_move_to_group", disabled=True)
            with Vertical(id="form_container"):
                with Horizontal(id="form_row_name"):
                    yield Label("Name: ")
                    yield self._input_name
                    yield Label("  Icon: ")
                    yield self._input_icon
                with Horizontal(id="form_row_path"):
                    yield Label("Path: ")
                    yield self._input_path
            with Horizontal(id="ok_cancel_row"):
                yield Button("Cancel", id="btn_cancel", variant="default")
                yield Button("OK", id="btn_ok", variant="primary")

    def on_mount(self) -> None:
        self._rebuild_tree(select_tag=None)
        self._sync_form_to_selection(None)
        self._tree.focus()

    # ------------------------------------------------------------------ tree

    def _rebuild_tree(self, select_tag: _NodeTag | None) -> None:
        self._tree.clear()
        for gi, group in enumerate(self._working.groups):
            icon = ICONS.get_icon(group.icon) + " " if group.icon else ""
            group_node = self._tree.root.add(
                f"{icon}{group.name}",
                data=("group", gi),
                expand=True,
            )
            for ei, entry in enumerate(group.bookmarks):
                eicon = ICONS.get_icon(entry.icon) + " " if entry.icon else ""
                group_node.add_leaf(
                    f"{eicon}{entry.name}  {entry.path}",
                    data=("entry", gi, ei),
                )

        if select_tag is not None:
            self._select_node_by_tag(select_tag)

    def _select_node_by_tag(self, tag: _NodeTag) -> None:
        for node in self._tree.root.children:
            if node.data == tag:
                self._tree.select_node(node)
                return
            for child in node.children:
                if child.data == tag:
                    self._tree.select_node(child)
                    return

    def _selected_tag(self) -> _NodeTag | None:
        node = self._tree.cursor_node
        if node is None or node.data is None:
            return None
        return node.data  # type: ignore[return-value]

    # ------------------------------------------------------------------ form sync

    def _sync_form_to_selection(self, tag: _NodeTag | None) -> None:
        if tag is None:
            self._input_name.value = ""
            self._input_icon.value = ""
            self._input_path.value = ""
            self._input_name.disabled = True
            self._input_icon.disabled = True
            self._input_path.disabled = True
            self._input_path.display = True
            return

        self._input_name.disabled = False
        self._input_icon.disabled = False

        if tag[0] == "group":
            _, gi = tag  # type: ignore[misc]
            group = self._working.groups[gi]
            self._input_name.value = group.name
            self._input_icon.value = group.icon or ""
            self._input_path.value = ""
            self._input_path.display = False
        else:
            _, gi, ei = tag  # type: ignore[misc]
            entry = self._working.groups[gi].bookmarks[ei]
            self._input_name.value = entry.name
            self._input_icon.value = entry.icon or ""
            self._input_path.value = entry.path
            self._input_path.display = True
            self._input_path.disabled = False

    def _update_button_states(self, tag: _NodeTag | None) -> None:
        has_groups = len(self._working.groups) > 0
        something_selected = tag is not None

        self.query_one("#btn_add_entry", Button).disabled = not has_groups
        self.query_one("#btn_remove", Button).disabled = not something_selected
        self.query_one("#btn_move_up", Button).disabled = not self._can_move_up(tag)
        self.query_one("#btn_move_down", Button).disabled = not self._can_move_down(tag)
        self.query_one("#btn_move_to_group", Button).disabled = not (
            something_selected
            and tag is not None
            and tag[0] == "entry"
            and len(self._working.groups) >= 2
        )

    def _can_move_up(self, tag: _NodeTag | None) -> bool:
        if tag is None:
            return False
        if tag[0] == "group":
            return tag[1] > 0
        else:
            _, gi, ei = tag  # type: ignore[misc]
            return ei > 0

    def _can_move_down(self, tag: _NodeTag | None) -> bool:
        if tag is None:
            return False
        if tag[0] == "group":
            return tag[1] < len(self._working.groups) - 1
        else:
            _, gi, ei = tag  # type: ignore[misc]
            return ei < len(self._working.groups[gi].bookmarks) - 1

    # ------------------------------------------------------------------ event handlers

    def on_tree_node_highlighted(self, event: Tree.NodeHighlighted[_NodeTag]) -> None:
        tag = event.node.data
        self._sync_form_to_selection(tag)
        self._update_button_states(tag)

    @on(Input.Changed, "#input_name")
    def _on_name_changed(self, event: Input.Changed) -> None:
        tag = self._selected_tag()
        if tag is None:
            return
        if tag[0] == "group":
            self._working.groups[tag[1]].name = event.value
        else:
            self._working.groups[tag[1]].bookmarks[tag[2]].name = event.value  # type: ignore[misc]

    @on(Input.Changed, "#input_icon")
    def _on_icon_changed(self, event: Input.Changed) -> None:
        tag = self._selected_tag()
        if tag is None:
            return
        icon_val: str | None = event.value if event.value else None
        if tag[0] == "group":
            self._working.groups[tag[1]].icon = icon_val
        else:
            self._working.groups[tag[1]].bookmarks[tag[2]].icon = icon_val  # type: ignore[misc]

    @on(Input.Changed, "#input_path")
    def _on_path_changed(self, event: Input.Changed) -> None:
        tag = self._selected_tag()
        if tag is None or tag[0] != "entry":
            return
        self._working.groups[tag[1]].bookmarks[tag[2]].path = event.value  # type: ignore[misc]

    # ------------------------------------------------------------------ button actions

    def on_button_pressed(self, event: Button.Pressed) -> None:
        match event.button.id:
            case "btn_ok":
                self._action_ok()
            case "btn_cancel":
                self.dismiss(None)
            case "btn_add_group":
                self.action_add_group()
            case "btn_add_entry":
                self.action_add_entry()
            case "btn_remove":
                self.action_remove_item()
            case "btn_move_up":
                self.action_move_up()
            case "btn_move_down":
                self.action_move_down()
            case "btn_move_to_group":
                self._run_move_to_group()

    def _action_ok(self) -> None:
        from nova_navigator.config import conf_
        conf_.bookmarks.groups = self._working.groups
        conf_.bookmarks.save()
        self.dismiss(None)

    def action_cancel(self) -> None:
        self.dismiss(None)

    def action_add_group(self) -> None:
        new_group = Group(name="New Group")
        self._working.groups.append(new_group)
        new_tag: _NodeTag = ("group", len(self._working.groups) - 1)
        self._rebuild_tree(select_tag=new_tag)
        self._sync_form_to_selection(new_tag)
        self._update_button_states(new_tag)

    def action_add_entry(self) -> None:
        tag = self._selected_tag()
        if tag is None:
            gi = 0
        elif tag[0] == "group":
            gi = tag[1]
        else:
            gi = tag[1]
        new_entry = Bookmark(name="New Entry")
        self._working.groups[gi].bookmarks.append(new_entry)
        new_tag: _NodeTag = ("entry", gi, len(self._working.groups[gi].bookmarks) - 1)
        self._rebuild_tree(select_tag=new_tag)
        self._sync_form_to_selection(new_tag)
        self._update_button_states(new_tag)

    def action_remove_item(self) -> None:
        tag = self._selected_tag()
        if tag is None:
            return
        if tag[0] == "group":
            del self._working.groups[tag[1]]
        else:
            del self._working.groups[tag[1]].bookmarks[tag[2]]  # type: ignore[misc]
        self._rebuild_tree(select_tag=None)
        self._sync_form_to_selection(None)
        self._update_button_states(None)

    def action_move_up(self) -> None:
        tag = self._selected_tag()
        if tag is None or not self._can_move_up(tag):
            return
        if tag[0] == "group":
            gi = tag[1]
            lst = self._working.groups
            lst[gi - 1], lst[gi] = lst[gi], lst[gi - 1]
            new_tag: _NodeTag = ("group", gi - 1)
        else:
            gi, ei = tag[1], tag[2]  # type: ignore[misc]
            lst2 = self._working.groups[gi].bookmarks
            lst2[ei - 1], lst2[ei] = lst2[ei], lst2[ei - 1]
            new_tag = ("entry", gi, ei - 1)
        self._rebuild_tree(select_tag=new_tag)
        self._sync_form_to_selection(new_tag)
        self._update_button_states(new_tag)

    def action_move_down(self) -> None:
        tag = self._selected_tag()
        if tag is None or not self._can_move_down(tag):
            return
        if tag[0] == "group":
            gi = tag[1]
            lst = self._working.groups
            lst[gi + 1], lst[gi] = lst[gi], lst[gi + 1]
            new_tag: _NodeTag = ("group", gi + 1)
        else:
            gi, ei = tag[1], tag[2]  # type: ignore[misc]
            lst2 = self._working.groups[gi].bookmarks
            lst2[ei + 1], lst2[ei] = lst2[ei], lst2[ei + 1]
            new_tag = ("entry", gi, ei + 1)
        self._rebuild_tree(select_tag=new_tag)
        self._sync_form_to_selection(new_tag)
        self._update_button_states(new_tag)

    def _run_move_to_group(self) -> None:
        tag = self._selected_tag()
        if tag is None or tag[0] != "entry":
            return
        gi = tag[1]
        candidate_names = [
            g.name for idx, g in enumerate(self._working.groups) if idx != gi
        ]
        candidate_indices = [
            idx for idx in range(len(self._working.groups)) if idx != gi
        ]
        self.app.push_screen(
            MoveToGroupDialog(group_names=candidate_names),
            callback=lambda result: self._on_move_to_group_result(result, tag, candidate_indices),
        )

    def _on_move_to_group_result(
        self,
        result: int | None,
        tag: _NodeTag,
        candidate_indices: list[int],
    ) -> None:
        if result is None:
            return
        gi, ei = tag[1], tag[2]  # type: ignore[misc]
        entry = self._working.groups[gi].bookmarks.pop(ei)
        target_gi = candidate_indices[result]
        self._working.groups[target_gi].bookmarks.append(entry)
        new_tag: _NodeTag = (
            "entry",
            target_gi,
            len(self._working.groups[target_gi].bookmarks) - 1,
        )
        self._rebuild_tree(select_tag=new_tag)
        self._sync_form_to_selection(new_tag)
        self._update_button_states(new_tag)
```

- [ ] **Step 2.4: Run tests to verify they pass**

```
uv run pytest tests/widgets/test_manage_bookmarks_dialog.py::test_tree_shows_groups_and_entries tests/widgets/test_manage_bookmarks_dialog.py::test_form_path_hidden_for_group_selection tests/widgets/test_manage_bookmarks_dialog.py::test_form_path_visible_for_entry_selection -v
```
Expected: PASS

- [ ] **Step 2.5: Coding-guideline follow-up checklist**

- [ ] `docs/coding_conventions.md` read
- [ ] All symbols use `snake_case` / `UpperCamelCase` as required
- [ ] All functions/methods have full type annotations
- [ ] `uv run pytest tests/widgets/test_manage_bookmarks_dialog.py -v` passes
- [ ] `uv run ruff check src/nova_navigator/dialogs/manage_bookmarks_dialog.py` passes

---

## Task 3: Mutation tests — add, remove, reorder, move-to-group

**Files:**
- Modify: `tests/widgets/test_manage_bookmarks_dialog.py`

---

- [ ] **Step 3.1: Write the failing tests**

Add to `tests/widgets/test_manage_bookmarks_dialog.py`:

```python
from textual.widgets import Button


def _make_dialog(cfg: BookmarkConfig) -> tuple[ManageBookmarksDialog, type[App[None]]]:
    dialog = ManageBookmarksDialog(cfg)

    class _App(App[None]):
        def compose(self) -> ComposeResult:
            return iter([])

        async def on_mount(self) -> None:
            await self.push_screen(dialog)

    return dialog, _App


@pytest.mark.asyncio
async def test_add_group() -> None:
    cfg = _fixture_config()
    dialog, _App = _make_dialog(cfg)
    app = _App()
    async with app.run_test() as pilot:
        await pilot.pause()
        await pilot.click(app.query_one("#btn_add_group", Button))
        await pilot.pause()
        tree = app.query_one(Tree)
        assert len(list(tree.root.children)) == 3
        assert dialog._working.groups[-1].name == "New Group"


@pytest.mark.asyncio
async def test_add_entry() -> None:
    cfg = _fixture_config()
    dialog, _App = _make_dialog(cfg)
    app = _App()
    async with app.run_test() as pilot:
        await pilot.pause()
        # select first group node so add_entry targets it
        tree = app.query_one(Tree)
        first_group_node = list(tree.root.children)[0]
        tree.select_node(first_group_node)
        await pilot.pause()
        await pilot.click(app.query_one("#btn_add_entry", Button))
        await pilot.pause()
        assert len(dialog._working.groups[0].bookmarks) == 3
        assert dialog._working.groups[0].bookmarks[-1].name == "New Entry"


@pytest.mark.asyncio
async def test_remove_entry() -> None:
    cfg = _fixture_config()
    dialog, _App = _make_dialog(cfg)
    app = _App()
    async with app.run_test() as pilot:
        await pilot.pause()
        tree = app.query_one(Tree)
        first_entry = list(list(tree.root.children)[0].children)[0]
        tree.select_node(first_entry)
        await pilot.pause()
        await pilot.click(app.query_one("#btn_remove", Button))
        await pilot.pause()
        assert len(dialog._working.groups[0].bookmarks) == 1
        assert dialog._working.groups[0].bookmarks[0].name == "Docs"


@pytest.mark.asyncio
async def test_remove_group() -> None:
    cfg = _fixture_config()
    dialog, _App = _make_dialog(cfg)
    app = _App()
    async with app.run_test() as pilot:
        await pilot.pause()
        tree = app.query_one(Tree)
        second_group = list(tree.root.children)[1]
        tree.select_node(second_group)
        await pilot.pause()
        await pilot.click(app.query_one("#btn_remove", Button))
        await pilot.pause()
        assert len(dialog._working.groups) == 1
        assert dialog._working.groups[0].name == "Computer"


@pytest.mark.asyncio
async def test_move_entry_up() -> None:
    cfg = _fixture_config()
    dialog, _App = _make_dialog(cfg)
    app = _App()
    async with app.run_test() as pilot:
        await pilot.pause()
        tree = app.query_one(Tree)
        second_entry = list(list(tree.root.children)[0].children)[1]
        tree.select_node(second_entry)
        await pilot.pause()
        await pilot.click(app.query_one("#btn_move_up", Button))
        await pilot.pause()
        assert dialog._working.groups[0].bookmarks[0].name == "Docs"
        assert dialog._working.groups[0].bookmarks[1].name == "Home"


@pytest.mark.asyncio
async def test_move_entry_down() -> None:
    cfg = _fixture_config()
    dialog, _App = _make_dialog(cfg)
    app = _App()
    async with app.run_test() as pilot:
        await pilot.pause()
        tree = app.query_one(Tree)
        first_entry = list(list(tree.root.children)[0].children)[0]
        tree.select_node(first_entry)
        await pilot.pause()
        await pilot.click(app.query_one("#btn_move_down", Button))
        await pilot.pause()
        assert dialog._working.groups[0].bookmarks[0].name == "Docs"
        assert dialog._working.groups[0].bookmarks[1].name == "Home"


@pytest.mark.asyncio
async def test_move_group_up() -> None:
    cfg = _fixture_config()
    dialog, _App = _make_dialog(cfg)
    app = _App()
    async with app.run_test() as pilot:
        await pilot.pause()
        tree = app.query_one(Tree)
        second_group = list(tree.root.children)[1]
        tree.select_node(second_group)
        await pilot.pause()
        await pilot.click(app.query_one("#btn_move_up", Button))
        await pilot.pause()
        assert dialog._working.groups[0].name == "Work"
        assert dialog._working.groups[1].name == "Computer"
```

- [ ] **Step 3.2: Run tests to verify they fail**

```
uv run pytest tests/widgets/test_manage_bookmarks_dialog.py::test_add_group tests/widgets/test_manage_bookmarks_dialog.py::test_add_entry tests/widgets/test_manage_bookmarks_dialog.py::test_remove_entry tests/widgets/test_manage_bookmarks_dialog.py::test_remove_group tests/widgets/test_manage_bookmarks_dialog.py::test_move_entry_up tests/widgets/test_manage_bookmarks_dialog.py::test_move_entry_down tests/widgets/test_manage_bookmarks_dialog.py::test_move_group_up -v
```
Expected: FAIL — `ManageBookmarksDialog` not yet implemented in Step 2 (or these specific paths fail).
If Task 2 is complete, these may already pass — that is also acceptable.

- [ ] **Step 3.3: Run all new mutation tests and fix any failures in the implementation**

```
uv run pytest tests/widgets/test_manage_bookmarks_dialog.py -v
```
Expected: all PASS

- [ ] **Step 3.4: Coding-guideline follow-up checklist**

- [ ] `docs/coding_conventions.md` read
- [ ] All symbols use `snake_case` / `UpperCamelCase` as required
- [ ] All functions/methods have full type annotations
- [ ] `uv run pytest tests/widgets/test_manage_bookmarks_dialog.py -v` passes
- [ ] `uv run ruff check src/nova_navigator/dialogs/manage_bookmarks_dialog.py` passes

---

## Task 4: OK/Cancel persistence tests

**Files:**
- Modify: `tests/widgets/test_manage_bookmarks_dialog.py`

---

- [ ] **Step 4.1: Write the failing tests**

Add to `tests/widgets/test_manage_bookmarks_dialog.py`:

```python
from unittest.mock import patch, MagicMock

from nova_navigator.config.bookmarks import BookmarkConfig


@pytest.mark.asyncio
async def test_ok_saves_config() -> None:
    cfg = _fixture_config()
    dialog, _App = _make_dialog(cfg)
    app = _App()

    saved_groups = None

    def fake_save(self_: BookmarkConfig) -> None:
        nonlocal saved_groups
        saved_groups = list(self_.groups)

    with patch("nova_navigator.config.bookmarks.BookmarkConfig.save", fake_save):
        async with app.run_test() as pilot:
            await pilot.pause()
            # add a group so we have something to verify
            await pilot.click(app.query_one("#btn_add_group", Button))
            await pilot.pause()
            # press OK
            await pilot.click(app.query_one("#btn_ok", Button))
            await pilot.pause()

    assert saved_groups is not None
    assert len(saved_groups) == 3  # 2 original + 1 new


@pytest.mark.asyncio
async def test_cancel_discards_changes() -> None:
    from nova_navigator.config import conf_
    original_groups = list(conf_.bookmarks.groups)

    cfg = _fixture_config()
    dialog, _App = _make_dialog(cfg)
    app = _App()

    async with app.run_test() as pilot:
        await pilot.pause()
        # add a group (mutation)
        await pilot.click(app.query_one("#btn_add_group", Button))
        await pilot.pause()
        # press Cancel
        await pilot.click(app.query_one("#btn_cancel", Button))
        await pilot.pause()

    # conf_.bookmarks.groups should be unchanged
    assert len(conf_.bookmarks.groups) == len(original_groups)
```

- [ ] **Step 4.2: Run tests to verify they fail**

```
uv run pytest tests/widgets/test_manage_bookmarks_dialog.py::test_ok_saves_config tests/widgets/test_manage_bookmarks_dialog.py::test_cancel_discards_changes -v
```
Expected: FAIL

- [ ] **Step 4.3: Verify existing implementation already handles these (or fix)**

The `_action_ok` method in the implementation from Task 2 calls `conf_.bookmarks.groups = self._working.groups; conf_.bookmarks.save()`.
The `action_cancel` method calls `self.dismiss(None)` without touching `conf_`.
These should pass without code changes. If not, fix `_action_ok` / `action_cancel`.

- [ ] **Step 4.4: Run all tests to verify**

```
uv run pytest tests/widgets/test_manage_bookmarks_dialog.py -v
```
Expected: all PASS

- [ ] **Step 4.5: Coding-guideline follow-up checklist**

- [ ] `docs/coding_conventions.md` read
- [ ] All symbols use `snake_case` / `UpperCamelCase` as required
- [ ] All functions/methods have full type annotations
- [ ] `uv run pytest tests/widgets/test_manage_bookmarks_dialog.py -v` passes
- [ ] `uv run ruff check src/nova_navigator/dialogs/manage_bookmarks_dialog.py` passes

---

## Task 5: Export from `dialogs/__init__.py` and wire into `main.py`

**Files:**
- Modify: `src/nova_navigator/dialogs/__init__.py`
- Modify: `src/nova_navigator/main.py`

---

- [ ] **Step 5.1: Update `dialogs/__init__.py`**

In `src/nova_navigator/dialogs/__init__.py`, add the export:

```python
from .bookmarks_dialog import BookmarksDialog
from .dialog import DefaultButton
from .files_dialog import CopyMoveFilesDialog, DeleteFilesDialog
from .manage_bookmarks_dialog import ManageBookmarksDialog

# from .processes_dialog import ProcessesDialog

__all__ = [
    "BookmarksDialog",
    "CopyMoveFilesDialog",
    "DefaultButton",
    "DeleteFilesDialog",
    "ManageBookmarksDialog",
    # "ProcessesDialog",
]
```

- [ ] **Step 5.2: Add binding and action to `main.py`**

In `src/nova_navigator/main.py`, add the import:

```python
from nova_navigator.dialogs import BookmarksDialog, ManageBookmarksDialog
```

In `MainScreen.BINDINGS`, add after the `ctrl+b` binding:

```python
Binding("ctrl+shift+b", "edit_bookmarks", "Edit Bookmarks"),
```

Add the action method after `_action_show_bookmarks`:

```python
async def _action_edit_bookmarks(self) -> None:
    from nova_navigator.config import conf_
    await self.app.push_screen_wait(ManageBookmarksDialog(conf_.bookmarks))
```

- [ ] **Step 5.3: Add "Edit Bookmarks…" menu entry**

In `main.py`, find the Commands menu (or the section that adds the "Bookmarks" entry) and add alongside it. Look for the existing `Bookmarks` action in `compose()`. Based on the current code structure there is no dedicated Commands menu; add the entry to the File menu after the existing bookmarks-related entry. Search for where `_action_show_bookmarks` is referenced in the menu and add below it:

Find the block in `compose()` that builds the File menu. After any bookmark action (or at the end of the File menu entries), add:

```python
mc.action("Bookmarks", shortcut="Ctrl+B", action="show_bookmarks", name="bookmarks"),
mc.action("Edit Bookmarks…", shortcut="Ctrl+Shift+B", action="edit_bookmarks", name="edit_bookmarks"),
```

If a "Bookmarks" entry already exists in the menu, only add the "Edit Bookmarks…" line.

- [ ] **Step 5.4: Verify no regressions**

```
uv run pytest tests/ -v --ignore=tests/widgets/test_manage_bookmarks_dialog.py
```
Expected: all existing tests pass

```
uv run pytest tests/widgets/test_manage_bookmarks_dialog.py -v
```
Expected: all PASS

- [ ] **Step 5.5: Coding-guideline follow-up checklist**

- [ ] `docs/coding_conventions.md` read
- [ ] All symbols use `snake_case` / `UpperCamelCase` as required
- [ ] All functions/methods have full type annotations
- [ ] `uv run qa` passes (lint + type check + tests)

---

## Task 6: Full QA pass

- [ ] **Step 6.1: Run full QA**

```
uv run qa
```
Expected: zero failures. Fix any lint or type errors before proceeding.

- [ ] **Step 6.2: Manual smoke test**

```
uv run nn
```

1. Press `Ctrl+Shift+B` — the Edit Bookmarks modal should open.
2. Verify both groups and entries are visible in the tree.
3. Select a group node — confirm Path field is hidden; Name and Icon are populated.
4. Select an entry — confirm all three fields are visible and populated.
5. Add a new group; confirm it appears in the tree.
6. Add an entry to the new group; confirm it appears.
7. Move an entry up and down; confirm order changes.
8. Move an entry to a different group via "Move to Group…".
9. Press OK — re-open the dialog and verify changes persisted.
10. Repeat edits, press Cancel — re-open and confirm changes are gone.
11. Press `Ctrl+B` — confirm read-only BookmarksDialog still works.
