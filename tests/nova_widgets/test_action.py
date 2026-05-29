import pytest

from nova_widgets.action import Action, ActionCollection, ActionGroup


def test_action_default_enabled() -> None:
    a = Action("Open")
    assert a.enabled is True


def test_action_default_not_checkable() -> None:
    a = Action("Open")
    assert a.checkable is False


def test_action_default_not_checked() -> None:
    a = Action("Open")
    assert a.checked is False


def test_action_default_not_separator() -> None:
    a = Action("Open")
    assert a.is_separator is False


def test_action_default_no_group() -> None:
    a = Action("Open")
    assert a.group is None


def test_action_default_no_name() -> None:
    a = Action("Open")
    assert a.id is None


def test_action_default_no_shortcut() -> None:
    a = Action("Open")
    assert a.shortcut is None


def test_action_default_no_icon() -> None:
    a = Action("Open")
    assert a.icon is None


def test_action_set_enabled_false() -> None:
    a = Action("Open")
    a.set_enabled(False)
    assert a.enabled is False


def test_action_set_enabled_true_after_false() -> None:
    a = Action("Open")
    a.set_enabled(False)
    a.set_enabled(True)
    assert a.enabled is True


def test_action_set_checked_on_checkable_action() -> None:
    a = Action("Bold", checkable=True)
    a.set_checked(True)
    assert a.checked is True


def test_action_set_checked_false_on_checkable_action() -> None:
    a = Action("Bold", checkable=True, checked=True)
    a.set_checked(False)
    assert a.checked is False


def test_action_checked_false_when_not_checkable() -> None:
    # checked=True in constructor has no effect when checkable=False
    a = Action("Bold", checkable=False, checked=True)
    assert a.checked is False


def test_action_set_checked_raises_when_not_checkable() -> None:
    a = Action("Open")
    with pytest.raises(AssertionError):
        a.set_checked(True)


def test_action_default_description_empty() -> None:
    a = Action("Open")
    assert a.description == ""


def test_action_default_show_in_bar_false() -> None:
    a = Action("Open")
    assert a.show_in_bar is False


def test_action_default_bar_priority_100() -> None:
    a = Action("Open")
    assert a.bar_priority == 100


def test_action_custom_description() -> None:
    a = Action("Copy", description="Copy files to the other panel")
    assert a.description == "Copy files to the other panel"


def test_action_shortcut_set_from_constructor() -> None:
    a = Action("Copy", shortcut="f5")
    assert str(a.shortcut) == "f5"


def test_action_show_in_bar_true() -> None:
    a = Action("Copy", show=True)
    assert a.show_in_bar is True


def test_action_custom_bar_priority() -> None:
    a = Action("Copy", bar_priority=10)
    assert a.bar_priority == 10


def test_action_set_shortcut() -> None:
    a = Action("Copy", shortcut="f5")
    a.set_shortcut("f6")
    assert str(a.shortcut) == "f6"


def test_action_set_shortcut_none() -> None:
    a = Action("Copy", shortcut="f5")
    a.set_shortcut(None)
    assert a.shortcut is None


def test_action_reset_shortcut() -> None:
    a = Action("Copy", shortcut="f5")
    a.set_shortcut("f6")
    a.reset_shortcut()
    assert str(a.shortcut) == "f5"


def test_action_reset_shortcut_to_none_when_no_initial() -> None:
    a = Action("Open")
    a.set_shortcut("f5")
    a.reset_shortcut()
    assert a.shortcut is None


def test_action_initial_shortcut_preserved_after_set_shortcut() -> None:
    a = Action("Copy", shortcut="f5")
    a.set_shortcut("f9")
    assert str(a.initial_shortcut) == "f5"
    assert str(a.shortcut) == "f9"


def test_action_separator_has_no_text_required() -> None:
    a = Action(is_separator=True)
    assert a.is_separator is True
    assert a.text == ""


def test_action_name_falls_back_to_action_string() -> None:
    a = Action("New", action="app.new_file")
    assert a.id == "app.new_file"


def test_action_explicit_name_overrides_action_string() -> None:
    a = Action("New", id="new", action="app.new_file")
    assert a.id == "new"


def test_action_group_checking_one_unchecks_other() -> None:
    a1 = Action("Option A", checkable=True)
    a2 = Action("Option B", checkable=True)
    group = ActionGroup(a1, a2)
    a1.set_group(group)
    a2.set_group(group)

    a1.set_checked(True)
    assert a1.checked is True
    assert a2.checked is False

    a2.set_checked(True)
    assert a2.checked is True
    assert a1.checked is False


def test_action_group_current_returns_checked_action() -> None:
    a1 = Action("Option A", checkable=True)
    a2 = Action("Option B", checkable=True)
    group = ActionGroup(a1, a2)
    a1.set_group(group)
    a2.set_group(group)

    a1.set_checked(True)
    assert group.current() is a1

    a2.set_checked(True)
    assert group.current() is a2


def test_action_group_current_none_before_any_check() -> None:
    a1 = Action("Option A", checkable=True)
    group = ActionGroup(a1)
    a1.set_group(group)
    assert group.current() is None


def test_action_collection_find_action_by_name() -> None:
    coll = ActionCollection()
    a = Action("Open", id="open")
    coll._add_action(a)
    assert coll.find_action("open") is a


def test_action_collection_find_action_returns_none_when_missing() -> None:
    coll = ActionCollection()
    coll._add_action(Action("Open", id="open"))
    assert coll.find_action("missing") is None


def test_action_collection_actions_property_returns_all() -> None:
    coll = ActionCollection()
    a1 = Action("Open", id="open")
    a2 = Action("Save", id="save")
    coll._add_action(a1)
    coll._add_action(a2)
    assert coll.actions == [a1, a2]
