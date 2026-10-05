"""Menu bar with File, Edit, Search, View menus for the editor screen."""

from __future__ import annotations

from nova_widgets.action import Action
from nova_widgets.menu import MenuBar


def build_menu_bar(actions: dict[str, Action], *, standalone: bool) -> MenuBar:
    """Build the menu bar with the four menus.

    Args:
        actions: Dict of action id → Action object (built by build_editor_actions, keyed by id).
        standalone: `True` for nova_edit, `False` for navigator integration.

    Returns:
        A MenuBar with File, Edit, Search, View menus populated with the given actions.
    """
    menu_bar = MenuBar()

    # File menu
    file_items = [actions["editor.open"], actions["editor.save"], actions["editor.save_as"], actions["editor.reload"]]
    file_items.append(actions["editor.quit"] if standalone else actions["editor.close"])
    menu_bar.add_menu("File", *file_items, name="file")

    # Edit menu
    edit_items = [
        actions["editor.undo"],
        actions["editor.redo"],
        actions["editor.cut"],
        actions["editor.copy"],
        actions["editor.paste"],
        actions["editor.select_all"],
    ]
    menu_bar.add_menu("Edit", *edit_items, name="edit")

    # Search menu
    search_items = [
        actions["editor.find"],
        actions["editor.find_next"],
        actions["editor.find_previous"],
        actions["editor.goto"],
    ]
    menu_bar.add_menu("Search", *search_items, name="search")

    # View menu
    view_items = [
        actions["editor.line_numbers"],
        actions["editor.wrap_mode"],
    ]
    if "editor.keyboard_shortcuts" in actions:
        view_items.append(actions["editor.keyboard_shortcuts"])
    menu_bar.add_menu("View", *view_items, name="view")

    return menu_bar
