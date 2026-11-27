from __future__ import annotations

from pathlib import Path

from nova_navigator.embedded_editor_keys import editor_key_actions
from nova_navigator.key_help import build_key_help
from nova_navigator.nova_navigator import MainScreen
from nova_navigator.widgets import DirectoryBrowser
from nova_widgets import KeybindingsConfig
from nova_widgets.action import Action
from nova_widgets.keymap.key_sequence import KeyFormatStyle, KeySequence


def _all_actions() -> list[Action]:
    return [*MainScreen.ACTIONS, *DirectoryBrowser.ACTIONS, *editor_key_actions()]


def test_every_bound_action_is_listed_once_in_a_named_group(tmp_path: Path) -> None:
    config = KeybindingsConfig(tmp_path)
    actions = _all_actions()
    groups = build_key_help(actions, config, KeyFormatStyle.CLASSIC)
    assert "Other" not in [g.title for g in groups]
    listed = [row.action_id for g in groups for row in g.rows if row.action_id is not None]
    assert sorted(listed) == sorted(config.resolve(actions))


def test_unmapped_action_is_omitted(tmp_path: Path) -> None:
    config = KeybindingsConfig(tmp_path)
    config.save({"browser.copy": None})
    groups = build_key_help(_all_actions(), config, KeyFormatStyle.CLASSIC)
    assert "browser.copy" not in [row.action_id for g in groups for row in g.rows]


def test_override_key_is_shown_instead_of_default(tmp_path: Path) -> None:
    config = KeybindingsConfig(tmp_path)
    config.save({"browser.copy": KeySequence.parse("f9")})
    groups = build_key_help(_all_actions(), config, KeyFormatStyle.CLASSIC)
    rows = {row.action_id: row for g in groups for row in g.rows}
    assert rows["browser.copy"].keys == "F9"


def test_viewer_group_lists_the_effective_close_keys(tmp_path: Path) -> None:
    config = KeybindingsConfig(tmp_path)
    config.save({"editor.quit": KeySequence.parse("f9"), "editor.close": None})
    groups = build_key_help(_all_actions(), config, KeyFormatStyle.CLASSIC)
    viewer = next(g for g in groups if g.title == "Viewer")
    assert [(row.keys, row.label, row.action_id) for row in viewer.rows] == [
        ("Esc", "Close viewer", None),
        ("F10", "Close viewer", None),
        ("F9", "Close viewer", None),
    ]
