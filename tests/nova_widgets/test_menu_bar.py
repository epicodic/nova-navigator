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

        # Simulate the mouse-down that triggers menu opening
        item = app.query(MenuBarItem).first()
        item.post_message(MenuBarItem.Selected(item))
        await pilot.pause(delay=0.1)

        # The File menu should now be mounted as a child of the MenuBar
        assert len(app.query(Menu)) >= 1
