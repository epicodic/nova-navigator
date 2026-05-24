"""KeymapRegistry and ContextResolver Protocol."""

from __future__ import annotations

from typing import Any, Protocol, runtime_checkable

from textual.app import App
from textual.widget import Widget

from nova_widgets.keymap.chord import ChordResult, ChordStateMachine
from nova_widgets.menu._action import Action


@runtime_checkable
class ContextResolver(Protocol):
    """Protocol for resolving the current application context."""

    def resolve(self) -> str:
        """Return the current dispatch context string, e.g. 'browser'."""
        ...

    def hover_context(self) -> str | None:
        """Return context for the widget under the mouse pointer, or None."""
        ...


class KeymapRegistry:
    """Central key dispatch coordinator.

    Walks the focused widget tree collecting ACTIONS, feeds key events into the
    ChordStateMachine, and dispatches matched actions via app.run_action().
    """

    def __init__(self, context_resolver: ContextResolver) -> None:
        self._context_resolver = context_resolver
        self._chord = ChordStateMachine()
        self._action_map: dict[str, Action] = {}
        self._bindings: dict[str, str] = {}
        self._pending_chord_info: tuple[str, list[tuple[str, str | None]]] | None = None

    def reload(self, bindings: dict[str, str], actions: list[Action] | None = None) -> None:
        """Rebuild the trie from a flat {action_name: key_sequence} config.

        Also accepts an optional list of Action objects to update the action map
        and write back shortcut display strings.

        Args:
            bindings: Maps action name to key sequence string (Textual notation).
            actions: Optional list of all known Action objects. Their shortcut
                     fields are updated to match the resolved bindings.
        """
        self._bindings = dict(bindings)

        if actions is not None:
            self._action_map = {a.name: a for a in actions if a.name is not None}
            for action in actions:
                if action.name and action.name in bindings:
                    action.set_shortcut(bindings[action.name])
                elif action.name and action.default_key:
                    action.set_shortcut(action.default_key)

        self._rebuild_trie()

    def _rebuild_trie(self) -> None:
        """Rebuild the chord trie from current bindings and action map."""
        trie_input: dict[str, tuple[str, list[str]]] = {}
        for action_name, key_seq in self._bindings.items():
            if not key_seq:
                continue
            action = self._action_map.get(action_name)
            contexts = action.contexts if action is not None else []
            trie_input[action_name] = (key_seq, contexts)
        self._chord.build_trie(trie_input)

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
            if action.name not in seen_names:
                seen_names.add(action.name or "")
                result.append(action)

        if focused is not None:
            # Walk from focused widget up to (but not including) screen
            node: Widget | None = focused
            local_actions: list[Action] = []
            while node is not None and node is not screen:
                for action in _get_widget_actions(node):
                    if action.name not in seen_names:
                        seen_names.add(action.name or "")
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
        context = self._context_resolver.resolve()

        # If the action map is empty (reload was called without actions), populate
        # it from the live widget tree and rebuild the trie with correct contexts.
        if not self._action_map:
            live_actions = self.collect_actions(app)
            self._action_map = {a.name: a for a in live_actions if a.name is not None}
            self._rebuild_trie()

        result: ChordResult = self._chord.feed(key, context)

        if not result.consumed:
            return False

        if result.action_name is not None:
            self._pending_chord_info = None
            action = self._action_map.get(result.action_name)
            if action is not None and action.action is not None:
                # Dispatch: focused widget first (e.g. DirectoryBrowser.action_insert_select),
                # then the active screen (e.g. MainScreen.action_copy_or_move_files),
                # then the app itself (e.g. App-level actions).
                dispatched = False
                if app.focused is not None:
                    dispatched = await app.run_action(action.action, app.focused)
                if not dispatched:
                    dispatched = await app.run_action(action.action, app.screen)
                if not dispatched:
                    await app.run_action(action.action)
            return True

        # Chord prefix consumed — store pending chord info for callers to inspect
        self._pending_chord_info = (key, result.continuations or [])
        return True

    @property
    def is_chord_pending(self) -> bool:
        """True when a prefix chord sequence is in progress."""
        return self._chord.is_pending

    @property
    def pending_chord_info(self) -> tuple[str, list[tuple[str, str | None]]] | None:
        """The current pending chord prefix and its possible continuations, or None."""
        return self._pending_chord_info


def _get_widget_actions(widget: Widget) -> list[Action]:
    """Return ACTIONS from a widget class, or empty list if not present."""
    actions = getattr(type(widget), "ACTIONS", None)
    if actions is None:
        return []
    return list(actions)


def _has_actions(widget: Widget) -> bool:
    """Return True if the widget class defines an ACTIONS attribute."""
    return hasattr(type(widget), "ACTIONS")


def _collect_subtree_actions(widget: Widget, seen_names: set[str], result: list[Action]) -> None:
    """Recursively collect ACTIONS from all descendants of widget (not widget itself)."""
    for child in widget.children:
        if isinstance(child, Widget):
            for action in _get_widget_actions(child):
                if action.name not in seen_names:
                    seen_names.add(action.name or "")
                    result.append(action)
            _collect_subtree_actions(child, seen_names, result)
