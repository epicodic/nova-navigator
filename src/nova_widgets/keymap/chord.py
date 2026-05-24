"""Chord state machine for multi-key sequence handling."""

from __future__ import annotations

from dataclasses import dataclass, field


def _context_matches(registered: set[str], current: str) -> bool:
    """Return True if *current* matches any registered context (hierarchically).

    A registered context of ``"browser"`` matches the exact context ``"browser"``
    and any sub-context like ``"browser.selection"`` or ``"browser.detail"``.
    Wildcards are not needed: specifying a parent context covers all children.
    """
    return current in registered or any(current.startswith(c + ".") for c in registered)


@dataclass
class TrieNode:
    """A node in the key-sequence trie."""

    children: dict[str, TrieNode] = field(default_factory=dict)
    action_name: str | None = None
    reachable_contexts: set[str] = field(default_factory=set)


@dataclass
class ChordResult:
    """Result of feeding a key to the ChordStateMachine."""

    consumed: bool
    action_name: str | None = None
    continuations: list[tuple[str, str | None]] | None = None


class ChordStateMachine:
    """Trie-based state machine for Emacs-style multi-chord key sequences."""

    def __init__(self) -> None:
        self._root = TrieNode()
        self._current: TrieNode = self._root

    def build_trie(self, bindings: dict[str, tuple[str, list[str]]]) -> None:
        """Build the trie from a mapping of action_name to (key_sequence, contexts).

        Args:
            bindings: Maps action name to (key_sequence, list_of_context_strings).
                      Key sequences use Textual notation; multi-chord chords are
                      space-separated, e.g. "ctrl+x ctrl+s".
        """
        self._root = TrieNode()
        self._current = self._root

        for action_name, (key_seq, contexts) in bindings.items():
            if not key_seq:
                continue
            chords = key_seq.strip().split(" ")
            node = self._root
            for chord in chords:
                if chord not in node.children:
                    node.children[chord] = TrieNode()
                node = node.children[chord]
            node.action_name = action_name
            node.reachable_contexts = set(contexts)

        self._propagate_contexts(self._root)

    def feed(self, key: str, context: str) -> ChordResult:
        """Feed a single key press and current context into the state machine.

        Args:
            key: Textual key name, e.g. "ctrl+x" or "f5".
            context: Current application context string, e.g. "browser".

        Returns:
            ChordResult indicating whether the key was consumed and what action matched.
        """
        if key == "escape":
            self._current = self._root
            return ChordResult(consumed=False)

        node = self._current.children.get(key)

        if node is None or not _context_matches(node.reachable_contexts, context):
            self._current = self._root
            return ChordResult(consumed=False)

        if node.action_name is not None:
            # Leaf node — dispatch the action
            self._current = self._root
            return ChordResult(consumed=True, action_name=node.action_name)

        # Prefix node — enter pending state
        self._current = node
        continuations = [
            (k, child.action_name)
            for k, child in node.children.items()
            if _context_matches(child.reachable_contexts, context)
        ]
        return ChordResult(consumed=True, continuations=continuations)

    def reset(self) -> None:
        """Reset the state machine to IDLE."""
        self._current = self._root

    @property
    def is_pending(self) -> bool:
        """True when a prefix chord has been started."""
        return self._current is not self._root

    def _propagate_contexts(self, node: TrieNode) -> set[str]:
        contexts: set[str] = set(node.reachable_contexts)
        for child in node.children.values():
            contexts |= self._propagate_contexts(child)
        node.reachable_contexts = contexts
        return contexts
