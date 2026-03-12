from __future__ import annotations

from enum import Enum, auto
from typing import TYPE_CHECKING

from textual.app import ComposeResult
from textual.timer import Timer
from textual.widget import Widget

if TYPE_CHECKING:
    from nova_navigator.dialogs.job_registry import JobRegistry

from nova_navigator.icons import ico_
from nova_navigator.scheduler import Job
from nova_widgets.animated_icon import AnimatedIcon
from nova_widgets.icon import Icon

_RUNNING_INTERVAL: float = 0.15
_FAILED_BRIGHT: tuple[int, int, int] = (255, 60, 60)
_FAILED_DIM: tuple[int, int, int] = (140, 10, 10)
_FAILED_N: int = 12
_FAILED_INTERVAL: float = 0.1


class _State(Enum):
    IDLE = auto()
    RUNNING = auto()
    FAILED = auto()


class JobStatusIcon(Widget):
    """Menu-bar icon that reflects the current job registry state.

    Polls the registry every 0.5 seconds and drives an AnimatedIcon:
    - IDLE: static idle glyph
    - RUNNING: animated spinner
    - FAILED: static error glyph (persists until failed job is dismissed)
    """

    DEFAULT_CSS = """
    JobStatusIcon {
        width: auto;
        height: 1;
    }
    """

    _registry: JobRegistry
    _action: str
    _current_state: _State
    _animated_icon: AnimatedIcon
    _poll_timer: Timer | None

    def __init__(self, registry: JobRegistry, action: str) -> None:
        super().__init__()
        self._registry = registry
        self._action = action
        self._current_state = _State.IDLE
        self._poll_timer = None

    def compose(self) -> ComposeResult:
        self._animated_icon = AnimatedIcon(
            ico_("job-spinner_full", default=Icon.of("●")),
            action=self._action,
        )
        yield self._animated_icon

    def on_mount(self) -> None:
        self._poll_timer = self.set_interval(0.5, self._update)

    def _on_unmount(self) -> None:
        if self._poll_timer is not None:
            self._poll_timer.stop()
            self._poll_timer = None

    def _compute_state(self) -> _State:
        finished = self._registry.finished_jobs
        if any(j.state == Job.State.FAILED for j in finished):
            return _State.FAILED
        if self._registry.running_jobs:
            return _State.RUNNING
        return _State.IDLE

    def _update(self) -> None:
        new_state = self._compute_state()
        if new_state == self._current_state:
            return
        self._current_state = new_state
        match new_state:
            case _State.IDLE:
                self._animated_icon.icon_static(ico_("job-spinner_full", default=Icon.of("●")))
            case _State.RUNNING:
                self._animated_icon.icon_animate(
                    ico_("job-spinner", default=Icon.from_glyphs(["○", "◔", "◑", "◕", "●"])),
                    _RUNNING_INTERVAL,
                )
            case _State.FAILED:
                self._animated_icon.icon_pulse(
                    ico_("job-error", default=Icon.of("⚠")),
                    bright=_FAILED_BRIGHT,
                    dim=_FAILED_DIM,
                    n=_FAILED_N,
                    interval=_FAILED_INTERVAL,
                )
