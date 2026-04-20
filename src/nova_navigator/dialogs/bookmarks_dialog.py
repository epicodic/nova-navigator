from textual.app import ComposeResult
from textual.containers import Vertical
from textual.events import Focus
from textual.message import Message
from textual.widgets import Tree

from nova_navigator.dialogs.edit_bookmarks_dialog import EditBookmarksDialog
from nova_widgets import Button

from ..config import conf_
from ..dialogs.constants import DEFAULT_BOOKMARKS_GROUP
from ..icons import ICONS
from ..widgets.popup_widget import PopupWidget


class BookmarksDialog(PopupWidget, can_focus=True):
    """Bookmarks dialog overlay widget."""

    CLOSE_ACTION = PopupWidget.CloseAction.REMOVE
    SHOW_CLOSE_BUTTON = True

    DEFAULT_CSS = """
        BookmarksDialog {
            width: 40;
            height: 20;

            Tree {
                background: transparent;
            }
        }


        BookmarksDialog #btn_edit {
            width: 100%;
            height: 1;
            border: none;
            background: $panel-lighten-1;
            color: $text-muted;

            &:hover {
                background: $accent;
                color: $text;
            }
        }
    """

    class BookmarkSelected(Message):
        def __init__(self, bookmark_path: str) -> None:
            super().__init__()
            self.bookmark_path = bookmark_path

    def __init__(self, position: tuple[int, int]) -> None:
        super().__init__("Bookmarks", position)

    def compose(self) -> ComposeResult:
        tree: Tree[str] = Tree("Bookmarks", id="bookmark_tree")
        tree.show_root = False
        yield Vertical(
            tree,
            Button("Edit bookmarks…", id="btn_edit"),
        )

    def on_mount(self) -> None:
        self._rebuild_tree()
        self._select_default_group()
        self.query_one(Tree).focus()

    def on_focus(self, event: Focus) -> None:
        self.query_one(Tree).focus()

    def _rebuild_tree(self) -> None:
        tree: Tree[str] = self.query_one(Tree)
        tree.clear()
        for group in conf_.bookmarks.groups:
            group_node = tree.root.add(ICONS.get_icon(group.icon).glyph + " " + group.name, expand=True)
            for bookmark in group.bookmarks:
                group_node.add_leaf(ICONS.get_icon(name=bookmark.icon).glyph + " " + bookmark.name, bookmark.path)

    def _select_default_group(self) -> None:
        """Move the tree cursor to the default Bookmarks group, if present."""
        tree: Tree[str] = self.query_one(Tree)
        # Force _build() so all nodes have correct _line values.
        tree._tree_lines  # noqa: B018, RUF100, SLF001
        # Bypass validate_cursor_line (which clamps -1 to 0) so that
        # watch_cursor_line fires even when the target is line 0.
        tree.set_reactive(Tree.cursor_line, -1)  # ty: ignore[invalid-argument-type]
        for node, group in zip(tree.root.children, conf_.bookmarks.groups, strict=False):
            if group.name == DEFAULT_BOOKMARKS_GROUP:
                node.expand()
                tree.move_cursor(node)
                return

    def on_tree_node_selected(self, event: Tree.NodeSelected[str]) -> None:
        bookmark_path = event.node.data
        if bookmark_path is None:
            return
        self.post_message(self.BookmarkSelected(bookmark_path))
        self.close()

    def on_button_pressed(self, event: Button.Pressed) -> None:
        if event.button.id != "btn_edit":
            return

        async def _after_edit(result: str | None) -> None:
            if result == "OK":
                self._rebuild_tree()
                self.query_one(Tree).focus()

        self.app.push_screen(EditBookmarksDialog(conf_.bookmarks), callback=_after_edit)
