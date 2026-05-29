"""KeymapRegistry — central key dispatch coordinator."""

from __future__ import annotations

from typing import Any
from weakref import WeakKeyDictionary

from textual.app import App
from textual.widget import Widget

from nova_widgets.action import Action, set_key_format_style
from nova_widgets.keymap.hint_bar import HintBar
from nova_widgets.keymap.key_sequence import KeyChord, KeyFormatStyle, KeySequence
from nova_widgets.keymap.key_sequence_state_machine import KeySequenceStateMachine, SequenceResult


class KeymapRegistry:
    """Central key dispatch coordinator.

    Walks the focused widget tree collecting ACTIONS, feeds key events into the
    KeySequenceStateMachine, and dispatches matched actions via app.run_action().
    Owns the HintBar update logic, including per-widget priority overrides.
    """

    def __init__(self, hint_bar: HintBar) -> None:
        self._hint_bar = hint_bar
        self._chord = KeySequenceStateMachine()
        self._action_map: dict[str, Action] = {}
        self._all_actions: list[Action] = []
        self._bindings: dict[str, KeySequence] = {}
        self._key_display_style: KeyFormatStyle = KeyFormatStyle.CLASSIC
        self._focused_widget: Widget | None = None
        self._widget_priority_overrides: WeakKeyDictionary[Widget, dict[str, int]] = WeakKeyDictionary()
        self._pending_chord_info: tuple[KeySequence, list[Action]] | None = None

    def reload(self, bindings: dict[str, KeySequence], actions: list[Action] | None = None) -> None:
        """Rebuild the trie from a flat {action_name: KeySequence} config.

        Also accepts an optional list of Action objects to update the action map
        and write back shortcut display strings.

        Args:
            bindings: Maps action name to KeySequence.
            actions: Optional list of all known Action objects. Their shortcut
                     fields are updated to match the resolved bindings.
        """
        self._bindings = dict(bindings)

        if actions is not None:
            self._all_actions = list(actions)
            self._action_map = {a.id: a for a in actions if a.id is not None}
            for action in actions:
                if action.id and action.id in bindings:
                    action.set_shortcut(bindings[action.id])
                elif action.id and action.initial_shortcut:
                    action.set_shortcut(action.initial_shortcut)

        self._chord.build_trie(self._bindings)
        self._refresh_hint_bar()

    def set_key_display_style(self, style: KeyFormatStyle) -> None:
        """Update the key display style and refresh the hint bar."""
        self._key_display_style = style
        set_key_format_style(style)
        self._refresh_hint_bar()

    def collect_actions(self, app: App[Any]) -> list[Action]:
        """Collect all ACTIONS from the focused widget tree.

        Walks from the focused widget up to the screen. Child actions shadow
        parent actions with the same name. Only walks widgets that have an ACTIONS
        class variable (our own widgets).

        Args:
            app: The running Textual application.

        Returns:
            Ordered list of actions, highest priority first (focused widget first).
        """
        focused: Widget | None = app.focused
        seen_names: set[str] = set()
        result: list[Action] = []
        screen = app.screen

        # Screen-level ACTIONS (lowest priority)
        for action in _get_widget_actions(screen):
            if action.id not in seen_names:
                seen_names.add(action.id or "")
                result.append(action)

        if focused is not None:
            # Walk from focused widget up to (but not including) screen
            node: Widget | None = focused
            local_actions: list[Action] = []
            while node is not None and node is not screen:
                for action in _get_widget_actions(node):
                    if action.id not in seen_names:
                        seen_names.add(action.id or "")
                        local_actions.append(action)
                parent = node.parent
                if not isinstance(parent, Widget):
                    break
                node = parent
            # Prepend local (higher priority) actions
            result = local_actions + result
        else:
            # No focused widget — walk all screen descendants recursively
            _collect_subtree_actions(screen, seen_names, result)

        return result

    async def handle_key(self, key: str, app: App[Any]) -> bool:
        """Handle a key event.

        Args:
            key: Textual key name, e.g. "ctrl+x" or "f5".
            app: The running application.

        Returns:
            True if the key was consumed, False if it should propagate to widgets.
        """
        # If the action map is empty (reload was called without actions), populate
        # it from the live widget tree and rebuild the trie.
        if not self._action_map:
            live_actions = self.collect_actions(app)
            self._all_actions = live_actions
            self._action_map = {a.id: a for a in live_actions if a.id is not None}
            self._chord.build_trie(self._bindings)

        result: SequenceResult = self._chord.feed(KeyChord.parse(key))

        if not result.consumed:
            if self._pending_chord_info is not None:
                self._pending_chord_info = None
                self._hint_bar.clear_chord()
            return False

        if result.action_name is not None:
            self._pending_chord_info = None
            self._hint_bar.clear_chord()
            action = self._action_map.get(result.action_name)
            if action is not None and action.action is not None:
                # Dispatch: focused widget first, then the active screen, then the app.
                dispatched = False
                if app.focused is not None:
                    dispatched = await app.run_action(action.action, app.focused)
                if not dispatched:
                    dispatched = await app.run_action(action.action, app.screen)
                if not dispatched:
                    await app.run_action(action.action)
            return True

        # Chord prefix consumed — store pending chord info and update hint bar
        continuation_actions = self._make_continuation_actions(result.continuations or [])
        new_chord = KeyChord.parse(key)
        if self._pending_chord_info is not None:
            existing_prefix, _ = self._pending_chord_info
            new_prefix = KeySequence((*existing_prefix.chords, new_chord))
        else:
            new_prefix = KeySequence((new_chord,))
        self._pending_chord_info = (new_prefix, continuation_actions)
        self._hint_bar.show_chord_pending(new_prefix, continuation_actions)
        return True

    def update_hint_priorities(self, widget: Widget, overrides: dict[str, int]) -> None:
        """Store per-widget hint priority overrides and refresh if widget is focused.

        Called when a widget posts a HintsChanged message.

        Args:
            widget: The widget whose hint priorities changed.
            overrides: Maps action name to effective bar_priority for this widget's state.
        """
        self._widget_priority_overrides[widget] = overrides
        if widget is self._focused_widget:
            self._refresh_hint_bar()

    def on_focus_changed(self, focused_widget: Widget | None) -> None:
        """Called when the focused widget changes.

        Args:
            focused_widget: The newly focused widget, or None.
        """
        self._focused_widget = focused_widget
        self._refresh_hint_bar()

    def _refresh_hint_bar(self) -> None:
        """Recompute the hint bar display from current actions and priority overrides."""
        focused = self._focused_widget
        overrides: dict[str, int]
        if focused is None:
            overrides = {}
        else:
            overrides = self._widget_priority_overrides.get(focused) or {}

        def effective_priority(action: Action) -> int:
            return overrides.get(action.id or "", action.bar_priority)

        visible = sorted(
            [a for a in self._all_actions if a.show_in_bar and a.shortcut],
            key=effective_priority,
        )
        self._hint_bar.set_hints(visible, self._key_display_style)

    @property
    def is_chord_pending(self) -> bool:
        """True when a prefix chord sequence is in progress."""
        return self._chord.is_pending

    def _make_continuation_actions(self, continuations: list[tuple[KeyChord, str | None]]) -> list[Action]:
        """Convert raw chord continuations to Action objects."""
        actions: list[Action] = []
        for chord, action_name in continuations:
            if action_name is not None and action_name in self._action_map:
                actions.append(self._action_map[action_name])
            else:
                chord_str = chord.format(KeyFormatStyle.CLASSIC)
                fallback = Action(chord_str, shortcut=chord_str)
                fallback.set_shortcut(chord_str)
                actions.append(fallback)
        return actions

    @property
    def pending_chord_info(self) -> tuple[KeySequence, list[Action]] | None:
        """The current pending prefix sequence and its possible continuation actions, or None."""
        return self._pending_chord_info


def _get_widget_actions(widget: Widget) -> list[Action]:
    """Return ACTIONS from a widget class, or empty list if not present."""
    actions = getattr(type(widget), "ACTIONS", None)
    if actions is None:
        return []
    return list(actions)


def _collect_subtree_actions(widget: Widget, seen_names: set[str], result: list[Action]) -> None:
    """Recursively collect ACTIONS from all descendants of widget (not widget itself)."""
    for child in widget.children:
        if isinstance(child, Widget):
            for action in _get_widget_actions(child):
                if action.id not in seen_names:
                    seen_names.add(action.id or "")
                    result.append(action)
            _collect_subtree_actions(child, seen_names, result)
