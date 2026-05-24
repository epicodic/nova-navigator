# Keymap System

Nova Navigator uses a custom keymap system that replaces Textual's built-in `BINDINGS` mechanism.
It supports Emacs-style multi-chord sequences, user-configurable overrides, a status-bar hint display, and widget-driven hint priority overrides.

---

## Packages

The implementation is split across two packages.

**`nova_widgets/keymap/`** — reusable, app-agnostic layer:

| Module | Contents |
|--------|----------|
| `chord.py` | Trie-based chord state machine |
| `format.py` | `KeyDisplayStyle` enum + `format_key()` |
| `hint_bar.py` | `HintBar` widget + `HintsChanged` message |
| `registry.py` | `KeymapRegistry` |

**`nova_navigator/keymap/`** — app-specific wiring:

| Module | Contents |
|--------|----------|
| `config.py` | `KeybindingsConfig` (TOML persistence) |

---

## Action Definition

Every dispatchable command is described by an `Action` object defined in `nova_widgets/menu/_action.py`.
The keymap-relevant fields added to `Action` are:

| Field | Type | Meaning |
|-------|------|---------|
| `name` | `str \| None` | Stable dot-namespaced identifier, e.g. `"browser.copy"` |
| `action` | `str \| None` | Textual action string dispatched on activation, e.g. `"copy_or_move_files(False)"` |
| `description` | `str` | Human-readable description shown in the keybindings dialog |
| `default_key` | `str \| None` | Default key sequence in Textual notation, e.g. `"f5"` or `"ctrl+x ctrl+s"` |
| `show_in_bar` | `bool` | Whether the action appears in the `HintBar` |
| `bar_priority` | `int` | Default sort order in the `HintBar` (lower = further left) |

Actions are declared as `ACTIONS: ClassVar[list[Action]]` on a `Screen` or `Widget` subclass.
Nova Navigator declares them on `MainScreen` and `DirectoryBrowser`.

### Example

```python
ACTIONS: ClassVar[list[Action]] = [
    Action(
        "Copy",
        name="browser.copy",
        action="copy_or_move_files(False)",
        description="Copy selected files to the other panel",
        default_key="f5",
        show_in_bar=True,
        bar_priority=20,
    ),
]
```

---

## Key Sequences

Key names follow Textual notation: `"f5"`, `"ctrl+c"`, `"alt+left"`, `"shift+enter"`.
Multi-chord sequences are space-separated: `"ctrl+x ctrl+s"`.

---

## Chord State Machine (`ChordStateMachine`)

The state machine lives in `nova_widgets/keymap/chord.py`.
It maintains a trie of key sequences and tracks the current position within it.

### How it works

1. `build_trie(bindings)` inserts every `action_name → key_sequence` pair into the trie.
   Each leaf node stores the action name.

2. `feed(key)` processes one key press:
   - If `"escape"` is pressed, the state machine resets to IDLE and returns `consumed=False` (escape is never consumed).
   - If the key matches a child of the current node, the machine advances.
     - **Leaf hit**: the action name is returned and the machine resets to IDLE.
     - **Prefix hit**: the machine enters a pending state and returns the list of valid next keys (`continuations`).
   - If no match, the machine resets to IDLE and returns `consumed=False`.

3. `reset()` unconditionally returns to IDLE.

### ChordResult fields

| Field | Meaning |
|-------|---------|
| `consumed` | `True` if the key was handled (action fired or prefix accepted) |
| `action_name` | Set when a complete sequence was recognised |
| `continuations` | Set on a prefix hit; list of `(key, action_name)` for next chord |

---

## Registry (`KeymapRegistry`)

`KeymapRegistry` in `nova_widgets/keymap/registry.py` is the central coordinator.
`MainScreen` owns one instance and passes it the `HintBar` at construction time.

### Constructor

```python
KeymapRegistry(hint_bar: HintBar)
```

### `reload(bindings, actions)`

Called after startup and after the user edits keybindings.
It:

1. Stores the `{action_name: key_sequence}` mapping.
2. Iterates over `actions`, writing the effective shortcut back into each `Action.shortcut` so menus and the hint bar display the current binding.
3. Rebuilds the chord trie.
4. Refreshes the hint bar.

### `set_key_display_style(style)`

Updates the `KeyDisplayStyle` and immediately refreshes the hint bar.
Call this before or after `reload`.

### `handle_key(key, app) -> bool`

Called from `MainScreen._on_key` before any other key handling.
Returns `True` if the key was consumed.

Dispatch order when a complete chord is resolved:

1. `app.run_action(action, app.focused)` — tries the focused widget first (e.g. `DirectoryBrowser._action_insert_select`).
2. `app.run_action(action, app.screen)` — tries the active screen (e.g. `MainScreen._action_rename`).
3. `app.run_action(action)` — falls back to the app itself.

Textual's `_dispatch_action` tries `_action_{name}` before `action_{name}` on each target.

### `on_focus_changed(widget)`

Called by `MainScreen.on_focus` whenever the focused widget changes.
Stores the new focused widget and refreshes the hint bar with any saved priority overrides for that widget.

### `update_hint_priorities(widget, overrides)`

Called when a widget posts a `HintsChanged` message.
Stores the per-widget overrides in a `WeakKeyDictionary` (auto-cleaned when the widget is unmounted).
If the widget is currently focused, the hint bar is refreshed immediately.

---

## Widget-Driven Hint Priorities

Widgets can temporarily adjust the sort order of hint bar entries to reflect their current state.
They do this by posting a `HintsChanged` message:

```python
from nova_widgets.keymap import HintsChanged

# Inside a widget method, when state changes:
self.post_message(HintsChanged(self, {"browser.copy": 5, "browser.delete": 1}))
```

`HintsChanged` fields:

| Field | Meaning |
|-------|---------|
| `widget` | The widget whose priorities changed |
| `priorities` | Maps action name → effective `bar_priority` for this widget's current state |

The registry intercepts `HintsChanged` via `MainScreen.on_hints_changed`.
Priority overrides are stored per-widget and survive focus round-trips: when focus returns to a widget, the hint bar is restored to that widget's last-announced state without the widget needing to repost.

Post an empty dict to reset a widget's overrides to default ordering.

---

## Keybindings Config (`KeybindingsConfig`)

`KeybindingsConfig` in `nova_navigator/keymap/config.py` loads and saves per-user overrides.

**File location:** `~/.config/nova-navigator/keybindings.toml`

**File format:**

```toml
[bindings]
browser.copy = "f5"
browser.delete = "delete"
app.quit = "ctrl+q"
```

Only overrides need to be listed; actions absent from the file fall back to their `default_key`.
Setting a value to an empty string (`""`) unmaps the default binding entirely.

### `resolve(actions) -> dict[str, str]`

Merges `Action.default_key` values with file overrides and returns the effective `{action_name: key_sequence}` map.
This is the map passed to `KeymapRegistry.reload()`.

---

## HintBar

`HintBar` in `nova_widgets/keymap/hint_bar.py` is a Textual widget docked to the bottom of the screen (height 1).
It replaces the old `Footer` widget.

It operates in two modes:

**Normal mode** — displays `[KEY] Label` badges for all actions in the pre-sorted list supplied by `set_hints`.
`KeymapRegistry._refresh_hint_bar` calls `set_hints` with a list already filtered to `show_in_bar=True` and sorted by effective priority (default `bar_priority` overridden by any widget-specific priorities).

**Chord-pending mode** — when the user has pressed the first chord of a multi-chord sequence, the bar switches to display the pressed prefix and available continuation keys.
Pressing `Escape` cancels and returns to normal mode.

### Key display styles

Configured via `GeneralSettings.key_display_style` (`KeyDisplayStyle`):

| Style | Example |
|-------|---------|
| `classic` | `Ctrl+C` |
| `emacs` | `C-c` |
| `caret` | `^C` |

---

## Startup flow

1. `MainScreen.on_mount` creates `HintBar` and `KeymapRegistry(hint_bar)`.
2. `MainScreen._reload_keymap` is called:
   - Sets the key display style on the registry.
   - Collects `ACTIONS` from `MainScreen` and `DirectoryBrowser`.
   - Calls `KeybindingsConfig.resolve(actions)` to get the effective binding map.
   - Calls `KeymapRegistry.reload(bindings, actions)`, which writes shortcut strings back into `Action` objects, rebuilds the trie, and refreshes the hint bar.
3. On every key event, `MainScreen._on_key` calls `KeymapRegistry.handle_key(key, app)`.
   If consumed, the event is stopped.
   Otherwise, it falls through to terminal key forwarding.
4. `MainScreen.on_focus` calls `KeymapRegistry.on_focus_changed(self.app.focused)` on every focus change.
5. `MainScreen.on_hints_changed` calls `KeymapRegistry.update_hint_priorities(event.widget, event.priorities)` when any widget posts a `HintsChanged` message.

`_reload_keymap` is also called after the `KeybindingsDialog` is dismissed.

---

## Adding a new action

1. Add an `Action(...)` entry to `ACTIONS` on the appropriate class (`MainScreen` or `DirectoryBrowser`).
   Choose a dot-namespaced `name`, set `default_key`, `show_in_bar`, and the Textual `action` string.

2. Implement `_action_{name}` (or `action_{name}`) on the same class.
   Textual's dispatch tries the private form first.

3. If it should appear in the keybindings dialog, `KeybindingsConfig` will pick it up automatically.
   No further registration is needed.

---

## Keybindings Dialog

`KeybindingsDialog` in `nova_navigator/dialogs/keybindings_dialog.py` lists all known actions with their current shortcut in a data table.
It is opened from the system menu under **Key Bindings…** or programmatically via `action_keybindings`.

After the dialog is dismissed, `MainScreen._reload_keymap` is called to apply any changes.


---

## Action Definition

Every dispatchable command is described by an `Action` object defined in `nova_widgets/menu/_action.py`.
The keymap-relevant fields added to `Action` are:

| Field | Type | Meaning |
|-------|------|---------|
| `name` | `str \| None` | Stable dot-namespaced identifier, e.g. `"browser.copy"` |
| `action` | `str \| None` | Textual action string dispatched on activation, e.g. `"copy_or_move_files(False)"` |
| `description` | `str` | Human-readable description shown in the keybindings dialog |
| `contexts` | `list[str]` | Contexts in which this action is active (see [Contexts](#contexts)) |
| `default_key` | `str \| None` | Default key sequence in Textual notation, e.g. `"f5"` or `"ctrl+x ctrl+s"` |
| `show_in_bar` | `bool` | Whether the action appears in the `HintBar` |
| `bar_priority` | `int` | Sort order in the `HintBar` (lower = further left) |

Actions are declared as `ACTIONS: ClassVar[list[Action]]` on a `Screen` or `Widget` subclass.
Nova Navigator declares them on `MainScreen` and `DirectoryBrowser`.

### Example

```python
ACTIONS: ClassVar[list[Action]] = [
    Action(
        "Copy",
        name="browser.copy",
        action="copy_or_move_files(False)",
        description="Copy selected files to the other panel",
        contexts=["browser", "browser.selection"],
        default_key="f5",
        show_in_bar=True,
        bar_priority=20,
    ),
]
```

---

## Contexts

A **context** is a string that describes the current application state.
`NovaContextResolver.resolve()` returns one of:

| Context | When active |
|---------|-------------|
| `"dialog"` | A `Dialog` modal screen is open |
| `"terminal"` | The embedded terminal widget has keyboard focus |
| `"browser.selection"` | A `DirectoryBrowser` has focus **and** has selected items |
| `"browser"` | A `DirectoryBrowser` has focus with no selection |

### Hierarchical matching

Context matching is **hierarchical**.
An action registered under `contexts=["browser"]` will fire in both `"browser"` and `"browser.selection"`.
The rule is: a registered context `c` matches the current context `ctx` if `ctx == c` or `ctx.startswith(c + ".")`.

This means an action that should be available in all browser states only needs `contexts=["browser"]`.
An action that must be restricted to the selection state uses `contexts=["browser.selection"]`.

---

## Key Sequences

Key names follow Textual notation: `"f5"`, `"ctrl+c"`, `"alt+left"`, `"shift+enter"`.
Multi-chord sequences are space-separated: `"ctrl+x ctrl+s"`.

---

## Chord State Machine (`ChordStateMachine`)

The state machine lives in `nova_widgets/keymap/chord.py`.
It maintains a trie of key sequences and tracks the current position within it.

### How it works

1. `build_trie(bindings)` inserts every `(key_sequence, contexts)` pair into the trie.
   Each leaf node stores the action name and the set of contexts in which it is active.
   Each interior (prefix) node accumulates the union of all descendant contexts so the machine can reject a prefix early when the current context cannot reach any leaf below it.

2. `feed(key, context)` processes one key press:
   - If `"escape"` is pressed, the state machine resets to IDLE and returns `consumed=False` (escape is never consumed).
   - If the key matches a child of the current node **and** the context matches (hierarchically), the machine advances.
     - **Leaf hit**: the action name is returned and the machine resets to IDLE.
     - **Prefix hit**: the machine enters a pending state and returns the list of valid next keys (`continuations`).
   - If no match, the machine resets to IDLE and returns `consumed=False`.

3. `reset()` unconditionally returns to IDLE.

### ChordResult fields

| Field | Meaning |
|-------|---------|
| `consumed` | `True` if the key was handled (action fired or prefix accepted) |
| `action_name` | Set when a complete sequence was recognised |
| `continuations` | Set on a prefix hit; list of `(key, action_name)` for next chord |

---

## Registry (`KeymapRegistry`)

`KeymapRegistry` in `nova_widgets/keymap/registry.py` is the central coordinator.
`MainScreen` owns one instance and holds a reference to it.

### `reload(bindings, actions)`

Called after startup and after the user edits keybindings.
It:

1. Stores the `{action_name: key_sequence}` mapping.
2. Iterates over `actions`, writing the effective shortcut back into each `Action.shortcut` so menus and the hint bar display the current binding.
3. Rebuilds the chord trie.

### `handle_key(key, app) -> bool`

Called from `MainScreen._on_key` before any other key handling.
Returns `True` if the key was consumed.

Dispatch order when a complete chord is resolved:

1. `app.run_action(action, app.focused)` — tries the focused widget first (e.g. `DirectoryBrowser._action_insert_select`).
2. `app.run_action(action, app.screen)` — tries the active screen (e.g. `MainScreen._action_rename`).
3. `app.run_action(action)` — falls back to the app itself.

Textual's `_dispatch_action` tries `_action_{name}` before `action_{name}` on each target, so private implementation methods are found without public wrappers.

---

## Context Resolver (`NovaContextResolver`)

`NovaContextResolver` in `nova_navigator/keymap/context.py` implements the `ContextResolver` protocol.
It is instantiated in `MainScreen.on_mount` and passed to `KeymapRegistry`.

```python
class ContextResolver(Protocol):
    def resolve(self) -> str: ...
    def hover_context(self) -> str | None: ...
```

`resolve()` inspects the live Textual widget tree to determine the active context (see [Contexts](#contexts) above).

---

## Keybindings Config (`KeybindingsConfig`)

`KeybindingsConfig` in `nova_navigator/keymap/config.py` loads and saves per-user overrides.

**File location:** `~/.config/nova-navigator/keybindings.toml`

**File format:**

```toml
[bindings]
browser.copy = "f5"
browser.delete = "delete"
app.quit = "ctrl+q"
```

Only overrides need to be listed; actions absent from the file fall back to their `default_key`.
Setting a value to an empty string (`""`) unmaps the default binding entirely.

### `resolve(actions) -> dict[str, str]`

Merges `Action.default_key` values with file overrides and returns the effective `{action_name: key_sequence}` map.
This is the map passed to `KeymapRegistry.reload()`.

---

## HintBar

`HintBar` in `nova_widgets/keymap/hint_bar.py` is a Textual widget docked to the bottom of the screen (height 1).
It replaces the old `Footer` widget.

It operates in two modes:

**Normal mode** — displays `[KEY] Label` badges for all actions whose `show_in_bar=True` and which have an active shortcut.
Badges are sorted by `bar_priority`.

**Chord-pending mode** — when the user has pressed the first chord of a multi-chord sequence, the bar switches to display the pressed prefix and available continuation keys.
Pressing `Escape` cancels and returns to normal mode.

### Key display styles

Configured via `GeneralSettings.key_display_style` (`KeyDisplayStyle`):

| Style | Example |
|-------|---------|
| `classic` | `Ctrl+C` |
| `emacs` | `C-c` |
| `caret` | `^C` |

---

## Startup flow

1. `MainScreen.on_mount` creates `NovaContextResolver` and `KeymapRegistry`.
2. `MainScreen._reload_keymap` is called:
   - Collects `ACTIONS` from `MainScreen` and `DirectoryBrowser`.
   - Calls `KeybindingsConfig.resolve(actions)` to get the effective binding map.
   - Calls `KeymapRegistry.reload(bindings, actions)` which writes shortcut strings back into `Action` objects and rebuilds the trie.
   - Updates the `HintBar` with actions that are `show_in_bar=True` for the current context.
3. On every key event, `MainScreen._on_key` calls `KeymapRegistry.handle_key(key, app)`.
   If consumed, the event is stopped.
   Otherwise, it falls through to terminal key forwarding in `_handle_key`.

`_reload_keymap` is also called after the `KeybindingsDialog` is dismissed.

---

## Adding a new action

1. Add an `Action(...)` entry to `ACTIONS` on the appropriate class (`MainScreen` or `DirectoryBrowser`).
   Choose a dot-namespaced `name`, set `contexts`, `default_key`, and the Textual `action` string.

2. Implement `_action_{name}` (or `action_{name}`) on the same class.
   Textual's dispatch tries the private form first.

3. If it should appear in the keybindings dialog, `KeybindingsConfig` will pick it up automatically.
   No further registration is needed.

---

## Keybindings Dialog

`KeybindingsDialog` in `nova_navigator/dialogs/keybindings_dialog.py` lists all known actions with their current shortcut in a data table.
It is opened from the `𑁔` system menu under **Key Bindings…** or programmatically via `action_keybindings`.

After the dialog is dismissed, `MainScreen._reload_keymap` is called to apply any changes.
