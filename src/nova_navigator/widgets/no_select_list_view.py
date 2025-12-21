from textual.widgets import ListItem, ListView


class NoSelectListView(ListView, can_focus=False):
    """A ListView that does not allow selection of its items."""

    ListItem = ListItem

    def watch_index(self, old_index: int | None, new_index: int | None) -> None:
        if new_index is not None:
            self.index = None
