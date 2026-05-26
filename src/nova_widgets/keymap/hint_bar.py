"""HintBar widget — MC-style status bar with which-key overlay."""

from __future__ import annotations

from rich.text import Text
from textual.app import RenderResult
from textual.message import Message
from textual.reactive import reactive
from textual.widget import Widget

from nova_widgets.keymap.key_sequence import KeyFormatStyle, KeySequence
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
        self._style: KeyFormatStyle = KeyFormatStyle.CLASSIC
        self._chord_prefix: KeySequence = KeySequence(())
        self._chord_continuations: list[Action] = []

    def set_hints(self, actions: list[Action], style: KeyFormatStyle) -> None:
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
        prefix: KeySequence,
        continuations: list[Action],
    ) -> None:
        """Switch to chord-pending mode.

        Args:
            prefix: The key sequence pressed so far.
            continuations: List of actions available as chord continuations.
        """
        self._chord_prefix = prefix
        self._chord_continuations = continuations
        self.is_chord_pending = True
        self.refresh()

    def clear_chord(self) -> None:
        """Return to normal mode."""
        self._chord_prefix = KeySequence(())
        self._chord_continuations = []
        self.is_chord_pending = False
        self.refresh()

    def render(self) -> RenderResult:
        if self.is_chord_pending:
            return self._render_key_sequence_mode()
        return self._render_normal_mode()

    def _render_normal_mode(self) -> Text:
        text = Text(no_wrap=True, overflow="ellipsis")
        for action in self._hints:
            if action.shortcut is None:
                continue
            key_display = action.shortcut.format(self._style)
            text.append(f" {key_display} ", style="bold reverse")
            text.append(f" {action.text} ", style="")
            text.append(" ")
        return text

    def _render_key_sequence_mode(self) -> Text:
        text = Text(no_wrap=True, overflow="ellipsis")
        prefix_display = self._chord_prefix.format(self._style)
        text.append(f" {prefix_display} → ", style="bold yellow")
        for action in self._chord_continuations:
            if action.shortcut is None:
                continue
            remaining = action.shortcut.suffix_after(self._chord_prefix)
            key_display = remaining.format(self._style) or str(action.shortcut)
            text.append(f" {key_display} ", style="bold reverse")
            text.append(f" {action.text} ", style="")
        text.append("  [Esc] cancel", style="dim")
        return text
