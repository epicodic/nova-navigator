"""Tests for ActionsSupport mixin."""

from typing import ClassVar

import pytest

from nova_widgets.action import Action
from nova_widgets.actions_support import ActionsSupport


class SimpleWidget(ActionsSupport):
    ACTIONS: ClassVar[list[Action]] = [
        Action("Open", id="open"),
        Action("Close", id="close"),
    ]


class WidgetWithNoActions(ActionsSupport):
    pass


class ChildWidget(SimpleWidget):
    ACTIONS: ClassVar[list[Action]] = [
        Action("Save", id="save"),
    ]


def test_act_returns_action_by_name() -> None:
    w = SimpleWidget()
    a = w._act("open")
    assert a.text == "Open"


def test_act_returns_action_by_name_close() -> None:
    w = SimpleWidget()
    a = w._act("close")
    assert a.text == "Close"


def test_act_unknown_name_raises_key_error() -> None:
    w = SimpleWidget()
    with pytest.raises(KeyError):
        w._act("nonexistent")


def test_act_child_inherits_parent_actions() -> None:
    w = ChildWidget()
    a = w._act("open")  # from parent
    assert a.text == "Open"


def test_act_child_own_action() -> None:
    w = ChildWidget()
    a = w._act("save")  # from child
    assert a.text == "Save"


def test_widget_with_no_actions_raises_key_error() -> None:
    w = WidgetWithNoActions()
    with pytest.raises(KeyError):
        w._act("anything")


def test_actions_by_id_is_class_var() -> None:
    """Two instances share the same dict — it's a ClassVar, not per-instance."""
    w1 = SimpleWidget()
    w2 = SimpleWidget()
    assert w1._actions_by_id is w2._actions_by_id


def test_child_actions_by_id_does_not_equal_parent() -> None:
    """Child has its own dict that includes parent actions but is separate."""
    assert SimpleWidget._actions_by_id is not ChildWidget._actions_by_id
    assert "save" in ChildWidget._actions_by_id
    assert "open" in ChildWidget._actions_by_id


class PassthroughWidget(SimpleWidget):
    pass  # no ACTIONS declared


class WidgetWithUnnamedAction(ActionsSupport):
    ACTIONS: ClassVar[list[Action]] = [
        Action("Separator", is_separator=True),  # name is None
        Action("Open", id="open"),
    ]


def test_passthrough_inherits_parent_actions() -> None:
    w = PassthroughWidget()
    assert w._act("open").text == "Open"
    assert w._act("close").text == "Close"


def test_unnamed_actions_are_skipped() -> None:
    w = WidgetWithUnnamedAction()
    a = w._act("open")
    assert a.text == "Open"
    assert len(w._actions_by_id) == 1  # separator not included
