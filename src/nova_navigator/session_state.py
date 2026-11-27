"""Session state persisted between runs: panel sort orders and the right panel's directory.

The module is Textual-free. Loading never raises, and a damaged or unusable value falls back to its default.
"""

from __future__ import annotations

import logging
import os
import tomllib
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

import tomlkit

from nova_navigator.vfs import VPath
from nova_navigator.vfs.filesystems.local import LocalFilesystem

_logger = logging.getLogger(__name__)

_STATE_FILE: Path = Path.home() / ".local" / "state" / "nova-navigator" / "state.toml"
SORT_COLUMN_NAMES: tuple[str, ...] = ("name", "size", "modified", "extension")


@dataclass(frozen=True)
class PanelState:
    """Sort order of one directory panel. ``None`` means the value was not stored."""

    sort_column: str | None = None
    sort_ascending: bool | None = None


@dataclass(frozen=True)
class SessionState:
    """State of both panels and the directory shown by the right panel."""

    left: PanelState = field(default_factory=PanelState)
    right: PanelState = field(default_factory=PanelState)
    right_path: str | None = None


def load_session_state() -> SessionState:
    """Load the session state, returning defaults for anything damaged or missing."""
    try:
        if not _STATE_FILE.is_file():
            return SessionState()
        data = tomllib.loads(_STATE_FILE.read_text(encoding="utf-8"))
    except (OSError, ValueError, RecursionError) as exc:
        _logger.warning("Ignoring unreadable session state %s: %s", _STATE_FILE, exc)
        return SessionState()
    return SessionState(
        left=_panel_from_table(data.get("left")),
        right=_panel_from_table(data.get("right")),
        right_path=_path_from_table(data.get("right")),
    )


def save_session_state(state: SessionState) -> None:
    """Save the session state. Failures are logged, never raised."""
    right = _panel_to_table(state.right)
    if state.right_path is not None:
        try:
            state.right_path.encode("utf-8")
        except UnicodeEncodeError:
            _logger.warning("Session state: right panel path is not valid UTF-8, not saved")
        else:
            right["path"] = state.right_path
    data = {"left": _panel_to_table(state.left), "right": right}
    payload = tomlkit.dumps(data).encode("utf-8")
    target = os.path.realpath(_STATE_FILE)
    try:
        filesystem = LocalFilesystem.singleton()
        writer = filesystem.write_atomic(VPath(target, filesystem))
        try:
            writer.write(payload)
        except BaseException:
            writer.abort()
            raise
        writer.close()
    except (OSError, ValueError) as exc:
        _logger.warning("Could not save session state to %s: %s", target, exc)


def usable_directory(path: str | None) -> str | None:
    """Return ``path`` if it can be listed and entered, otherwise ``None``."""
    if path is None:
        return None
    try:
        with os.scandir(path):
            pass
        entered = os.access(path, os.X_OK)
    except (OSError, ValueError):
        return None
    return path if entered else None


def _panel_from_table(table: Any) -> PanelState:
    """Build a panel state from its table, ignoring each invalid value on its own."""
    if not isinstance(table, dict):
        return PanelState()
    column = table.get("sort_column")
    ascending = table.get("sort_ascending")
    return PanelState(
        sort_column=column if isinstance(column, str) and column in SORT_COLUMN_NAMES else None,
        sort_ascending=ascending if isinstance(ascending, bool) else None,
    )


def _path_from_table(table: Any) -> str | None:
    """Return the stored directory if it is a string, absolute and free of NUL characters."""
    if not isinstance(table, dict):
        return None
    path = table.get("path")
    if not isinstance(path, str) or "\x00" in path or not os.path.isabs(path):
        return None
    return path


def _panel_to_table(panel: PanelState) -> dict[str, object]:
    table: dict[str, object] = {}
    if panel.sort_column is not None:
        table["sort_column"] = panel.sort_column
    if panel.sort_ascending is not None:
        table["sort_ascending"] = panel.sort_ascending
    return table
