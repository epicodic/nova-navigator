"""Bookmark editor dialog."""

from __future__ import annotations

from collections.abc import Callable
from typing import ClassVar, cast

from textual import on
from textual.app import ComposeResult
from textual.binding import Binding, BindingType
from textual.containers import Horizontal, Vertical
from textual.widgets import Label, ListItem, ListView, Tree

from nova_navigator.config.bookmarks import Bookmark, BookmarkConfig, Group
from nova_navigator.dialogs.constants import DEFAULT_BOOKMARKS_GROUP
from nova_navigator.dialogs.dialog import Dialog
from nova_navigator.dialogs.icon_picker_dialog import IconPickerDialog
from nova_navigator.icons import ICONS
from nova_navigator.response import Response
from nova_navigator.widgets.popup_widget import PopupWidget
from nova_widgets import Button, ButtonBox, Input

# Tag types stored in tree node data
_GroupTag = tuple[str, int]  # ("group", group_index)
_EntryTag = tuple[str, int, int]  # ("entry", group_index, entry_index)
_NodeTag = _GroupTag | _EntryTag


class _MoveToGroupOverlay(PopupWidget, can_focus=True):
    """Inline overlay showing a list for moving an entry to another group.

    Always present in the DOM.
    Uses CSS `visibility` (not `display`) to show/hide so that no layout event
    is fired on the parent ModalScreen — preventing the dialog from jumping.
    """

    CLOSE_ACTION = PopupWidget.CloseAction.KEEP

    DEFAULT_CSS = """
    _MoveToGroupOverlay {
        width: 30;
        height: auto;

        ListView {
            height: 10
        }
    }
    """

    _on_selected: Callable[[int], None]
    _options: list[tuple[str, int]]

    def __init__(self, on_selected: Callable[[int], None]) -> None:
        # CloseAction.KEEP: base close() leaves display/DOM untouched; we manage visibility.
        super().__init__("", (0, 0))
        self._on_selected = on_selected
        self._options = []
        self.visible = False

    def compose(self) -> ComposeResult:
        yield ListView()

    def open(self, options: list[tuple[str, int]], position: tuple[int, int]) -> None:
        """Populate the list, reposition, and show without triggering layout."""
        self._saved_focus = self.app.focused
        self._options = options
        lv = self.query_one(ListView)
        lv.clear()
        for label, _ in options:
            lv.append(ListItem(Label(label)))
        self.offset = position
        self.visible = True
        lv.focus()
        lv.index = 0

    def close(self) -> None:
        """Restore focus and hide via visibility to avoid layout reflow on the parent."""
        if self._saved_focus:
            self._saved_focus.focus()
        self.visible = False

    @on(ListView.Selected)
    def _on_list_selected(self, event: ListView.Selected) -> None:
        index = event.list_view.index
        if index is None or index >= len(self._options):
            return
        self._on_selected(self._options[index][1])
        self.close()


class EditBookmarksDialog(Dialog):
    """Full-screen modal for editing bookmark groups and entries."""

    DEFAULT_CSS = """
    EditBookmarksDialog {

        #dialog_box {
            width: 80%;
            height: 80%;
        }

        #bookmark_tree {
            width: 1fr;
        }

        #action_row {
            width: auto;
            height: 1fr;

            Vertical {
                width: auto;
            }

            .button-box-row {
                width: auto;
            }
        }

        #tree_row {
            height: 1fr;
        }

        #tree_row {
            height: 1fr;
        }

        #form_container {
            height: auto;
            margin-top: 1;
            padding: 0 1;
        }

        #form_row_name {
            height: auto;
        }

        #input_name {
            width: 1fr;
        }

        #input_path {
            width: 1fr;
        }

        #input_icon {
            width: 20;
        }

        #btn_pick_icon {
            width: 5;
            max-width: 5;
            margin: 0 0 0 1;
        }

        #form_row_path {
            height: auto;
        }

        .form_label {
            width: auto;
            border: inner transparent;
        }

    }
    """

    BINDINGS: ClassVar[list[BindingType]] = [
        Binding("alt+up", "move_up", "Move Up", show=False),
        Binding("alt+down", "move_down", "Move Down", show=False),
        Binding("delete", "remove_item", "Remove", show=False),
        Binding("f8", "remove_item", "Remove", show=False),
    ]

    _config: BookmarkConfig
    _current_tag: _NodeTag | None
    _syncing: bool
    _prefill_tag: _NodeTag | None

    def __init__(
        self,
        config: BookmarkConfig,
        *,
        prefill: tuple[str, str, str] | None = None,
    ) -> None:
        super().__init__("Edit Bookmarks", buttons=[Response.OK, Response.CANCEL])
        self._config = config
        self._current_tag = None
        self._syncing = False
        self._prefill_tag = None
        if prefill is not None:
            group_name, entry_name, entry_path = prefill
            groups = self._config.groups
            gi = next((i for i, g in enumerate(groups) if g.name == group_name), None)
            if gi is None:
                groups.append(Group(name=group_name))
                gi = len(groups) - 1
            groups[gi].bookmarks.append(Bookmark(name=entry_name, path=entry_path))
            self._prefill_tag = ("entry", gi, len(groups[gi].bookmarks) - 1)

    # ------------------------------------------------------------------ compose

    def compose_content(self) -> ComposeResult:
        yield Horizontal(
            Tree("Bookmarks", id="bookmark_tree"),
            ButtonBox(
                [
                    [Button("Add Group", id="btn_add_group")],
                    [Button("Add Entry", id="btn_add_entry", disabled=True)],
                    [Button("Remove", id="btn_remove", disabled=True)],
                    [Button("↑ Move Up", id="btn_move_up", disabled=True)],
                    [Button("↓ Move Down", id="btn_move_down", disabled=True)],
                    [Button("Move to…", id="btn_move_to_group", disabled=True)],
                ],
                id="action_row",
            ),
            id="tree_row",
        )
        yield Vertical(
            Horizontal(
                Label("Name: ", classes="form_label"),
                Input(placeholder="Name", id="input_name"),
                Label("  Icon: ", classes="form_label"),
                Input(placeholder="Icon", id="input_icon"),
                Button("…", id="btn_pick_icon"),
                id="form_row_name",
            ),
            Horizontal(
                Label("URI: ", classes="form_label"),
                Input(placeholder="URI", id="input_path"),
                id="form_row_path",
            ),
            id="form_container",
        )

    def on_mount(self) -> None:
        # Mount the overlay as a direct child of the ModalScreen (not inside #dialog_box)
        # so that overlay: screen positions it relative to the actual screen origin.
        self.mount(_MoveToGroupOverlay(on_selected=self._on_group_selected))
        tree: Tree[_NodeTag] = self.query_one(Tree)
        tree.show_root = False
        select_tag = self._prefill_tag
        if select_tag is None:
            gi = next(
                (i for i, g in enumerate(self._config.groups) if g.name == DEFAULT_BOOKMARKS_GROUP),
                None,
            )
            if gi is not None:
                select_tag = ("group", gi)
        self._rebuild_tree(select_tag=select_tag)
        if select_tag is None:
            self._sync_form_to_selection(None)
            tree.unselect()  # reset cursor to -1: show_root change clamps cursor to 0 via stale cache
        tree.focus()

    # ------------------------------------------------------------------ tree

    def _rebuild_tree(self, select_tag: _NodeTag | None) -> None:
        tree: Tree[_NodeTag] = self.query_one(Tree)
        tree.clear()
        for gi, group in enumerate(self._config.groups):
            icon = ICONS.get_icon(group.icon).glyph + " " if group.icon else ""
            group_node = tree.root.add(
                f"{icon}{group.name}",
                data=("group", gi),
                expand=True,
            )
            for ei, entry in enumerate(group.bookmarks):
                eicon = ICONS.get_icon(entry.icon).glyph + " " if entry.icon else ""
                group_node.add_leaf(
                    f"{eicon}{entry.name}  {entry.path}",
                    data=("entry", gi, ei),
                )

        if select_tag is not None:
            self._select_node_by_tag(select_tag)

    def _select_node_by_tag(self, tag: _NodeTag) -> None:
        tree: Tree[_NodeTag] = self.query_one(Tree)
        # After tree.clear() + node additions the internal line cache is stale and
        # every new node has _line=0 by default.  Accessing _tree_lines forces
        # _build() to run, assigning correct _line values before select_node uses them.
        tree._tree_lines  # noqa: B018, RUF100, SLF001
        # validate_cursor_line clamps -1 to 0, so the cursor may already sit at line 0.
        # watch_cursor_line then sees previous==new and skips NodeHighlighted for the first
        # node.  set_reactive bypasses validation, giving a true -1 so the watcher fires.
        tree.set_reactive(Tree.cursor_line, -1)  # ty: ignore[invalid-argument-type]
        for node in tree.root.children:
            if node.data == tag:
                node.expand()
                tree.move_cursor(node)
                return
            for child in node.children:
                if child.data == tag:
                    tree.move_cursor(child)
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
        self._syncing = True
        try:
            if tag is None:
                input_name.value = ""
                input_icon.value = ""
                input_path.value = ""
                input_name.disabled = True
                input_icon.disabled = True
                input_path.disabled = True
                self.query_one("#btn_pick_icon", Button).disabled = True
                self.query_one("#form_row_path", Horizontal).display = True
                return

            input_name.disabled = False
            input_icon.disabled = False
            self.query_one("#btn_pick_icon", Button).disabled = False

            if tag[0] == "group":
                gi = tag[1]
                group = self._config.groups[gi]
                input_name.value = group.name
                input_icon.value = group.icon or ""
                input_path.value = ""
                self.query_one("#form_row_path", Horizontal).display = False
            else:
                entry_tag = cast("_EntryTag", tag)
                gi, ei = entry_tag[1], entry_tag[2]
                entry = self._config.groups[gi].bookmarks[ei]
                input_name.value = entry.name
                input_icon.value = entry.icon or ""
                input_path.value = entry.path
                self.query_one("#form_row_path", Horizontal).display = True
                input_path.disabled = False
        finally:
            self._syncing = False

    def _update_button_states(self, tag: _NodeTag | None) -> None:
        has_groups = len(self._config.groups) > 0
        something_selected = tag is not None

        is_protected = tag is not None and tag[0] == "group" and self._config.groups[tag[1]].name == DEFAULT_BOOKMARKS_GROUP
        self.query_one("#btn_add_entry", Button).disabled = not has_groups
        self.query_one("#btn_remove", Button).disabled = not something_selected or is_protected
        self.query_one("#btn_move_up", Button).disabled = not self._can_move_up(tag)
        self.query_one("#btn_move_down", Button).disabled = not self._can_move_down(tag)
        can_move_to = tag is not None and tag[0] == "entry" and len(self._config.groups) >= 2  # noqa: PLR2004
        self.query_one("#btn_move_to_group", Button).disabled = not can_move_to

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
            return tag[1] < len(self._config.groups) - 1
        entry_tag = cast("_EntryTag", tag)
        gi, ei = entry_tag[1], entry_tag[2]
        return ei < len(self._config.groups[gi].bookmarks) - 1

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
        if self._syncing:
            return
        tag = self._current_tag
        if tag is None:
            return
        if tag[0] == "group":
            self._config.groups[tag[1]].name = event.value
        else:
            entry_tag = cast("_EntryTag", tag)
            gi, ei = entry_tag[1], entry_tag[2]
            self._config.groups[gi].bookmarks[ei].name = event.value
        self._update_current_node_label()

    @on(Input.Changed, "#input_icon")
    def _on_icon_changed(self, event: Input.Changed) -> None:
        if self._syncing:
            return
        tag = self._current_tag
        if tag is None:
            return
        icon_val: str | None = event.value if event.value else None
        if tag[0] == "group":
            self._config.groups[tag[1]].icon = icon_val
        else:
            entry_tag = cast("_EntryTag", tag)
            gi, ei = entry_tag[1], entry_tag[2]
            self._config.groups[gi].bookmarks[ei].icon = icon_val
        self._update_current_node_label()

    @on(Input.Changed, "#input_path")
    def _on_path_changed(self, event: Input.Changed) -> None:
        if self._syncing:
            return
        tag = self._current_tag
        if tag is None or tag[0] != "entry":
            return
        entry_tag = cast("_EntryTag", tag)
        gi, ei = entry_tag[1], entry_tag[2]
        self._config.groups[gi].bookmarks[ei].path = event.value
        self._update_current_node_label()

    def _update_current_node_label(self) -> None:
        """Update the currently-selected tree node's label in-place from _config."""
        tag = self._current_tag
        if tag is None:
            return
        tree: Tree[_NodeTag] = self.query_one(Tree)
        node = tree.cursor_node
        if node is None or node.data != tag:
            return
        if tag[0] == "group":
            gi = tag[1]
            group = self._config.groups[gi]
            icon = ICONS.get_icon(group.icon).glyph + " " if group.icon else ""
            node.set_label(f"{icon}{group.name}")
        else:
            entry_tag = cast("_EntryTag", tag)
            gi, ei = entry_tag[1], entry_tag[2]
            entry = self._config.groups[gi].bookmarks[ei]
            eicon = ICONS.get_icon(entry.icon).glyph + " " if entry.icon else ""
            node.set_label(f"{eicon}{entry.name}  {entry.path}")

    # ------------------------------------------------------------------ button actions

    @property
    def config(self) -> BookmarkConfig:
        return self._config

    def action_accept_dialog(self) -> None:
        self._action_ok()

    @on(Button.Pressed, "#btn_add_group")
    def _on_btn_add_group(self, _event: Button.Pressed) -> None:
        self.action_add_group()

    @on(Button.Pressed, "#btn_add_entry")
    def _on_btn_add_entry(self, _event: Button.Pressed) -> None:
        self.action_add_entry()

    @on(Button.Pressed, "#btn_remove")
    def _on_btn_remove(self, _event: Button.Pressed) -> None:
        self.action_remove_item()

    @on(Button.Pressed, "#btn_move_up")
    def _on_btn_move_up(self, _event: Button.Pressed) -> None:
        self.action_move_up()

    @on(Button.Pressed, "#btn_move_down")
    def _on_btn_move_down(self, _event: Button.Pressed) -> None:
        self.action_move_down()

    @on(Button.Pressed, "#btn_move_to_group")
    def _on_btn_move_to_group(self, _event: Button.Pressed) -> None:
        self._run_move_to_group()

    @on(Button.Pressed, "#btn_pick_icon")
    def _on_btn_pick_icon(self, _event: Button.Pressed) -> None:
        self._run_pick_icon()

    def _action_ok(self) -> None:
        self.dismiss(Response.OK)

    def action_add_group(self) -> None:
        new_group = Group(name="New Group")
        self._config.groups.append(new_group)
        new_tag: _NodeTag = ("group", len(self._config.groups) - 1)
        self._rebuild_tree(select_tag=new_tag)
        self._sync_form_to_selection(new_tag)
        self._update_button_states(new_tag)

    def action_add_entry(self) -> None:
        tag = self._current_tag
        if tag is None:
            gi = 0
        else:
            gi = tag[1]
        new_entry = Bookmark(name="New Entry")
        self._config.groups[gi].bookmarks.append(new_entry)
        new_tag: _NodeTag = ("entry", gi, len(self._config.groups[gi].bookmarks) - 1)
        self._rebuild_tree(select_tag=new_tag)
        self._sync_form_to_selection(new_tag)
        self._update_button_states(new_tag)

    def action_remove_item(self) -> None:
        tag = self._current_tag
        if tag is None:
            return
        if tag[0] == "group":
            del self._config.groups[tag[1]]
        else:
            entry_tag = cast("_EntryTag", tag)
            gi, ei = entry_tag[1], entry_tag[2]
            del self._config.groups[gi].bookmarks[ei]
        self._rebuild_tree(select_tag=None)
        self._sync_form_to_selection(None)
        self._update_button_states(None)

    def action_move_up(self) -> None:
        tag = self._current_tag
        if tag is None or not self._can_move_up(tag):
            return
        if tag[0] == "group":
            gi = tag[1]
            lst = self._config.groups
            lst[gi - 1], lst[gi] = lst[gi], lst[gi - 1]
            new_tag: _NodeTag = ("group", gi - 1)
        else:
            entry_tag = cast("_EntryTag", tag)
            gi, ei = entry_tag[1], entry_tag[2]
            lst2 = self._config.groups[gi].bookmarks
            lst2[ei - 1], lst2[ei] = lst2[ei], lst2[ei - 1]
            new_tag = ("entry", gi, ei - 1)
        self._rebuild_tree(select_tag=new_tag)
        self._sync_form_to_selection(new_tag)
        self._update_button_states(new_tag)

    def action_move_down(self) -> None:
        tag = self._current_tag
        if tag is None or not self._can_move_down(tag):
            return
        if tag[0] == "group":
            gi = tag[1]
            lst = self._config.groups
            lst[gi + 1], lst[gi] = lst[gi], lst[gi + 1]
            new_tag: _NodeTag = ("group", gi + 1)
        else:
            entry_tag = cast("_EntryTag", tag)
            gi, ei = entry_tag[1], entry_tag[2]
            lst2 = self._config.groups[gi].bookmarks
            lst2[ei + 1], lst2[ei] = lst2[ei], lst2[ei + 1]
            new_tag = ("entry", gi, ei + 1)
        self._rebuild_tree(select_tag=new_tag)
        self._sync_form_to_selection(new_tag)
        self._update_button_states(new_tag)

    def _run_pick_icon(self) -> None:
        current = self.query_one("#input_icon", Input).value or None
        _dlg = IconPickerDialog(initial_icon=current)

        def _on_icon_picked(result: Response | None) -> None:
            if result != Response.OK:
                return
            if _dlg.selected_icon is not None:
                self.query_one("#input_icon", Input).value = _dlg.selected_icon

        self.app.push_screen(_dlg, callback=_on_icon_picked)

    def _run_move_to_group(self) -> None:
        tag = self._current_tag
        if tag is None or tag[0] != "entry":
            return
        gi = tag[1]
        options = [(g.name, idx) for idx, g in enumerate(self._config.groups) if idx != gi]
        btn = self.query_one("#btn_move_to_group", Button)
        pos = (btn.region.x, btn.region.y + btn.region.height)
        self.query_one(_MoveToGroupOverlay).open(options, pos)

    def _on_group_selected(self, target_gi: int) -> None:
        tag = self._current_tag
        if tag is None or tag[0] != "entry":
            return
        entry_tag = cast("_EntryTag", tag)
        gi, ei = entry_tag[1], entry_tag[2]
        entry = self._config.groups[gi].bookmarks.pop(ei)
        self._config.groups[target_gi].bookmarks.append(entry)
        new_tag: _NodeTag = (
            "entry",
            target_gi,
            len(self._config.groups[target_gi].bookmarks) - 1,
        )
        self._rebuild_tree(select_tag=new_tag)
        self._sync_form_to_selection(new_tag)
        self._update_button_states(new_tag)
        self.query_one(Tree).focus()
