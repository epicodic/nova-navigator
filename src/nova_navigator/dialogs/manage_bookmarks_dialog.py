"""Bookmark editor dialog."""

from __future__ import annotations

import copy
from typing import ClassVar, cast

from textual import on
from textual.app import ComposeResult
from textual.binding import Binding, BindingType
from textual.containers import Horizontal, Vertical
from textual.screen import ModalScreen
from textual.widgets import Button, Input, Label, ListItem, ListView, Tree

from nova_navigator.config.bookmarks import Bookmark, BookmarkConfig, Group
from nova_navigator.icons import ICONS


class MoveToGroupDialog(ModalScreen[int | None]):
    """Modal dialog that lists group names and returns the index of the chosen one."""

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

    def __init__(self, group_names: list[str]) -> None:
        super().__init__()
        self._group_names = group_names

    def compose(self) -> ComposeResult:
        with Vertical(id="mtg_box"):
            yield Label("Move to Group")
            yield ListView(
                *[ListItem(Label(name)) for name in self._group_names],
                id="group_list",
            )
            with Horizontal(id="mtg_buttons"):
                yield Button("Cancel", id="mtg_cancel", variant="default", flat=True)
                yield Button("OK", id="mtg_ok", variant="primary", flat=True)

    def on_button_pressed(self, event: Button.Pressed) -> None:
        if event.button.id == "mtg_ok":
            idx = self.query_one(ListView).index
            if idx is not None:
                self.dismiss(idx)
        else:
            self.dismiss(None)

    def action_cancel(self) -> None:
        self.dismiss(None)


# Tag types stored in tree node data
_GroupTag = tuple[str, int]  # ("group", group_index)
_EntryTag = tuple[str, int, int]  # ("entry", group_index, entry_index)
_NodeTag = _GroupTag | _EntryTag


class ManageBookmarksDialog(ModalScreen[None]):
    """Full-screen modal for editing bookmark groups and entries."""

    DEFAULT_CSS = """
    ManageBookmarksDialog {
        align: center middle;

        #editor_box {
            border: round $accent;
            width: 80%;
            height: 80%;
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

        #input_name {
            width: 1fr;
        }

        #input_icon {
            width: 10;
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

        Button {
            width: auto;
            max-width: 15;
            min-width: 1;
            margin: 0 2;
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

    _config: BookmarkConfig
    _working: BookmarkConfig
    _current_tag: _NodeTag | None

    def __init__(self, config: BookmarkConfig) -> None:
        super().__init__()
        self._config = config
        self._working = copy.deepcopy(config)
        self._current_tag = None

    # ------------------------------------------------------------------ compose

    def compose(self) -> ComposeResult:
        with Vertical(id="editor_box"):
            yield Label("Edit Bookmarks", id="editor_title")
            with Vertical(id="tree_container"):
                yield Tree("Bookmarks", id="bookmark_tree")
            with Horizontal(id="action_row"):
                yield Button("Add Group", id="btn_add_group", flat=True)
                yield Button("Add Entry", id="btn_add_entry", disabled=True, flat=True)
                yield Button("Remove", id="btn_remove", disabled=True, flat=True)
                yield Button("↑ Move Up", id="btn_move_up", disabled=True, flat=True)
                yield Button("↓ Move Down", id="btn_move_down", disabled=True, flat=True)
                yield Button("Move to…", id="btn_move_to_group", disabled=True, flat=True)
            with Vertical(id="form_container"):
                with Horizontal(id="form_row_name"):
                    yield Label("Name: ")
                    yield Input(placeholder="Name", id="input_name", compact=True)
                    yield Label("  Icon: ")
                    yield Input(placeholder="Icon", id="input_icon", compact=True)
                with Horizontal(id="form_row_path"):
                    yield Label("Path: ")
                    yield Input(placeholder="Path", id="input_path", compact=True)
            with Horizontal(id="ok_cancel_row"):
                yield Button("Cancel", id="btn_cancel", variant="default", flat=True)
                yield Button("OK", id="btn_ok", variant="primary", flat=True)

    def on_mount(self) -> None:
        tree: Tree[_NodeTag] = self.query_one(Tree)
        tree.show_root = False
        self._rebuild_tree(select_tag=None)
        self._sync_form_to_selection(None)
        tree.unselect()  # reset cursor to -1: show_root change clamps cursor to 0 via stale cache
        tree.focus()

    # ------------------------------------------------------------------ tree

    def _rebuild_tree(self, select_tag: _NodeTag | None) -> None:
        tree: Tree[_NodeTag] = self.query_one(Tree)
        tree.clear()
        for gi, group in enumerate(self._working.groups):
            icon = ICONS.get_icon(group.icon) + " " if group.icon else ""
            group_node = tree.root.add(
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
        tree: Tree[_NodeTag] = self.query_one(Tree)
        for node in tree.root.children:
            if node.data == tag:
                tree.select_node(node)
                return
            for child in node.children:
                if child.data == tag:
                    tree.select_node(child)
                    return

    def _selected_tag(self) -> _NodeTag | None:
        tree: Tree[_NodeTag] = self.query_one(Tree)
        node = tree.cursor_node
        if node is None or node.data is None:
            return None
        return node.data  # type: ignore[return-value]

    # ------------------------------------------------------------------ form sync

    def _sync_form_to_selection(self, tag: _NodeTag | None) -> None:
        self._current_tag = tag
        input_name = self.query_one("#input_name", Input)
        input_icon = self.query_one("#input_icon", Input)
        input_path = self.query_one("#input_path", Input)

        if tag is None:
            input_name.value = ""
            input_icon.value = ""
            input_path.value = ""
            input_name.disabled = True
            input_icon.disabled = True
            input_path.disabled = True
            self.query_one("#form_row_path", Horizontal).display = True
            return

        input_name.disabled = False
        input_icon.disabled = False

        if tag[0] == "group":
            gi = tag[1]
            group = self._working.groups[gi]
            input_name.value = group.name
            input_icon.value = group.icon or ""
            input_path.value = ""
            self.query_one("#form_row_path", Horizontal).display = False
        else:
            entry_tag = cast("_EntryTag", tag)
            gi, ei = entry_tag[1], entry_tag[2]
            entry = self._working.groups[gi].bookmarks[ei]
            input_name.value = entry.name
            input_icon.value = entry.icon or ""
            input_path.value = entry.path
            self.query_one("#form_row_path", Horizontal).display = True
            input_path.disabled = False

    def _update_button_states(self, tag: _NodeTag | None) -> None:
        has_groups = len(self._working.groups) > 0
        something_selected = tag is not None

        self.query_one("#btn_add_entry", Button).disabled = not has_groups
        self.query_one("#btn_remove", Button).disabled = not something_selected
        self.query_one("#btn_move_up", Button).disabled = not self._can_move_up(tag)
        self.query_one("#btn_move_down", Button).disabled = not self._can_move_down(tag)
        self.query_one("#btn_move_to_group", Button).disabled = not (
            tag is not None and tag[0] == "entry" and len(self._working.groups) >= 2  # noqa: PLR2004
        )

    def _can_move_up(self, tag: _NodeTag | None) -> bool:
        if tag is None:
            return False
        if tag[0] == "group":
            return tag[1] > 0
        entry_tag = cast("_EntryTag", tag)
        return entry_tag[2] > 0

    def _can_move_down(self, tag: _NodeTag | None) -> bool:
        if tag is None:
            return False
        if tag[0] == "group":
            return tag[1] < len(self._working.groups) - 1
        entry_tag = cast("_EntryTag", tag)
        gi, ei = entry_tag[1], entry_tag[2]
        return ei < len(self._working.groups[gi].bookmarks) - 1

    # ------------------------------------------------------------------ event handlers

    def on_tree_node_highlighted(self, event: Tree.NodeHighlighted[_NodeTag]) -> None:
        tag = event.node.data
        if tag is None:
            self._sync_form_to_selection(None)
            self._update_button_states(None)
            return
        self._sync_form_to_selection(tag)
        self._update_button_states(tag)

    @on(Input.Changed, "#input_name")
    def _on_name_changed(self, event: Input.Changed) -> None:
        tag = self._current_tag
        if tag is None:
            return
        if tag[0] == "group":
            self._working.groups[tag[1]].name = event.value
        else:
            entry_tag = cast("_EntryTag", tag)
            gi, ei = entry_tag[1], entry_tag[2]
            self._working.groups[gi].bookmarks[ei].name = event.value

    @on(Input.Changed, "#input_icon")
    def _on_icon_changed(self, event: Input.Changed) -> None:
        tag = self._current_tag
        if tag is None:
            return
        icon_val: str | None = event.value if event.value else None
        if tag[0] == "group":
            self._working.groups[tag[1]].icon = icon_val
        else:
            entry_tag = cast("_EntryTag", tag)
            gi, ei = entry_tag[1], entry_tag[2]
            self._working.groups[gi].bookmarks[ei].icon = icon_val

    @on(Input.Changed, "#input_path")
    def _on_path_changed(self, event: Input.Changed) -> None:
        tag = self._current_tag
        if tag is None or tag[0] != "entry":
            return
        entry_tag = cast("_EntryTag", tag)
        gi, ei = entry_tag[1], entry_tag[2]
        self._working.groups[gi].bookmarks[ei].path = event.value

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
        self._config.groups = self._working.groups
        self._config.save()
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
            entry_tag = cast("_EntryTag", tag)
            gi, ei = entry_tag[1], entry_tag[2]
            del self._working.groups[gi].bookmarks[ei]
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
            entry_tag = cast("_EntryTag", tag)
            gi, ei = entry_tag[1], entry_tag[2]
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
            entry_tag = cast("_EntryTag", tag)
            gi, ei = entry_tag[1], entry_tag[2]
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
        candidate_names = [g.name for idx, g in enumerate(self._working.groups) if idx != gi]
        candidate_indices = [idx for idx in range(len(self._working.groups)) if idx != gi]
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
        entry_tag = cast("_EntryTag", tag)
        gi, ei = entry_tag[1], entry_tag[2]
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
