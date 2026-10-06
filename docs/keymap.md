# Keymap System

Nova Navigator uses a custom keymap system that replaces Textual's built-in `BINDINGS` mechanism.
It supports Emacs-style multi-chord sequences, user-configurable overrides, a status-bar hint display, and widget-driven hint priority overrides.

---

## Packages

The implementation is split across two packages.

**`nova_widgets/`** — reusable, app-agnostic layer:

| Module | Contents |
|--------|----------|
| `keymap/key_sequence.py` | `Key`, `KeyChord`, `KeySequence`, `KeyFormatStyle` |
| `keymap/key_sequence_state_machine.py` | Trie-based `KeySequenceStateMachine` |
| `keymap/hint_bar.py` | `HintBar` widget + `HintsChanged` message |
| `keymap/registry.py` | `KeymapRegistry` |
| `keybindings_config.py` | `KeybindingsConfig` (TOML persistence) |
| `keybindings_dialog.py` | `KeybindingsDialog`, `KeyCaptureDialog` |

---

## Action Definition

Every dispatchable command is described by an `Action` object defined in `nova_widgets/action.py`.
The keymap-relevant fields of the constructor are:

| Parameter | Type | Meaning |
|-----------|------|---------|
| `text` | `str \| None` | Display label for menus, e.g. `"Open…"` |
| `id` | `str \| None` | Stable dot-namespaced identifier, e.g. `"browser.copy"` |
| `action` | `str \| None` | Textual action string dispatched on activation, e.g. `"copy_or_move_files(False)"` |
| `description` | `str` | Human-readable description shown in the keybindings dialog |
| `shortcut` | `str \| None` | Default key sequence in Textual notation, e.g. `"f5"` or `"ctrl+x ctrl+s"` |
| `show` | `bool` | Whether the action appears in the `HintBar` |
| `bar_priority` | `int` | Sort order in the `HintBar` (lower = further left) |

The `initial_shortcut` property holds the shortcut set at construction time (frozen, never mutated).
The `set_shortcut()` method sets the displayed shortcut after loading user config.

Actions are declared as `ACTIONS: ClassVar[list[Action]]` on a `Screen` or `Widget` subclass.
Nova Navigator declares them on `MainScreen` and `DirectoryBrowser`.

### Example

```python
ACTIONS: ClassVar[list[Action]] = [
    Action(
        "Copy",
        id="browser.copy",
        action="copy_or_move_files(False)",
        description="Copy selected files to the other panel",
        shortcut="f5",
        show=True,
        bar_priority=20,
    ),
]
```

---

## Key Representation (`key_sequence.py`)

`key_sequence.py` defines the data model for all key representations.

### `KeyFormatStyle`

A `StrEnum` controlling how keys are rendered in the UI.

| Value | Example |
|-------|---------|
| `CLASSIC` | `Ctrl+X` |
| `EMACS` | `C-x` |
| `CARET` | `^X` |

### `Key`

A single physical key, e.g. `Key("ctrl")` or `Key("f5")`.
`Key.parse(s)` normalises the name to lowercase.
`key.is_modifier` returns `True` for `ctrl`, `alt`, `shift`, and `meta`.
`key.format(style)` renders the key for display.

### `KeyChord`

One or more keys pressed simultaneously, e.g. `Ctrl+X` or `F5`.
Keys are stored in canonical order — modifiers (`ctrl < alt < shift < meta`) first, then the base key — so `KeyChord.parse("x+ctrl") == KeyChord.parse("ctrl+x")`.
`KeyChord.parse(s)` accepts a Textual key string such as `"ctrl+x"`.
`chord.format(style)` renders the chord for display.

### `KeySequence`

An ordered series of key chords pressed one after another, e.g. `Ctrl+X` followed by `Ctrl+S`.
`KeySequence.parse(s)` splits a space-separated Textual key string into chords.
`seq.format(style)` renders the full sequence for display.
`seq.suffix_after(prefix)` returns the sub-sequence following the first contiguous occurrence of `prefix`.
It is used in chord-pending mode to show only the keys still to be pressed.
If `prefix` is not found, `self` is returned unchanged.

---

## Key Sequences

Key names follow Textual notation: `"f5"`, `"ctrl+c"`, `"alt+left"`, `"shift+enter"`.
Multi-chord sequences are space-separated: `"ctrl+x ctrl+s"`.

---

## Key-Sequence State Machine (`KeySequenceStateMachine`)

The state machine lives in `nova_widgets/keymap/key_sequence_state_machine.py`.
It maintains a trie of key sequences and tracks the current position within it.

### How it works

1. `build_trie(bindings)` inserts every `action_name → key_sequence` pair into the trie.
   Each leaf node stores the action name.

2. `feed(chord)` accepts a `KeyChord` and processes one key press:
   - If the chord is `escape`, the state machine resets to IDLE and returns `consumed=False` (escape is never consumed).
   - If the chord matches a child of the current node, the machine advances.
     - **Leaf hit**: the action name is returned and the machine resets to IDLE.
     - **Prefix hit**: the machine enters a pending state and returns the list of valid next chords (`continuations`).
   - If no match, the machine resets to IDLE and returns `consumed=False`.

3. `reset()` unconditionally returns to IDLE.

### `SequenceResult` fields

| Field | Meaning |
|-------|---------|
| `consumed` | `True` if the key was handled (action fired or prefix accepted) |
| `action_name` | Set when a complete sequence was recognised |
| `continuations` | Set on a prefix hit; list of `(KeyChord, action_name)` for next chord |

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
3. Rebuilds the key-sequence trie.
4. Refreshes the hint bar.

### `set_key_display_style(style)`

Updates the `KeyFormatStyle` and immediately refreshes the hint bar.
Call this before or after `reload`.

### `async handle_key(key, app) -> bool`

Called from `NovaNavigator.on_event` before Textual's priority bindings and awaited.
Returns `True` if the key was consumed.

`key` is a Textual key name string such as `"ctrl+x"` or `"f5"`.
It is converted to a `KeyChord` internally before being fed to the state machine.

When a complete sequence is resolved, the dispatch order is:

1. `app.run_action(action, app.focused)` — tries the focused widget first.
2. `app.run_action(action, app.screen)` — tries the active screen.
3. `app.run_action(action)` — falls back to the app itself.

When a key is not consumed and a pending sequence was in progress (including when `Escape` is pressed), the pending state is cleared and the hint bar returns to normal mode.

### `on_focus_changed(widget)`

Called by `MainScreen.on_focus` whenever the focused widget changes.
Stores the new focused widget and refreshes the hint bar with any saved priority overrides for that widget.

### `update_hint_priorities(widget, overrides)`

Called when a widget posts a `HintsChanged` message.
Stores the per-widget overrides in a `WeakKeyDictionary` (auto-cleaned when the widget is unmounted).
If the widget is currently focused, the hint bar is refreshed immediately.

---

## HintBar

`HintBar` operates in two modes.

**Normal mode** shows MC-style key/label badges for all actions visible in the current context.

**Chord-pending mode** activates when the user has pressed a prefix chord.
It displays the prefix entered so far, a `→` separator, and badges for the available continuation keys.
Only the keys still to be pressed are shown for each continuation — the already-entered prefix is stripped using `KeySequence.suffix_after`.
Pressing `Escape` or any unrecognised key cancels the sequence and returns to normal mode.

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

`KeybindingsConfig` in `nova_widgets/keybindings_config.py` loads and saves per-user overrides.

**File location:** `~/.config/nova-navigator/keybindings.toml`

**File format:**

```toml
[bindings]
browser.copy = "f5"
browser.delete = "delete"
app.quit = "ctrl+q"
```

Only overrides need to be listed; actions absent from the file fall back to their `initial_shortcut`.
Setting a value to an empty string (`""`) unmaps the default binding entirely.

### `resolve(actions) -> dict[str, KeySequence]`

Merges `Action.initial_shortcut` values with file overrides and returns the effective `{action_name: key_sequence}` map.
This is the map passed to `KeymapRegistry.reload()`.

---

## Startup flow

1. `MainScreen.on_mount` creates `HintBar` and `KeymapRegistry(hint_bar)`.
2. `MainScreen._reload_keymap` is called:
   - Sets the key display style on the registry.
   - Collects `ACTIONS` from `MainScreen` and `DirectoryBrowser`.
   - Calls `KeybindingsConfig.resolve(actions)` to get the effective binding map.
   - Calls `KeymapRegistry.reload(bindings, actions)`, which writes shortcut strings back into `Action` objects, rebuilds the trie, and refreshes the hint bar.
3. On every key event, `NovaNavigator.on_event` calls `KeymapRegistry.handle_key(key, app)` when a `MainScreen` is the active screen.
   If consumed, the event is stopped.
   Otherwise, it falls through to terminal and panel key handling.
4. `MainScreen.on_focus` calls `KeymapRegistry.on_focus_changed(self.app.focused)` on every focus change.
5. `MainScreen.on_hints_changed` calls `KeymapRegistry.update_hint_priorities(event.widget, event.priorities)` when any widget posts a `HintsChanged` message.

`_reload_keymap` is also called after the `KeybindingsDialog` is dismissed.

---

## Adding a new action

1. Add an `Action(...)` entry to `ACTIONS` on the appropriate class (`MainScreen` or `DirectoryBrowser`).
   Choose a dot-namespaced `id`, set `shortcut`, `show`, and the Textual `action` string.

2. Implement `_action_{name}` (or `action_{name}`) on the same class.
   Textual's dispatch tries the private form first.

3. If it should appear in the keybindings dialog, `KeybindingsConfig` will pick it up automatically.
   No further registration is needed.

---

## Keybindings Dialog

`KeybindingsDialog` in `nova_widgets/keybindings_dialog.py` lists all known actions with their current shortcut in a data table.
It is opened from the system menu under **Key Bindings…** or programmatically via `action_keybindings`.

After the dialog is dismissed, `MainScreen._reload_keymap` is called to apply any changes.
The navigator lists `MainScreen.ACTIONS` followed by the 18 editor actions of `editor_key_actions()` (`nova_navigator/embedded_editor_keys.py`), labelled `Editor: <text>`.
The ids stay `editor.*`, so the overrides land under the same names in `keybindings.toml` as in the standalone editor.
Every `editor.*` id must be in the list, because a save replaces the overrides in the file with the map of the listed actions.

### Unmapped actions behavior

The dialog keeps unmapped actions unmapped on save: an action that has an initial default key but no entry in the resolved map joins the deleted names, so the save writes it as an empty string.
Actions with neither default nor override are not written.

---

## EditorScreen (nova_editor integration)

`EditorScreen` (in `nova_editor/screen.py`) uses a per-screen keymap registry to support independent key bindings across multiple editor instances.

### Architecture

Each `EditorScreen` instance builds:
1. **ACTIONS** — 18 editor actions (File, Edit, Search, View), plus Keyboard Shortcuts… when the screen has a key config.
2. **KeymapRegistry** — Per-screen registry for those actions (not shared).
3. **MenuBar** — Per-screen menu bar with current bindings.
4. **HintBar** — Per-screen hint bar.

This design allows a Textual app to embed multiple editor screens with different keybinding configurations or contexts.

### Keybinding Flow

1. On construction, `EditorScreen.__init__` calls `_apply_keymap()`: it resolves the overrides with `KeybindingsConfig.resolve()` and loads them into the registry, the actions and the hint bar.
2. The menu bar is built with those actions, showing the current binding in each menu item.
3. The `KeymapRegistry` manages dispatch.
4. The host forwards every raw `Key` event to `EditorScreen.press_key` (from `App.on_event`, before Textual's priority bindings); the registry dispatches to the action method.
   While the `Input` of a popup has the focus, `press_key` leaves its own keys and the editing actions to the `Input`; every other key reaches the registry first, so F3, Ctrl+F, Ctrl+G, Ctrl+S and Ctrl+Q work with a popup open.
   With a dialog on top of the screen the host does not call `press_key` (only an `EditorScreen` on top gets it), so the keys belong to the dialog; Textual's own Ctrl+Q binding still reaches `NovaEditApp.action_quit`, which asks the screen and is ignored while a flow is open.

### Save and Apply Flow

Opening View > "Keyboard Shortcuts…" shows the shared `KeybindingsDialog` with the `editor.*` actions.
After the dialog is dismissed, the flow calls `reload_keymap()`, which re-reads the config file, runs `_apply_keymap()` and re-measures the menus, applying at once any changes to the key, the menu label, the hint bar, the swallowing of old editing keys and unmap handling.

### Navigator integration

The navigator passes the `KeybindingsConfig` of its own `keybindings.toml` to every new `EditorScreen` with `keyboard_shortcuts_item=False`, so the standalone "Keyboard Shortcuts…" item is absent and an editor-only save can never overwrite the navigator's file.
The editor actions live in the screen's own registry; the navigator's registry is loaded only from `MainScreen.ACTIONS` and `DirectoryBrowser.ACTIONS` and never sees them.
The overrides are applied when the editor is opened; the dialog is not reachable while an editor is open, so `reload_keymap()` is not called.
While the editor is the active screen, `NovaNavigator.on_event` gives raw keys to `EditorScreen.press_key` and the `MainScreen` registry is not consulted, so equal default keys never collide.

### Unmapped actions in `KeymapRegistry.reload`

`KeymapRegistry.reload()` puts the default key of an unmapped action back instead of showing nothing.
For example, if `editor.save` is unmapped (empty string) in keybindings, the hint bar would still show `Ctrl+S`.
This is a known limitation in `nova_widgets`.
`_apply_keymap` works around this by calling `action.set_shortcut(None)` for every action without an effective binding after `reload()`.
The menu and the hint bar then show no key for it.
The workaround is sufficient for the navigator integration because the editor actions are never given to the navigator's registry.

---
