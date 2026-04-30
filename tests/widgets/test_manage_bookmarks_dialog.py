# tests/widgets/test_manage_bookmarks_dialog.py
from __future__ import annotations

from unittest.mock import patch

import pytest
from textual.app import App, ComposeResult
from textual.containers import Horizontal
from textual.widgets import Button, Input, Tree

from nova_navigator.config.bookmarks import Bookmark, BookmarkConfig, Group
from nova_navigator.dialogs.edit_bookmarks_dialog import EditBookmarksDialog

# ---------------------------------------------------------------------------
# EditBookmarksDialog tests
# ---------------------------------------------------------------------------


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


@pytest.mark.asyncio
async def test_tree_shows_groups_and_entries() -> None:
    cfg = _fixture_config()
    dialog = EditBookmarksDialog(cfg)

    class _App(App[None]):
        def compose(self) -> ComposeResult:
            return iter([])

        async def on_mount(self) -> None:
            await self.push_screen(dialog)

    app = _App()
    async with app.run_test() as pilot:
        await pilot.pause()
        tree = app.screen.query_one(Tree)
        # root has 2 group children
        assert len(list(tree.root.children)) == 2
        # first group has 2 entry children
        first_group = next(iter(tree.root.children))
        assert len(list(first_group.children)) == 2


@pytest.mark.asyncio
async def test_form_path_hidden_for_group_selection() -> None:
    cfg = _fixture_config()
    dialog = EditBookmarksDialog(cfg)

    class _App(App[None]):
        def compose(self) -> ComposeResult:
            return iter([])

        async def on_mount(self) -> None:
            await self.push_screen(dialog)

    app = _App()
    async with app.run_test() as pilot:
        await pilot.pause()
        tree = app.screen.query_one(Tree)
        # select the first group node
        tree.focus()
        await pilot.press("down")  # moves cursor from -1 to line 0 (first group)
        await pilot.pause()
        form_row_path = app.screen.query_one("#form_row_path", Horizontal)
        assert not form_row_path.display


@pytest.mark.asyncio
async def test_form_path_visible_for_entry_selection() -> None:
    cfg = _fixture_config()
    dialog = EditBookmarksDialog(cfg)

    class _App(App[None]):
        def compose(self) -> ComposeResult:
            return iter([])

        async def on_mount(self) -> None:
            await self.push_screen(dialog)

    app = _App()
    async with app.run_test() as pilot:
        await pilot.pause()
        tree = app.screen.query_one(Tree)
        # select first entry of first group (line 1 with show_root=False: group=0, entry=1)
        tree.focus()
        await pilot.press("down")  # line 0: first group
        await pilot.press("down")  # line 1: first entry of first group
        await pilot.pause()
        path_input = app.screen.query_one("#input_path", Input)
        assert path_input.display


# ---------------------------------------------------------------------------
# Mutation tests — add, remove, reorder, move-to-group
# ---------------------------------------------------------------------------


def _make_dialog_app(cfg: BookmarkConfig) -> tuple[EditBookmarksDialog, type[App[None]]]:
    dialog = EditBookmarksDialog(cfg)

    class _App(App[None]):
        def compose(self) -> ComposeResult:
            return iter([])

        async def on_mount(self) -> None:
            await self.push_screen(dialog)

    # The dialog has 6 action buttons (~18 rows) so the test terminal must be
    # tall enough that they don't visually overlap the form input rows below.
    _App.run_test = lambda self, **kw: App.run_test(self, size=kw.pop("size", (80, 40)), **kw)  # type: ignore[method-assign]

    return dialog, _App


@pytest.mark.asyncio
async def test_add_group() -> None:
    cfg = _fixture_config()
    dialog, _App = _make_dialog_app(cfg)
    app = _App()
    async with app.run_test() as pilot:
        await pilot.pause()
        await pilot.click(app.screen.query_one("#btn_add_group", Button))
        await pilot.pause()
        tree = app.screen.query_one(Tree)
        assert len(list(tree.root.children)) == 3
        assert dialog._working.groups[-1].name == "New Group"


@pytest.mark.asyncio
async def test_add_entry() -> None:
    cfg = _fixture_config()
    dialog, _App = _make_dialog_app(cfg)
    app = _App()
    async with app.run_test() as pilot:
        await pilot.pause()
        # navigate to first group
        tree = app.screen.query_one(Tree)
        tree.focus()
        await pilot.press("down")
        await pilot.pause()
        await pilot.click(app.screen.query_one("#btn_add_entry", Button))
        await pilot.pause()
        assert len(dialog._working.groups[0].bookmarks) == 3
        assert dialog._working.groups[0].bookmarks[-1].name == "New Entry"


@pytest.mark.asyncio
async def test_remove_entry() -> None:
    cfg = _fixture_config()
    dialog, _App = _make_dialog_app(cfg)
    app = _App()
    async with app.run_test() as pilot:
        await pilot.pause()
        tree = app.screen.query_one(Tree)
        tree.focus()
        await pilot.press("down")  # group
        await pilot.press("down")  # first entry (Home)
        await pilot.pause()
        await pilot.click(app.screen.query_one("#btn_remove", Button))
        await pilot.pause()
        assert len(dialog._working.groups[0].bookmarks) == 1
        assert dialog._working.groups[0].bookmarks[0].name == "Docs"


@pytest.mark.asyncio
async def test_remove_group() -> None:
    cfg = _fixture_config()
    dialog, _App = _make_dialog_app(cfg)
    app = _App()
    async with app.run_test() as pilot:
        await pilot.pause()
        tree = app.screen.query_one(Tree)
        tree.focus()
        await pilot.press("down")  # first group (Computer)
        await pilot.press("down")  # first entry
        await pilot.press("down")  # second entry
        await pilot.press("down")  # second group (Work)
        await pilot.pause()
        await pilot.click(app.screen.query_one("#btn_remove", Button))
        await pilot.pause()
        assert len(dialog._working.groups) == 1
        assert dialog._working.groups[0].name == "Computer"


@pytest.mark.asyncio
async def test_move_entry_up() -> None:
    cfg = _fixture_config()
    dialog, _App = _make_dialog_app(cfg)
    app = _App()
    async with app.run_test() as pilot:
        await pilot.pause()
        tree = app.screen.query_one(Tree)
        tree.focus()
        await pilot.press("down")  # group
        await pilot.press("down")  # entry 0 (Home)
        await pilot.press("down")  # entry 1 (Docs)
        await pilot.pause()
        await pilot.click(app.screen.query_one("#btn_move_up", Button))
        await pilot.pause()
        assert dialog._working.groups[0].bookmarks[0].name == "Docs"
        assert dialog._working.groups[0].bookmarks[1].name == "Home"


@pytest.mark.asyncio
async def test_move_entry_down() -> None:
    cfg = _fixture_config()
    dialog, _App = _make_dialog_app(cfg)
    app = _App()
    async with app.run_test() as pilot:
        await pilot.pause()
        tree = app.screen.query_one(Tree)
        tree.focus()
        await pilot.press("down")  # group
        await pilot.press("down")  # entry 0 (Home)
        await pilot.pause()
        await pilot.press("alt+down")
        await pilot.pause()
        assert dialog._working.groups[0].bookmarks[0].name == "Docs"
        assert dialog._working.groups[0].bookmarks[1].name == "Home"


@pytest.mark.asyncio
async def test_move_group_up() -> None:
    cfg = _fixture_config()
    dialog, _App = _make_dialog_app(cfg)
    app = _App()
    async with app.run_test() as pilot:
        await pilot.pause()
        tree = app.screen.query_one(Tree)
        tree.focus()
        await pilot.press("down")  # group 0 (Computer)
        await pilot.press("down")  # entry
        await pilot.press("down")  # entry
        await pilot.press("down")  # group 1 (Work)
        await pilot.pause()
        await pilot.click(app.screen.query_one("#btn_move_up", Button))
        await pilot.pause()
        assert dialog._working.groups[0].name == "Work"
        assert dialog._working.groups[1].name == "Computer"


# ---------------------------------------------------------------------------
# Persistence tests — OK saves, Cancel does not
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_ok_saves_to_config() -> None:
    """OK button writes _working.groups back to the config and calls save()."""
    cfg = _fixture_config()
    save_called = False
    saved_groups: list | None = None

    def mock_save(self_: object) -> None:
        nonlocal save_called, saved_groups
        save_called = True
        assert isinstance(self_, BookmarkConfig)
        saved_groups = list(self_.groups)

    _dialog, _App = _make_dialog_app(cfg)
    app = _App()

    with patch.object(BookmarkConfig, "save", mock_save):
        async with app.run_test(size=(80, 40)) as pilot:
            await pilot.pause()
            # add a group so we can detect the change
            await pilot.click(app.screen.query_one("#btn_add_group", Button))
            await pilot.pause()
            # press OK
            await pilot.click(app.screen.query_one("#OK", Button))
            await pilot.pause(0.1)

    assert save_called, "save() was not called"
    assert saved_groups is not None
    assert len(saved_groups) == 3  # 2 original + 1 new


@pytest.mark.asyncio
async def test_cancel_does_not_modify_config() -> None:
    """Cancel leaves the original config unchanged."""
    cfg = _fixture_config()
    original_group_count = len(cfg.groups)
    original_names = [g.name for g in cfg.groups]

    _dialog, _App = _make_dialog_app(cfg)
    app = _App()

    async with app.run_test(size=(80, 40)) as pilot:
        await pilot.pause()
        # add a group (mutation in working copy only)
        await pilot.click(app.screen.query_one("#btn_add_group", Button))
        await pilot.pause()
        # press Cancel
        await pilot.click(app.screen.query_one("#CANCEL", Button))
        await pilot.pause(0.1)

    # original cfg must be unchanged
    assert len(cfg.groups) == original_group_count
    assert [g.name for g in cfg.groups] == original_names


@pytest.mark.asyncio
async def test_tree_updates_after_name_input_loses_focus() -> None:
    """Editing the name input and tabbing away should update the tree label."""
    cfg = BookmarkConfig(groups=[Group(name="Computer", bookmarks=[Bookmark(name="Home", path="/home/user")])])
    _dialog, _App = _make_dialog_app(cfg)
    app = _App()
    async with app.run_test() as pilot:
        await pilot.pause()
        tree = app.screen.query_one(Tree)
        tree.focus()
        await pilot.press("down")  # select group
        await pilot.press("down")  # select entry
        await pilot.pause()

        # type a new name into the name input
        inp = app.screen.query_one("#input_name", Input)
        inp.focus()
        await pilot.pause()
        await pilot.press("ctrl+a")
        for _ in inp.value:
            await pilot.press("backspace")
        for ch in "My Home":
            await pilot.press(ch)
        await pilot.pause()

        # blur the input by pressing Tab
        await pilot.press("tab")
        await pilot.pause()

        # tree label should reflect the new name
        entry_node = next(iter(next(iter(tree.root.children)).children))
        assert "My Home" in str(entry_node.label)


# ---------------------------------------------------------------------------
# prefill= constructor tests
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_prefill_adds_entry_to_existing_group() -> None:
    """prefill= appends an entry to the named group and pre-fills the form on mount."""
    cfg = _fixture_config()
    dialog = EditBookmarksDialog(cfg, prefill=("Computer", "Desktop", "/home/user/Desktop"))

    class _App(App[None]):
        def compose(self) -> ComposeResult:
            return iter([])

        async def on_mount(self) -> None:
            await self.push_screen(dialog)

    _App.run_test = lambda self, **kw: App.run_test(self, size=kw.pop("size", (80, 40)), **kw)  # type: ignore[method-assign]

    app = _App()
    async with app.run_test() as pilot:
        await pilot.pause()

        computer = dialog._working.groups[0]
        assert len(computer.bookmarks) == 3
        assert computer.bookmarks[-1].name == "Desktop"
        assert computer.bookmarks[-1].path == "/home/user/Desktop"

        assert app.screen.query_one("#input_name", Input).value == "Desktop"
        assert app.screen.query_one("#input_path", Input).value == "/home/user/Desktop"


@pytest.mark.asyncio
async def test_prefill_creates_new_group_if_missing() -> None:
    """prefill= creates the named group when it does not exist."""
    cfg = _fixture_config()
    dialog = EditBookmarksDialog(cfg, prefill=("New Group", "Server", "/mnt/server"))

    class _App(App[None]):
        def compose(self) -> ComposeResult:
            return iter([])

        async def on_mount(self) -> None:
            await self.push_screen(dialog)

    _App.run_test = lambda self, **kw: App.run_test(self, size=kw.pop("size", (80, 40)), **kw)  # type: ignore[method-assign]

    app = _App()
    async with app.run_test() as pilot:
        await pilot.pause()

        assert len(dialog._working.groups) == 3
        new_group = dialog._working.groups[-1]
        assert new_group.name == "New Group"
        assert len(new_group.bookmarks) == 1
        assert new_group.bookmarks[0].name == "Server"
        assert new_group.bookmarks[0].path == "/mnt/server"

        assert app.screen.query_one("#input_name", Input).value == "Server"
        assert app.screen.query_one("#input_path", Input).value == "/mnt/server"
