"""The `editor.*` actions of the editor screen with the Kate default keys (REQ-3, REQ-4)."""

from __future__ import annotations

from nova_widgets.action import Action


def build_editor_actions() -> list[Action]:
    """Build the 18 editor actions.

    Every call returns new objects: key overrides and the checked state of one screen never reach another.
    The `action` names are handled by `EditorScreen` (flows) or by the editor widget (editing).

    Returns:
        The actions in menu order.
    """
    return [
        Action("Open…", id="editor.open", action="open_file", shortcut="ctrl+o", description="Open a file"),
        Action("Save", id="editor.save", action="save", shortcut="ctrl+s", description="Save the document", show=True, bar_priority=10),
        Action("Save As…", id="editor.save_as", action="save_as", shortcut="ctrl+shift+s", description="Save the document under another name", show=True, bar_priority=60),
        Action("Reload", id="editor.reload", action="reload", shortcut="f5", description="Reload the file from disk", show=True, bar_priority=50),
        Action("Close", id="editor.close", action="close_editor", shortcut="ctrl+w", description="Close the editor"),
        Action("Quit", id="editor.quit", action="quit_editor", shortcut="ctrl+q", description="Quit the editor", show=True, bar_priority=20),
        Action("Undo", id="editor.undo", action="undo", shortcut="ctrl+z", description="Undo the last edit"),
        Action("Redo", id="editor.redo", action="redo", shortcut="ctrl+y", description="Redo the last undone edit"),
        Action("Cut", id="editor.cut", action="cut", shortcut="ctrl+x", description="Cut the selection"),
        Action("Copy", id="editor.copy", action="copy", shortcut="ctrl+c", description="Copy the selection"),
        Action("Paste", id="editor.paste", action="paste", shortcut="ctrl+v", description="Paste from the clipboard"),
        Action("Select All", id="editor.select_all", action="select_all", shortcut="ctrl+a", description="Select the whole document"),
        Action("Find…", id="editor.find", action="find", shortcut="ctrl+f", description="Search the document", show=True, bar_priority=30),
        Action("Find Next", id="editor.find_next", action="find_next", shortcut="f3", description="Repeat the search forward", show=True, bar_priority=80),
        Action("Find Previous", id="editor.find_previous", action="find_previous", shortcut="shift+f3", description="Repeat the search backward", show=True, bar_priority=90),
        Action("Go to…", id="editor.goto", action="goto", shortcut="ctrl+g", description="Go to a line or a byte offset", show=True, bar_priority=40),
        Action("Line Numbers", id="editor.line_numbers", action="toggle_line_numbers", shortcut="f11", checkable=True, description="Show or hide the line numbers"),
        Action("Wrap Mode", id="editor.wrap_mode", action="toggle_wrap", shortcut="f10", checkable=True, description="Switch soft wrap on or off", show=True, bar_priority=70),
    ]


def build_keyboard_shortcuts_action() -> Action:
    """Build the action that opens the keyboard shortcuts dialog (no default key; only a screen with a key config has it)."""
    return Action("Keyboard Shortcuts…", id="editor.keyboard_shortcuts", action="keyboard_shortcuts", description="Edit the key bindings of the editor")
