"""The editor's key actions as rows of the navigator's keybindings dialog (REQ-7)."""

from __future__ import annotations

from nova_editor.editor_actions import build_editor_actions
from nova_navigator.embedded_editor_keys import EDITOR_LABEL_PREFIX, editor_key_actions
from nova_navigator.nova_navigator import MainScreen


def test_every_editor_action_is_listed_with_its_id_default_key_and_description() -> None:
    editor = build_editor_actions()
    rows = editor_key_actions()
    assert [a.id for a in rows] == [a.id for a in editor]
    assert [a.initial_shortcut for a in rows] == [a.initial_shortcut for a in editor]
    assert [a.description for a in rows] == [a.description for a in editor]
    assert len(rows) == 18


def test_the_labels_are_prefixed_so_they_differ_from_the_navigators() -> None:
    rows = editor_key_actions()
    assert all(a.text.startswith(EDITOR_LABEL_PREFIX) for a in rows)
    navigator_labels = {a.text for a in MainScreen.ACTIONS}
    assert not navigator_labels & {a.text for a in rows}
    assert "Editor: Save As…" in {a.text for a in rows}


def test_the_ids_do_not_collide_with_the_navigators() -> None:
    navigator_ids = {a.id for a in MainScreen.ACTIONS}
    assert not navigator_ids & {a.id for a in editor_key_actions()}


def test_every_call_builds_new_actions() -> None:
    first = editor_key_actions()
    second = editor_key_actions()
    assert all(a is not b for a, b in zip(first, second, strict=True))
    first[0].set_shortcut(None)
    assert second[0].shortcut == second[0].initial_shortcut
