"""Chord state machine for multi-key sequence handling."""

from __future__ import annotations

from dataclasses import dataclass, field


@dataclass
class TrieNode:
    """A node in the key-sequence trie."""

    children: dict[str, TrieNode] = field(default_factory=dict)
    action_name: str | None = None


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

    def build_trie(self, bindings: dict[str, str]) -> None:
        """Build the trie from a mapping of action_name to key_sequence.

        Args:
            bindings: Maps action name to key sequence string.
                      Key sequences use Textual notation; multi-chord sequences are
                      space-separated, e.g. "ctrl+x ctrl+s".
        """
        self._root = TrieNode()
        self._current = self._root

        for action_name, key_seq in bindings.items():
            if not key_seq:
                continue
            chords = key_seq.strip().split(" ")
            node = self._root
            for chord in chords:
                if chord not in node.children:
                    node.children[chord] = TrieNode()
                node = node.children[chord]
            node.action_name = action_name

    def feed(self, key: str) -> ChordResult:
        """Feed a single key press into the state machine.

        Args:
            key: Textual key name, e.g. "ctrl+x" or "f5".

        Returns:
            ChordResult indicating whether the key was consumed and what action matched.
        """
        if key == "escape":
            self._current = self._root
            return ChordResult(consumed=False)

        node = self._current.children.get(key)

        if node is None:
            self._current = self._root
            return ChordResult(consumed=False)

        if node.action_name is not None:
            # Leaf node — dispatch the action
            self._current = self._root
            return ChordResult(consumed=True, action_name=node.action_name)

        # Prefix node — enter pending state
        self._current = node
        continuations = [(k, child.action_name) for k, child in node.children.items()]
        return ChordResult(consumed=True, continuations=continuations)

    def reset(self) -> None:
        """Reset the state machine to IDLE."""
        self._current = self._root

    @property
    def is_pending(self) -> bool:
        """True when a prefix chord has been started."""
        return self._current is not self._root
