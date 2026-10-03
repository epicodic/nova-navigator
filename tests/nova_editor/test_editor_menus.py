"""The four menus in order, with the REQ-2 items, built from the very objects they are given."""

from __future__ import annotations

from nova_editor.editor_actions import build_editor_actions
from nova_editor.editor_menus import build_menu_bar
from nova_widgets.menu import Menu


def test_build_menu_bar_takes_action_dict_and_returns_menubar() -> None:
    actions = {a.id: a for a in build_editor_actions() if a.id is not None}
    menu_bar = build_menu_bar(actions, standalone=True)
    assert menu_bar is not None
    assert hasattr(menu_bar, "add_menu")


def test_four_menus_in_order() -> None:
    actions = {a.id: a for a in build_editor_actions() if a.id is not None}
    menu_bar = build_menu_bar(actions, standalone=True)
    menus = list(menu_bar.actions)
    assert len(menus) == 4
    assert menus[0].text == "File"
    assert menus[1].text == "Edit"
    assert menus[2].text == "Search"
    assert menus[3].text == "View"


def test_file_menu_items() -> None:
    actions = {a.id: a for a in build_editor_actions() if a.id is not None}
    menu_bar = build_menu_bar(actions, standalone=True)
    file_menu: Menu = next(iter(menu_bar.actions))
    items = list(file_menu.actions)
    ids = [a.id for a in items if a.id]
    assert ids == ["editor.open", "editor.save", "editor.save_as", "editor.reload", "editor.close", "editor.quit"]


def test_edit_menu_items() -> None:
    actions = {a.id: a for a in build_editor_actions() if a.id is not None}
    menu_bar = build_menu_bar(actions, standalone=True)
    menus = list(menu_bar.actions)
    edit_menu: Menu = menus[1]
    items = list(edit_menu.actions)
    ids = [a.id for a in items if a.id]
    assert ids == ["editor.undo", "editor.redo", "editor.cut", "editor.copy", "editor.paste", "editor.select_all"]


def test_search_menu_items() -> None:
    actions = {a.id: a for a in build_editor_actions() if a.id is not None}
    menu_bar = build_menu_bar(actions, standalone=True)
    menus = list(menu_bar.actions)
    search_menu: Menu = menus[2]
    items = list(search_menu.actions)
    ids = [a.id for a in items if a.id]
    assert ids == ["editor.find", "editor.find_next", "editor.find_previous", "editor.goto"]


def test_view_menu_items() -> None:
    actions = {a.id: a for a in build_editor_actions() if a.id is not None}
    menu_bar = build_menu_bar(actions, standalone=True)
    menus = list(menu_bar.actions)
    view_menu: Menu = menus[3]
    items = list(view_menu.actions)
    ids = [a.id for a in items if a.id]
    assert ids == ["editor.line_numbers", "editor.wrap_mode"]


def test_menu_items_are_the_very_action_objects_passed_in() -> None:
    """Critical test (DEC-20): menu items must be the same objects, not copies."""
    actions = {a.id: a for a in build_editor_actions() if a.id is not None}
    menu_bar = build_menu_bar(actions, standalone=True)
    file_menu: Menu = next(iter(menu_bar.actions))
    save_item = next(a for a in file_menu.actions if a.id == "editor.save")
    assert save_item is actions["editor.save"]
