"""Keybindings configuration — TOML load/save for user key overrides."""

from __future__ import annotations

from pathlib import Path

import tomlkit

from nova_widgets.action import Action
from nova_widgets.keymap.key_sequence import KeySequence

_FILENAME = "keybindings.toml"
_DEFAULT_CONFIG_DIR = Path.home() / ".config" / "nova-navigator"


class KeybindingsConfig:
    """Loads and saves user key binding overrides from keybindings.toml.

    Only deviations from defaults are stored in the file.
    """

    def __init__(self, config_dir: Path | None = None) -> None:
        self._config_dir = config_dir if config_dir is not None else _DEFAULT_CONFIG_DIR
        self._overrides: dict[str, KeySequence | None] = {}
        self._load()

    def resolve(self, actions: list[Action]) -> dict[str, KeySequence]:
        """Compute the effective {action_name: KeySequence} map.

        File overrides take precedence over Action.initial_shortcut.
        A None override explicitly removes a default binding.

        Args:
            actions: All known Action objects.

        Returns:
            Mapping of action name to effective KeySequence.
            Actions with no binding (unmapped or no default) are excluded.
        """
        result: dict[str, KeySequence] = {}
        for action in actions:
            if action.id is None:
                continue
            if action.id in self._overrides:
                value = self._overrides[action.id]
                if value is not None:  # None = explicitly unmapped
                    result[action.id] = value
            elif action.initial_shortcut is not None:
                result[action.id] = action.initial_shortcut
        return result

    def save(self, bindings: dict[str, KeySequence | None]) -> None:
        """Persist the given bindings to keybindings.toml.

        Args:
            bindings: Full effective binding map to save.
                      None values are written as empty strings to suppress defaults.
        """
        doc = tomlkit.document()
        bindings_table = tomlkit.table()
        for action_name, key_seq in sorted(bindings.items()):
            bindings_table.add(action_name, str(key_seq) if key_seq is not None else "")
        doc.add("bindings", bindings_table)

        self._config_dir.mkdir(parents=True, exist_ok=True)
        (self._config_dir / _FILENAME).write_text(tomlkit.dumps(doc))
        self._overrides = dict(bindings)

    def reload(self) -> None:
        """Re-read the file from disk."""
        self._load()

    def _load(self) -> None:
        file_path = self._config_dir / _FILENAME
        if not file_path.exists():
            self._overrides = {}
            return
        text = file_path.read_text()
        doc = tomlkit.loads(text)
        raw = doc.get("bindings", {})
        self._overrides = {str(k): (KeySequence.parse(str(v)) if str(v) else None) for k, v in raw.items()}
