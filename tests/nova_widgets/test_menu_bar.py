import pytest
from textual.app import App, ComposeResult

from nova_widgets.menu import Menu, MenuBar
from nova_widgets.menu._menu_bar import MenuBarItem


class MenuBarTestApp(App[None]):
    """Minimal app hosting a MenuBar for testing."""

    def __init__(self, bar: MenuBar) -> None:
        super().__init__()
        self._bar = bar

    def compose(self) -> ComposeResult:
        yield self._bar


@pytest.mark.asyncio
async def test_menu_bar_mounts_with_correct_item_count() -> None:
    bar = MenuBar()
    bar.add_menu("File")
    bar.add_menu("Edit")
    bar.add_menu("Help")

    app = MenuBarTestApp(bar)
    async with app.run_test() as pilot:
        await pilot.pause()
        items = app.query(MenuBarItem)
        assert len(items) == 3


@pytest.mark.asyncio
async def test_menu_bar_item_text_matches_menu_title() -> None:
    bar = MenuBar()
    bar.add_menu("File")
    bar.add_menu("Edit")

    app = MenuBarTestApp(bar)
    async with app.run_test() as pilot:
        await pilot.pause()
        items = list(app.query(MenuBarItem))
        assert items[0].menu.text == "File"
        assert items[1].menu.text == "Edit"


@pytest.mark.asyncio
async def test_menu_bar_click_opens_menu() -> None:
    bar = MenuBar()
    file_menu = bar.add_menu("File")
    file_menu.add_action("New", name="new")

    app = MenuBarTestApp(bar)
    async with app.run_test() as pilot:
        await pilot.pause()

        # Click the item to trigger menu opening
        item = app.query(MenuBarItem).first()
        await pilot.click(item)
        await pilot.pause(delay=0.1)

        # The File menu should now be mounted as a child of the MenuBar
        assert len(app.query(Menu)) >= 1


def _active_item(app: App[None]) -> MenuBarItem | None:
    """Return the currently active (highlighted) MenuBarItem, or None."""
    active = [item for item in app.query(MenuBarItem) if item.has_class("-active")]
    return active[0] if active else None


@pytest.mark.asyncio
async def test_menu_bar_right_arrow_while_menu_open_moves_to_next_item() -> None:
    bar = MenuBar()
    bar.add_menu("File")
    bar.add_menu("Edit")
    bar.add_menu("Help")

    app = MenuBarTestApp(bar)
    async with app.run_test() as pilot:
        await pilot.pause()

        # Open "File"
        items = list(app.query(MenuBarItem))
        await pilot.click(items[0])
        await pilot.pause(delay=0.1)
        active_file = _active_item(app)
        assert active_file is not None
        assert active_file.menu.text == "File"

        # Press right → "Edit" should become the active open menu
        await pilot.press("right")
        await pilot.pause(delay=0.1)
        active_after = _active_item(app)
        assert active_after is not None
        assert active_after.menu.text == "Edit"


@pytest.mark.asyncio
async def test_menu_bar_left_arrow_while_menu_open_moves_to_previous_item() -> None:
    bar = MenuBar()
    bar.add_menu("File")
    bar.add_menu("Edit")
    bar.add_menu("Help")

    app = MenuBarTestApp(bar)
    async with app.run_test() as pilot:
        await pilot.pause()

        # Open "Edit" (the middle item)
        items = list(app.query(MenuBarItem))
        await pilot.click(items[1])
        await pilot.pause(delay=0.1)
        active_edit = _active_item(app)
        assert active_edit is not None
        assert active_edit.menu.text == "Edit"

        # Press left → "File" should become the active open menu
        await pilot.press("left")
        await pilot.pause(delay=0.1)
        active_after = _active_item(app)
        assert active_after is not None
        assert active_after.menu.text == "File"
