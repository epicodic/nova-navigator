# tests/widgets/test_manage_bookmarks_dialog.py
from __future__ import annotations

from unittest.mock import patch

import pytest
from textual.app import App, ComposeResult
from textual.containers import Horizontal
from textual.widgets import Button, Input, ListView, Tree

from nova_navigator.config.bookmarks import Bookmark, BookmarkConfig, Group
from nova_navigator.dialogs.manage_bookmarks_dialog import ManageBookmarksDialog, MoveToGroupDialog


class _MoveToGroupApp(App[int | None]):
    def compose(self) -> ComposeResult:
        return iter([])


@pytest.mark.asyncio
async def test_move_to_group_dialog_lists_groups() -> None:
    app = _MoveToGroupApp()
    async with app.run_test() as pilot:
        await pilot.pause()
        await app.push_screen(MoveToGroupDialog(group_names=["Work", "Personal"]))
        await pilot.pause()
        items = list(app.screen.query(ListView))
        assert len(items) == 1
        assert len(items[0]) == 2


@pytest.mark.asyncio
async def test_cancel_button_returns_none() -> None:
    """Cancel button dismisses with None."""
    dismissed: list[int | None] = []

    app = _MoveToGroupApp()
    async with app.run_test() as pilot:
        await pilot.pause()
        await app.push_screen(
            MoveToGroupDialog(group_names=["Work", "Personal"]),
            callback=lambda v: dismissed.append(v),
        )
        await pilot.pause()
        await pilot.click(app.screen.query_one("#mtg_cancel", Button))
        await pilot.pause(0.1)

    assert len(dismissed) == 1
    assert dismissed[0] is None


@pytest.mark.asyncio
async def test_escape_returns_none() -> None:
    """Escape key dismisses with None."""
    dismissed: list[int | None] = []

    app = _MoveToGroupApp()
    async with app.run_test() as pilot:
        await pilot.pause()
        await app.push_screen(
            MoveToGroupDialog(group_names=["Work", "Personal"]),
            callback=lambda v: dismissed.append(v),
        )
        await pilot.pause()
        await pilot.press("escape")
        await pilot.pause(0.1)

    assert len(dismissed) == 1
    assert dismissed[0] is None


@pytest.mark.asyncio
async def test_ok_with_selection_returns_index() -> None:
    """OK button with a selection dismisses with the selected index."""
    dismissed: list[int | None] = []

    app = _MoveToGroupApp()
    async with app.run_test() as pilot:
        await pilot.pause()
        await app.push_screen(
            MoveToGroupDialog(group_names=["Work", "Personal"]),
            callback=lambda v: dismissed.append(v),
        )
        await pilot.pause()
        # ListView highlights index 0 by default; click OK
        await pilot.click(app.screen.query_one("#mtg_ok", Button))
        await pilot.pause(0.1)

    assert len(dismissed) == 1
    assert dismissed[0] == 0


# ---------------------------------------------------------------------------
# ManageBookmarksDialog tests
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
    dialog = ManageBookmarksDialog(cfg)

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
    dialog = ManageBookmarksDialog(cfg)

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
    dialog = ManageBookmarksDialog(cfg)

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


def _make_dialog_app(cfg: BookmarkConfig) -> tuple[ManageBookmarksDialog, type[App[None]]]:
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
            await pilot.click(app.screen.query_one("#btn_ok", Button))
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
        await pilot.click(app.screen.query_one("#btn_cancel", Button))
        await pilot.pause(0.1)

    # original cfg must be unchanged
    assert len(cfg.groups) == original_group_count
    assert [g.name for g in cfg.groups] == original_names
