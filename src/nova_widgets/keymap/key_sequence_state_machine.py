"""Key-sequence state machine for Emacs-style multi-chord key sequence handling."""

from __future__ import annotations

from dataclasses import dataclass, field

from nova_widgets.keymap.key_sequence import KeyChord, KeySequence


@dataclass
class TrieNode:
    """A node in the key-sequence trie."""

    children: dict[KeyChord, TrieNode] = field(default_factory=dict)
    action_name: str | None = None


@dataclass
class SequenceResult:
    """Result of feeding a key chord to the KeySequenceStateMachine."""

    consumed: bool
    action_name: str | None = None
    continuations: list[tuple[KeyChord, str | None]] | None = None


class KeySequenceStateMachine:
    """Trie-based state machine for Emacs-style key sequences.

    A key sequence is a series of key chords pressed one after another,
    e.g. Ctrl+X followed by Ctrl+S.
    """

    def __init__(self) -> None:
        self._root = TrieNode()
        self._current: TrieNode = self._root

    def build_trie(self, bindings: dict[str, KeySequence]) -> None:
        """Build the trie from a mapping of action_name to KeySequence.

        Args:
            bindings: Maps action name to KeySequence.
        """
        self._root = TrieNode()
        self._current = self._root

        for action_name, sequence in bindings.items():
            node = self._root
            for chord in sequence.chords:
                if chord not in node.children:
                    node.children[chord] = TrieNode()
                node = node.children[chord]
            node.action_name = action_name

    def feed(self, chord: KeyChord) -> SequenceResult:
        """Feed a single key chord into the state machine.

        Args:
            chord: The key chord pressed, e.g. KeyChord.parse("ctrl+x").

        Returns:
            SequenceResult indicating whether the chord was consumed and what
            action matched.
        """
        if any(k.name == "escape" for k in chord.keys):
            self._current = self._root
            return SequenceResult(consumed=False)

        node = self._current.children.get(chord)

        if node is None:
            self._current = self._root
            return SequenceResult(consumed=False)

        if node.action_name is not None:
            # Leaf node — dispatch the action
            self._current = self._root
            return SequenceResult(consumed=True, action_name=node.action_name)

        # Prefix node — enter pending state
        self._current = node
        continuations = [(kc, child.action_name) for kc, child in node.children.items()]
        return SequenceResult(consumed=True, continuations=continuations)

    def reset(self) -> None:
        """Reset the state machine to IDLE."""
        self._current = self._root

    @property
    def is_pending(self) -> bool:
        """True when a prefix chord has been pressed and the sequence is incomplete."""
        return self._current is not self._root
