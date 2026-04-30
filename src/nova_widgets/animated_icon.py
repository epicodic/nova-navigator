from textual.events import MouseDown
from textual.timer import Timer
from textual.widgets import Static

from .icon import Icon


class AnimatedIcon(Static):
    """A fixed-width icon widget that can animate through a list of glyphs.

    Displays a single :class:`~nova_widgets.icon.Icon` glyph.
    Optionally cycles through a list of frames to produce an animation.
    If *action* is given, clicking the widget calls that app action.
    """

    DEFAULT_CSS = """
    AnimatedIcon {
        width: auto;
        content-align: center middle;
        padding: 0 1;

        &:hover {
            background: $panel-lighten-2;
            color: $text;
        }
    }
    """

    _static_glyph: Icon
    _action: str | None
    _timer: Timer | None
    _frame_index: int
    _frames: list[Icon]

    def __init__(
        self,
        glyph: Icon,
        *,
        action: str | None = None,
        tooltip: str | None = None,
    ) -> None:
        super().__init__(str(glyph))
        self._static_glyph = glyph
        self._action = action
        self._timer = None
        self._frame_index = 0
        self._frames = []
        if tooltip is not None:
            self.tooltip = tooltip

    @property
    def renderable(self) -> str:
        """Return the currently displayed content as a string."""
        return str(self.content)

    def set_glyph(self, glyph: Icon) -> None:
        """Display *glyph* statically; stop any running animation."""
        self._stop_timer()
        self._static_glyph = glyph
        self.update(str(glyph))
        self._frames = []

    def set_animation(self, frames: list[Icon], interval: float) -> None:
        """Cycle through *frames* at *interval* seconds per frame."""
        self._stop_timer()
        self._frames = frames
        self._frame_index = 0
        if frames:
            self.update(str(frames[0]))
        self._timer = self.set_interval(interval, self._advance_frame)

    def stop_icon_animation(self) -> None:
        """Stop animation and restore the static glyph."""
        self._stop_timer()
        self.update(str(self._static_glyph))
        self._frames = []

    def _stop_timer(self) -> None:
        if self._timer is not None:
            self._timer.stop()
            self._timer = None

    def _advance_frame(self) -> None:
        if not self._frames:
            return
        self._frame_index = (self._frame_index + 1) % len(self._frames)
        self.update(str(self._frames[self._frame_index]))

    def _on_unmount(self) -> None:
        self._stop_timer()

    async def _on_mouse_down(self, event: MouseDown) -> None:
        event.stop()
        event.prevent_default()
        if self._action is not None:
            await self.app.run_action(self._action)
