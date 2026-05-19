from __future__ import annotations

from enum import Enum, auto
from typing import Any

from textual.app import App

from nova_navigator.vfs import VPath


class ClipboardOperation(Enum):
    """Operation associated with a path clipboard entry."""

    COPY = auto()
    CUT = auto()


class PathClipboard:
    """Internal path clipboard for Cut / Copy / Paste operations.

    One persistent instance lives on ``NovaNavigator``.
    Calling ``set`` also writes the path URIs to the terminal OSC 52 clipboard
    via Textual's ``App.copy_to_clipboard``.
    """

    def __init__(self, app: App[Any]) -> None:
        self._app = app
        self._paths: tuple[VPath, ...] = ()
        self._operation: ClipboardOperation | None = None

    def set(self, paths: tuple[VPath, ...], operation: ClipboardOperation) -> None:
        """Store *paths* with *operation* and write URIs to the OSC 52 clipboard."""
        self._paths = paths
        self._operation = operation
        self._app.copy_to_clipboard("\n".join(p.uri for p in paths))

    def empty(self) -> bool:
        """Return ``True`` when no paths are stored."""
        return not self._paths

    def get(self) -> tuple[tuple[VPath, ...], ClipboardOperation]:
        """Return ``(paths, operation)``.

        Raises:
            ValueError: If the clipboard is empty.
        """
        if self.empty() or self._operation is None:
            raise ValueError("PathClipboard is empty")
        return self._paths, self._operation

    def clear(self) -> None:
        """Reset the clipboard to the empty state."""
        self._paths = ()
        self._operation = None
