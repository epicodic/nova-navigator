"""The 18 editor actions: ids, REQ-4 default keys, and that every call builds fresh objects (DEC-20 A2)."""

from __future__ import annotations

from nova_editor.editor_actions import build_editor_actions
from nova_widgets.key_types import KeySequence

REQ4_DEFAULTS: dict[str, str] = {
    "editor.open": "ctrl+o",
    "editor.save": "ctrl+s",
    "editor.save_as": "ctrl+shift+s",
    "editor.reload": "f5",
    "editor.close": "ctrl+w",
    "editor.quit": "ctrl+q",
    "editor.undo": "ctrl+z",
    "editor.redo": "ctrl+y",
    "editor.cut": "ctrl+x",
    "editor.copy": "ctrl+c",
    "editor.paste": "ctrl+v",
    "editor.select_all": "ctrl+a",
    "editor.find": "ctrl+f",
    "editor.find_next": "f3",
    "editor.find_previous": "shift+f3",
    "editor.goto": "ctrl+g",
    "editor.wrap_mode": "f10",
    "editor.line_numbers": "f11",
}


def test_there_are_exactly_the_eighteen_actions() -> None:
    ids = [action.id for action in build_editor_actions()]
    assert sorted(i for i in ids if i is not None) == sorted(REQ4_DEFAULTS)
    assert len(ids) == 18


def test_every_id_starts_with_editor() -> None:
    assert all(action.id is not None and action.id.startswith("editor.") for action in build_editor_actions())


def test_defaults_equal_the_req4_table() -> None:
    actual = {action.id: action.shortcut for action in build_editor_actions()}
    assert actual == {action_id: KeySequence.parse(key) for action_id, key in REQ4_DEFAULTS.items()}


def test_the_initial_shortcut_equals_the_shortcut() -> None:
    assert all(action.shortcut == action.initial_shortcut for action in build_editor_actions())


def test_only_line_numbers_and_wrap_mode_are_checkable() -> None:
    checkable = {action.id for action in build_editor_actions() if action.checkable}
    assert checkable == {"editor.line_numbers", "editor.wrap_mode"}


def test_every_action_has_a_text_and_an_action_name() -> None:
    for action in build_editor_actions():
        assert action.text
        assert action.action


def test_each_call_builds_new_objects() -> None:
    first = build_editor_actions()
    second = build_editor_actions()
    assert not {id(a) for a in first} & {id(a) for a in second}
    first[0].set_shortcut("ctrl+k")
    assert second[0].shortcut == second[0].initial_shortcut


def test_user_visible_texts_name_no_activity() -> None:
    for action in build_editor_actions():
        text = f"{action.text} {action.description}"
        assert "ACT" not in text
        assert "partial" not in text.lower()
