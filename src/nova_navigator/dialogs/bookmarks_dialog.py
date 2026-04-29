from textual.app import ComposeResult
from textual.events import Focus
from textual.message import Message
from textual.widgets import Tree

from ..config import conf_
from ..icons import ICONS
from ..widgets.popup_widget import PopupWidget


class BookmarksDialog(PopupWidget, can_focus=True):
    """Bookmarks dialog overlay widget."""

    DEFAULT_CSS = """
        BookmarksDialog {
            width: 40;
            height: 20;
        }
    """

    class BookmarkSelected(Message):
        def __init__(self, bookmark_path: str) -> None:
            super().__init__()
            self.bookmark_path = bookmark_path

    _tree_widget: Tree[str]

    def __init__(self, position: tuple[int, int]) -> None:
        super().__init__("Bookmarks", position, close_action=PopupWidget.CloseAction.REMOVE)
        self._tree_widget = Tree("Bookmarks")
        self._tree_widget.show_root = False

        for group in conf_.bookmarks.groups:
            group_node = self._tree_widget.root.add(ICONS.get_icon(group.icon) + " " + group.name, expand=True)
            for bookmark in group.bookmarks:
                group_node.add_leaf(ICONS.get_icon(name=bookmark.icon) + " " + bookmark.name, bookmark.path)

    def compose(self) -> ComposeResult:
        yield self._tree_widget

    def on_focus(self, event: Focus) -> None:
        self._tree_widget.focus()

    def on_tree_node_selected(self, event: Tree.NodeSelected[str]) -> None:
        bookmark_path = event.node.data
        if bookmark_path is None:
            return

        self.post_message(self.BookmarkSelected(bookmark_path))
        self.close()
