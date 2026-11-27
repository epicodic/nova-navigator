"""Menu bar with File, Edit, Search, View menus for the editor screen."""

from __future__ import annotations

from nova_widgets.action import Action
from nova_widgets.menu import MenuBar


def build_menu_bar(actions: dict[str, Action], *, standalone: bool) -> MenuBar:
    """Build the menu bar with the four menus.

    Args:
        actions: Dict of action id → Action object (built by build_editor_actions, keyed by id); an action that is missing is left out of its menu.
        standalone: `True` for nova_edit, `False` for navigator integration.

    Returns:
        A MenuBar with File, Edit, Search, View menus populated with the given actions.
    """
    menu_bar = MenuBar()

    def items(*action_ids: str) -> list[Action]:
        return [actions[i] for i in action_ids if i in actions]

    menu_bar.add_menu("File", *items("editor.open", "editor.save", "editor.save_as", "editor.reload", "editor.quit" if standalone else "editor.close"), name="file")
    menu_bar.add_menu("Edit", *items("editor.undo", "editor.redo", "editor.cut", "editor.copy", "editor.paste", "editor.select_all"), name="edit")
    menu_bar.add_menu("Search", *items("editor.find", "editor.find_next", "editor.find_previous", "editor.goto"), name="search")
    menu_bar.add_menu("View", *items("editor.line_numbers", "editor.wrap_mode", "editor.hex_mode", "editor.keyboard_shortcuts"), name="view")

    return menu_bar
