# Actions

## Overview

The action system gives each command a single `Action` object that is the source of truth for its label, shortcut, icon, and enabled state.
The same `Action` instance is referenced by the menu, the keymap, and the key-hint bar — so all three stay in sync automatically.

## `Action` class

**Module:** `nova_widgets.action`

Each `Action` represents one user-visible command.

### Constructor

```python
Action(
    text: str | None = None,
    *,
    name: str | None = None,
    shortcut: str | None = None,
    icon: str | None = None,
    enabled: bool = True,
    checkable: bool = False,
    checked: bool = False,
    action: str | None = None,
    description: str = "",
    show: bool = False,
    bar_priority: int = 100,
)
```

| Parameter | Description |
|-----------|-------------|
| `text` | Label shown in menus and the hint bar. |
| `name` | Lookup key used by `_act()` and the keymap; defaults to `action` if omitted. |
| `shortcut` | Default key binding (e.g. `"f5"`, `"ctrl+c"`, `"ctrl+s a"`). |
| `icon` | Icon name passed to the icon provider. |
| `enabled` | Whether the action is initially enabled. |
| `checkable` | Whether the action can be toggled on/off. |
| `checked` | Initial checked state (only meaningful when `checkable=True`). |
| `action` | Textual action string executed when the command is triggered. |
| `description` | Longer description shown in the keybindings editor. |
| `show` | Whether to show this action in the key-hint bar. |
| `bar_priority` | Display order in the hint bar (lower = leftmost). |

### Key properties

- `text` — display label.
- `name` — lookup key (read-only).
- `shortcut` — current effective shortcut; may be overridden by user config.
- `initial_shortcut` — shortcut set at construction time; never mutated.
- `enabled` / `set_enabled(bool)` — enable or disable the action at runtime.
- `checkable`, `checked` / `set_checked(bool)` — toggle state.

### Key methods

- `set_shortcut(shortcut)` — replace the displayed shortcut (e.g. after loading user config).
- `reset_shortcut()` — restore `shortcut` to `initial_shortcut`.
- `set_icon_provider(provider)` (module-level) — replace the global icon factory.

### `name` vs `action`

`name` is the lookup key used by `_act()` and the keymap system.
`action` is the Textual action string that is actually dispatched when the command runs.
If `name` is not given, it falls back to `action`.

## `ActionsSupport` mixin

**Module:** `nova_widgets.actions_support`

`ActionsSupport` is a mixin that lets a widget or screen declare a named `ACTIONS` list and look up actions by name at runtime.

### Usage

Inherit from `ActionsSupport` **before** `Widget` or `Screen` in the MRO:

```python
from nova_widgets.actions_support import ActionsSupport
from textual.widget import Widget

class MyWidget(ActionsSupport, Widget):
    ...
```

### `ACTIONS` class variable

```python
ACTIONS: ClassVar[list[Action]] = [...]
```

Declare all actions for the widget or screen as a class variable.

### MRO merging

`ActionsSupport` walks the full MRO and merges all `ACTIONS` lists into `_actions_by_name`.
Parent actions come first; child actions override parent actions with the same name.
Child classes automatically inherit parent actions without re-declaring them.

### `_act(name)`

```python
def _act(self, name: str) -> Action: ...
```

Look up an action by name.
Raises `KeyError` if no action with that name is registered on the class.

## Example

```python
from nova_widgets.action import Action
from nova_widgets.actions_support import ActionsSupport
from textual.widget import Widget

class MyWidget(ActionsSupport, Widget):
    ACTIONS = [
        Action("Copy", name="copy", shortcut="f5", action="copy_files"),
        Action("Move", name="move", shortcut="f6", action="move_files"),
    ]

    def on_key(self, event: events.Key) -> None:
        copy_action = self._act("copy")
        if copy_action.enabled:
            ...
```

## Integration with the menu

Menu items in `compose()` are built with `mc.action()` using the Textual `action` string:

```python
mc.action("Copy", action="copy_or_move_files(False)")
```

The `action` string here corresponds to the `action` parameter on the matching `Action` entry.
The menu does not automatically read shortcuts from the `ACTIONS` list; shortcut display in the menu is configured separately via the menu builder.

## Integration with the keymap

`nova_navigator/keymap/config.py` uses `action.initial_shortcut` as the default binding for each named action.
User overrides in `~/.config/nova-navigator/keybindings.toml` take precedence over `initial_shortcut`.
A TOML entry of `""` (empty string) explicitly removes the default binding.
`KeybindingsConfig.resolve(actions)` returns the final `{name: KeySequence}` map used to wire up key handling.
