import math

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
        super().__init__(glyph.markup)
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

    def icon_static(self, glyph: Icon) -> None:
        """Display *glyph* statically; stop any running animation."""
        self._stop_timer()
        self._static_glyph = glyph
        self.update(glyph.markup)
        self._frames = []

    def icon_animate(self, frames: list[Icon], interval: float) -> None:
        """Cycle through *frames* at *interval* seconds per frame."""
        self._stop_timer()
        self._frames = frames
        self._frame_index = 0
        if frames:
            self.update(frames[0].markup)
        self._timer = self.set_interval(interval, self._advance_frame)

    def icon_pulse(
        self,
        glyph: str,
        *,
        bright: tuple[int, int, int],
        dim: tuple[int, int, int],
        n: int = 12,
        interval: float = 0.1,
    ) -> None:
        """Animate *glyph* pulsing between *bright* and *dim* colours via sin()."""
        frames: list[Icon] = []
        for i in range(n):
            t = i / n * 2 * math.pi
            blend = (math.sin(t) + 1) / 2
            r = round(dim[0] + (bright[0] - dim[0]) * blend)
            g = round(dim[1] + (bright[1] - dim[1]) * blend)
            b = round(dim[2] + (bright[2] - dim[2]) * blend)
            frames.append(Icon(glyph, color=(r, g, b)))
        self.icon_animate(frames, interval)

    def stop_icon_animation(self) -> None:
        """Stop animation and restore the static glyph."""
        self._stop_timer()
        self.update(self._static_glyph.markup)
        self._frames = []

    def _stop_timer(self) -> None:
        if self._timer is not None:
            self._timer.stop()
            self._timer = None

    def _advance_frame(self) -> None:
        if not self._frames:
            return
        self._frame_index = (self._frame_index + 1) % len(self._frames)
        self.update(self._frames[self._frame_index].markup)

    def _on_unmount(self) -> None:
        self._stop_timer()

    async def _on_mouse_down(self, event: MouseDown) -> None:
        event.stop()
        event.prevent_default()
        if self._action is not None:
            await self.app.screen.run_action(self._action)
