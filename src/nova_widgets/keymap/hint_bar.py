"""HintBar widget — MC-style status bar with which-key overlay."""

from __future__ import annotations

from rich.text import Text
from textual.app import RenderResult
from textual.message import Message
from textual.reactive import reactive
from textual.widget import Widget

from nova_widgets.keymap.format import KeyDisplayStyle, format_key
from nova_widgets.menu._action import Action


class HintsChanged(Message):
    """Posted by any widget when its hint bar priorities change.

    The registry stores these per-widget so priorities are restored when focus
    returns.  Pass an empty dict to reset to default ordering.
    """

    def __init__(self, widget: Widget, priorities: dict[str, int]) -> None:
        super().__init__()
        self.widget = widget
        """The widget whose priorities changed."""
        self.priorities = priorities
        """Maps action name to effective bar_priority for the widget's current state."""


class HintBar(Widget):
    """Two-mode status bar.

    Normal mode: displays MC-style key/label badges for the current context.
    Chord-pending mode: displays the active prefix and available continuations.
    """

    DEFAULT_CSS = """
    HintBar {
        dock: bottom;
        width: 100%;
        background: $panel;
        color: $foreground;
        height: 1;
    }
    """

    is_chord_pending: reactive[bool] = reactive(False)

    def __init__(self) -> None:
        super().__init__()
        self._hints: list[Action] = []
        self._style: KeyDisplayStyle = KeyDisplayStyle.CLASSIC
        self._chord_prefix: str = ""
        self._chord_continuations: list[tuple[str, str | None]] = []

    def set_hints(self, actions: list[Action], style: KeyDisplayStyle) -> None:
        """Update the normal-mode hints.

        Args:
            actions: Pre-filtered and pre-sorted actions to display.
            style: Key display style.
        """
        self._hints = list(actions)
        self._style = style
        self.refresh()

    def show_chord_pending(
        self,
        prefix: str,
        continuations: list[tuple[str, str | None]],
    ) -> None:
        """Switch to chord-pending mode.

        Args:
            prefix: The key chord pressed so far, e.g. "ctrl+x".
            continuations: List of (next_key, action_name) tuples.
        """
        self._chord_prefix = prefix
        self._chord_continuations = continuations
        self.is_chord_pending = True
        self.refresh()

    def clear_chord(self) -> None:
        """Return to normal mode."""
        self._chord_prefix = ""
        self._chord_continuations = []
        self.is_chord_pending = False
        self.refresh()

    def render(self) -> RenderResult:
        if self.is_chord_pending:
            return self._render_chord_mode()
        return self._render_normal_mode()

    def _render_normal_mode(self) -> Text:
        text = Text(no_wrap=True, overflow="ellipsis")
        for action in self._hints:
            if action.shortcut is None:
                continue
            key_display = format_key(action.shortcut, self._style)
            text.append(f" {key_display} ", style="bold reverse")
            text.append(f" {action.text} ", style="")
            text.append(" ")
        return text

    def _render_chord_mode(self) -> Text:
        text = Text(no_wrap=True, overflow="ellipsis")
        prefix_display = format_key(self._chord_prefix, self._style)
        text.append(f" {prefix_display} → ", style="bold yellow")
        for key, action_name in self._chord_continuations:
            key_display = format_key(key, self._style)
            label = action_name.split(".")[-1].replace("_", " ").capitalize() if action_name else key
            text.append(f" {key_display} ", style="bold reverse")
            text.append(f" {label} ", style="")
        text.append("  [Esc] cancel", style="dim")
        return text
