"""The editor's key actions as rows of the navigator's keybindings dialog (REQ-7)."""

from __future__ import annotations

from nova_editor.editor_actions import build_editor_actions
from nova_widgets.action import Action

EDITOR_LABEL_PREFIX = "Editor: "
"""Prefix of the label of an editor action in the dialog: the editor's own labels repeat the navigator's (Quit, Copy, Open ...), and the prefix groups the rows."""


def editor_key_actions() -> list[Action]:
    """Build the 18 `editor.*` actions for the keybindings dialog.

    The ids and default keys are the editor's own, so the overrides land under the same names in `keybindings.toml` as in the standalone editor.
    Every call returns new objects, so no state is shared with an editor screen or the navigator's registry.
    """
    return [
        Action(
            f"{EDITOR_LABEL_PREFIX}{action.text}",
            id=action.id,
            shortcut=str(action.initial_shortcut) if action.initial_shortcut is not None else None,
            description=action.description,
        )
        for action in build_editor_actions()
    ]
