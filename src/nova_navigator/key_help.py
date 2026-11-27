"""Build the F1 key help: the effective key bindings grouped for display."""

from __future__ import annotations

from dataclasses import dataclass

from nova_widgets.action import Action
from nova_widgets.keybindings_config import KeybindingsConfig
from nova_widgets.keymap.key_sequence import KeyFormatStyle


@dataclass(frozen=True)
class HelpRow:
    keys: str
    label: str
    action_id: str | None = None  # None for a fixed key that is not an action


@dataclass(frozen=True)
class HelpGroup:
    title: str
    rows: list[HelpRow]


_GROUP_ORDER: tuple[str, ...] = (
    "General",
    "File operations",
    "Navigation",
    "Selection and filter",
    "Sort and view",
    "Bookmarks",
    "File list",
    "Terminal",
    "Editor",
)

_EXCEPTIONS: dict[str, str] = {
    "browser.go_to_path": "Navigation",
    "browser.go_back": "Navigation",
    "browser.go_forward": "Navigation",
    "browser.go_up": "Navigation",
    "browser.follow_symlink": "Navigation",
    "app.connect_to": "Navigation",
    "browser.refresh": "Navigation",
    "browser.insert_select": "Selection and filter",
    "browser.select_all": "Selection and filter",
    "browser.filter": "Selection and filter",
    "browser.sort_name": "Sort and view",
    "browser.sort_size": "Sort and view",
    "browser.sort_modified": "Sort and view",
    "browser.sort_extension": "Sort and view",
    "browser.toggle_hidden": "Sort and view",
    "app.show_bookmarks": "Bookmarks",
    "app.toggle_terminal": "Terminal",
    "app.toggle_maximized_terminal": "Terminal",
}

_PREFIXES: tuple[tuple[str, str], ...] = (
    ("app.", "General"),
    ("file.", "File operations"),
    ("browser.", "File operations"),
    ("selection.", "Selection and filter"),
    ("view.", "Sort and view"),
    ("bookmarks.", "Bookmarks"),
    ("editor.", "Editor"),
)

_FIXED_ROWS: dict[str, list[HelpRow]] = {
    "File list": [
        HelpRow("Enter", "Open the item under the cursor"),
        HelpRow("Up / Down", "Move the cursor"),
        HelpRow("Left / Right", "Move the cursor"),
        HelpRow("Page Up / Page Down", "Move the cursor by a page"),
        HelpRow("Home / End", "First / last item"),
    ],
    "Terminal": [
        HelpRow("Ctrl+Shift+C", "Copy the terminal selection"),
        HelpRow("Shift+Page Up / Shift+Page Down", "Scroll the terminal"),
    ],
    "Editor": [
        HelpRow("Esc", "Close the editor"),
    ],
}


def _group_of(action_id: str) -> str:
    if action_id in _EXCEPTIONS:
        return _EXCEPTIONS[action_id]
    for prefix, group in _PREFIXES:
        if action_id.startswith(prefix):
            return group
    return "Other"


def build_key_help(actions: list[Action], config: KeybindingsConfig, key_display_style: KeyFormatStyle) -> list[HelpGroup]:
    """Group the effective bindings of *actions* for display; unbound actions are omitted.

    Args:
        actions: The actions whose bindings are listed, in display order.
        config: The key overrides; the keys shown are the effective ones.
        key_display_style: How the key sequences are formatted.

    Returns:
        The non-empty groups in display order. Each action id appears at most once.
    """
    bindings = config.resolve(actions)
    rows: dict[str, list[HelpRow]] = {title: [] for title in (*_GROUP_ORDER, "Other")}
    seen: set[str] = set()
    for action in actions:
        action_id = action.id
        if action_id is None or action_id not in bindings or action_id in seen:
            continue
        seen.add(action_id)
        rows[_group_of(action_id)].append(HelpRow(bindings[action_id].format(key_display_style), action.text, action_id))
    for title, fixed in _FIXED_ROWS.items():
        rows[title].extend(fixed)
    return [HelpGroup(title, rows[title]) for title in (*_GROUP_ORDER, "Other") if rows[title]]
