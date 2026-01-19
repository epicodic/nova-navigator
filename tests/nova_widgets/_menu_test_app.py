from textual.app import App, ComposeResult

from nova_widgets.menu import Action, Menu


class MenuTestApp(App[None]):
    """Minimal Textual app that mounts a single Menu and records events.

    Usage::

        menu = Menu()
        menu.add_action("Item A", name="item_a")
        async with MenuTestApp(menu).run_test() as pilot:
            await pilot.press("down")
            await pilot.press("enter")
            await pilot.pause()
        assert len(app.triggered) == 1

    Attributes:
        triggered: Actions received via Menu.Triggered, in order.
        dismissed: True once Menu.Dismissed has been received.
    """

    def __init__(self, menu: Menu) -> None:
        super().__init__()
        self._menu = menu
        self.triggered: list[Action] = []
        self.dismissed: bool = False

    def compose(self) -> ComposeResult:
        yield self._menu

    def on_mount(self) -> None:
        self._menu.focus()

    def on_menu_triggered(self, event: Menu.Triggered) -> None:
        self.triggered.append(event.action)

    def on_menu_dismissed(self, event: Menu.Dismissed) -> None:
        self.dismissed = True
