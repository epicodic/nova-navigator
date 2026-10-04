"""Inline widgets of the nova_edit search: the bar that takes the needle and the status line that shows progress and result."""

from __future__ import annotations

import time
from collections.abc import Callable
from typing import ClassVar

from textual import events
from textual.widgets import Input, Static

from nova_editor.widget import NovaTextArea


class SearchBar(Input):
    """Input field for the search needle (Ctrl+F). Enter searches (the app handles it), Alt+C toggles the case, Escape closes it."""

    BINDINGS: ClassVar[list[tuple[str, str, str]]] = [("escape", "close", "Close"), ("alt+c", "toggle_case", "Case")]

    def __init__(self) -> None:
        super().__init__(id="search_bar")
        self.case_sensitive = True
        """Whether the next search distinguishes upper and lower case."""
        self._refresh_placeholder()

    def _refresh_placeholder(self) -> None:
        self.placeholder = "Search (case-sensitive)" if self.case_sensitive else "Search (ignore case)"

    def open(self) -> None:
        """Show the bar with the last needle selected and focus it."""
        self.display = True
        self.focus()
        self.select_all()

    def action_toggle_case(self) -> None:
        """Switch between case-sensitive and ignore-case searching."""
        self.case_sensitive = not self.case_sensitive
        self._refresh_placeholder()

    def action_close(self) -> None:
        """Hide the bar (the needle stays for the next search) and give the focus back to the editor."""
        self.display = False
        editors = self.app.query(NovaTextArea)
        if editors:
            editors.first().focus()


class SearchStatus(Static):
    """Status line of a search: progress while it runs, then a result line for 3 s. Hidden when idle."""

    RESULT_SECONDS: ClassVar[float] = 3.0

    def __init__(self) -> None:
        super().__init__("", id="search_status", markup=False)
        self.line = ""
        """The text shown (empty when idle)."""
        self.clock: Callable[[], float] = time.monotonic
        """Time source of the result timeout (tests replace it)."""
        self._expires: float | None = None

    def _on_mount(self, event: events.Mount) -> None:
        self.set_interval(0.1, self._tick)

    def _show(self, line: str) -> None:
        self.line = line
        self.update(line)
        self.display = True

    def show_progress(self, line: str) -> None:
        """Show the state of a running search; it stays until a result or `clear`."""
        self._expires = None
        self._show(line)

    def show_result(self, line: str) -> None:
        """Show a result line that disappears after `RESULT_SECONDS`."""
        self._expires = self.clock() + self.RESULT_SECONDS
        self._show(line)

    def clear(self) -> None:
        """Hide the line."""
        self._expires = None
        self.line = ""
        self.update("")
        self.display = False

    def _tick(self) -> None:
        if self._expires is not None and self.clock() >= self._expires:
            self.clear()
