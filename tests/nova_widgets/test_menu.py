import pytest

from nova_widgets.menu import Menu
from tests.nova_widgets._menu_test_app import MenuTestApp


@pytest.mark.asyncio
async def test_menu_mounts_successfully() -> None:
    menu = Menu()
    menu.add_action("Item A", name="item_a")
    menu.add_action("Item B", name="item_b")

    app = MenuTestApp(menu)
    async with app.run_test() as pilot:
        await pilot.pause()
        # Menu is present in the DOM
        assert len(app.query(Menu)) == 1


@pytest.mark.asyncio
async def test_menu_down_key_highlights_first_item() -> None:
    menu = Menu()
    menu.add_action("Item A", name="item_a")
    menu.add_action("Item B", name="item_b")

    app = MenuTestApp(menu)
    async with app.run_test() as pilot:
        assert menu._highlighted is None
        await pilot.press("down")
        await pilot.pause()
        assert menu._highlighted == 0


@pytest.mark.asyncio
async def test_menu_up_key_highlights_last_item() -> None:
    menu = Menu()
    menu.add_action("Item A", name="item_a")
    menu.add_action("Item B", name="item_b")

    app = MenuTestApp(menu)
    async with app.run_test() as pilot:
        await pilot.press("up")
        await pilot.pause()
        # _next_highlighted(None, -1) starts at last index
        assert menu._highlighted == 1


@pytest.mark.asyncio
async def test_menu_down_then_down_highlights_second_item() -> None:
    menu = Menu()
    menu.add_action("Item A", name="item_a")
    menu.add_action("Item B", name="item_b")

    app = MenuTestApp(menu)
    async with app.run_test() as pilot:
        await pilot.press("down")
        await pilot.press("down")
        await pilot.pause()
        assert menu._highlighted == 1


@pytest.mark.asyncio
async def test_menu_enter_triggers_highlighted_action() -> None:
    menu = Menu()
    action_a = menu.add_action("Item A", name="item_a")
    menu.add_action("Item B", name="item_b")

    app = MenuTestApp(menu)
    async with app.run_test() as pilot:
        await pilot.press("down")  # highlight index 0 (Item A)
        await pilot.press("enter")
        await pilot.pause()

    assert len(app.triggered) == 1
    assert app.triggered[0] is action_a


@pytest.mark.asyncio
async def test_menu_enter_with_no_highlight_does_not_trigger() -> None:
    menu = Menu()
    menu.add_action("Item A", name="item_a")

    app = MenuTestApp(menu)
    async with app.run_test() as pilot:
        # Do NOT press down — highlighted is None
        await pilot.press("enter")
        await pilot.pause()

    assert len(app.triggered) == 0


@pytest.mark.asyncio
async def test_menu_escape_removes_menu_from_dom() -> None:
    menu = Menu()
    menu.add_action("Item A", name="item_a")

    app = MenuTestApp(menu)
    async with app.run_test() as pilot:
        await pilot.press("escape")
        await pilot.pause()
        assert len(app.query(Menu)) == 0


@pytest.mark.asyncio
async def test_menu_disabled_item_is_skipped_during_navigation() -> None:
    menu = Menu()
    menu.add_action("Skip Me", name="skip", enabled=False)
    menu.add_action("Get Me", name="get")

    app = MenuTestApp(menu)
    async with app.run_test() as pilot:
        await pilot.press("down")  # starts at index 0 (disabled) → advances to index 1
        await pilot.pause()
        # Disabled item at index 0 is skipped; highlighted lands on index 1
        assert menu._highlighted == 1


@pytest.mark.asyncio
async def test_menu_enter_on_checkable_item_toggles_checked_to_true() -> None:
    menu = Menu()
    checkable = menu.add_action("Bold", name="bold", checkable=True)

    app = MenuTestApp(menu)
    async with app.run_test() as pilot:
        await pilot.press("down")  # highlight index 0 (Bold)
        await pilot.press("enter")  # trigger → _triggered toggles checked before posting event
        await pilot.pause()

    # After triggering, the action's checked state is toggled in _triggered()
    assert checkable.checked is True
    assert len(app.triggered) == 1
    assert app.triggered[0] is checkable


@pytest.mark.asyncio
async def test_right_arrow_opens_submenu() -> None:
    sub = Menu("Submenu")
    sub.add_action("Sub Item", name="sub_item")

    menu = Menu()
    menu.add_action("Before", name="before")
    menu.add(sub)

    app = MenuTestApp(menu)
    async with app.run_test() as pilot:
        await pilot.press("down")  # highlight index 0 (Before)
        await pilot.press("down")  # highlight index 1 (Submenu)
        await pilot.press("right")  # open submenu
        await pilot.pause()
        # Parent menu has the submenu registered as opened
        assert menu._opened_submenu is sub
        # The submenu widget is now in the DOM
        menus = list(app.query(Menu))
        assert sub in menus


@pytest.mark.asyncio
async def test_enter_on_submenu_item_opens_submenu() -> None:
    sub = Menu("Submenu")
    sub.add_action("Sub Item", name="sub_item")

    menu = Menu()
    menu.add(sub)

    app = MenuTestApp(menu)
    async with app.run_test() as pilot:
        await pilot.press("down")  # highlight index 0 (Submenu)
        await pilot.press("enter")  # enter also opens a submenu item
        await pilot.pause()
        assert menu._opened_submenu is sub


@pytest.mark.asyncio
async def test_left_arrow_closes_submenu_and_returns_focus_to_parent() -> None:
    sub = Menu("Submenu")
    sub.add_action("Sub Item", name="sub_item")

    menu = Menu()
    menu.add_action("Before", name="before")
    menu.add(sub)

    app = MenuTestApp(menu)
    async with app.run_test() as pilot:
        await pilot.press("down")  # highlight index 1 (Submenu)
        await pilot.press("down")
        await pilot.press("right")  # open submenu — focus is now on sub
        await pilot.pause()
        assert menu._opened_submenu is sub

        await pilot.press("left")  # close submenu — focus returns to parent
        await pilot.pause()
        assert menu._opened_submenu is None
        # Submenu is removed from DOM
        assert sub not in list(app.query(Menu))


@pytest.mark.asyncio
async def test_triggering_item_in_submenu_fires_triggered_on_app() -> None:
    sub = Menu("Submenu")
    sub_action = sub.add_action("Sub Item", name="sub_item")

    menu = Menu()
    menu.add(sub)

    app = MenuTestApp(menu)
    async with app.run_test() as pilot:
        await pilot.press("down")  # highlight the Submenu item
        await pilot.press("right")  # open submenu
        await pilot.pause()
        await pilot.press("down")  # highlight "Sub Item" inside the submenu
        await pilot.press("enter")  # trigger it
        await pilot.pause()

    assert len(app.triggered) == 1
    assert app.triggered[0] is sub_action
