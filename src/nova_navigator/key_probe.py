from __future__ import annotations

from datetime import datetime

from textual import events
from textual.app import App, ComposeResult
from textual.containers import Vertical
from textual.widgets import RichLog, Static

from nova_navigator.runtime_patches import apply_runtime_patches


class KeyProbeApp(App[None]):
    """Small terminal app that shows detected key events in a scrolling log."""

    CSS = """
    Screen {
        layout: vertical;
    }

    #header {
        height: auto;
        padding: 0 1;
    }

    #log {
        height: 1fr;
        border: round $accent;
    }
    """

    _log: RichLog

    def __init__(self) -> None:
        super().__init__()
        self._log = RichLog(id="log", wrap=False, auto_scroll=True, highlight=False)

    def compose(self) -> ComposeResult:
        yield Vertical(
            Static("Press keys to inspect Textual detection. Use Ctrl+C to quit.", id="header"),
            self._log,
        )

    def on_mount(self) -> None:
        self._log.write("timestamp | key | character | aliases")

    def on_key(self, event: events.Key) -> None:
        timestamp = datetime.now().strftime("%H:%M:%S.%f")[:-3]
        character = repr(event.character) if event.character is not None else "None"
        aliases = ",".join(event.aliases) if event.aliases else "-"
        self._log.write(f"{timestamp} | {event.key} | {character} | {aliases}")


def main() -> None:
    """Run the key probe tool with runtime patches enabled."""
    apply_runtime_patches()
    KeyProbeApp().run()


if __name__ == "__main__":
    main()
