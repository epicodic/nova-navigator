import time
from collections.abc import Callable, Sequence
from typing import ClassVar, Final

from rich.segment import Segment
from rich.style import Style
from textual.app import ComposeResult
from textual.color import Color
from textual.containers import Vertical
from textual.events import Leave, MouseDown, MouseMove
from textual.geometry import Size
from textual.scroll_view import ScrollView
from textual.strip import Strip

from nova_navigator.scheduler import Job
from nova_widgets import Button

from ..widgets.popup_widget import PopupWidget
from .job_registry import JobRegistry


def _generate_progress_bar_segments(ratio: float, width: int) -> tuple[str, str]:
    """Return (fill_text, muted_text) for a sub-character-precision progress bar.

    Characters:
    - ━  full stroke (1.0 cell width) — used for both filled and empty runs
    - ╸  left-half stroke (0.5 cells) — trailing edge of fill; part of fill_text
    - ╺  right-half stroke (0.5 cells) — leading edge of empty; part of muted_text

    There is always exactly one half-width boundary character between the filled
    and empty regions.  len(fill_text) + len(muted_text) always equals *width*.
    """
    _BAR: Final = "━"
    _HALF_LEFT: Final = "╺"  # right-half stroke — leading edge of the empty region
    _HALF_RIGHT: Final = "╸"  # left-half stroke — trailing edge of the filled region

    p_half: int = round(ratio * width * 2)  # quantise to half-cell steps
    if p_half <= 0:
        return "", _BAR * width
    if p_half >= width * 2:
        return _BAR * width, ""
    full = p_half // 2
    has_half = bool(p_half % 2)
    empty = width - full - 1
    if has_half:
        return _BAR * full + _HALF_RIGHT, _BAR * empty
    return _BAR * full, _HALF_LEFT + _BAR * empty


class JobListView(ScrollView, can_focus=False):
    """A virtualised list of job rows. No child widgets — all rendering is done in render_line()."""

    ROW_HEIGHT: ClassVar[int] = 4
    ITEM_HEIGHT: ClassVar[int] = 5  # ROW_HEIGHT + 1 separator line
    RIGHT_PADDING: ClassVar[int] = 1  # keeps content clear of the scrollbar overlay

    COMPONENT_CLASSES: ClassVar[set[str]] = {
        "job-list--running",
        "job-list--completed",
        "job-list--canceled",
        "job-list--failed",
        "job-list--separator",
        "job-list--muted",
        "job-list--error",
        "job-list--bar-fill",
        "job-list--bar-complete",
        "job-list--bar-muted",
        "job-list--btn",
        "job-list--btn-hover",
    }

    DEFAULT_CSS = """
    JobListView {
        height: 1fr;
        border: none;
        padding: 0;
    }
    JobListView .job-list--running    { background: $panel; }
    JobListView .job-list--completed  { background: $success 20%; }
    JobListView .job-list--canceled   { background: $error 15%; }
    JobListView .job-list--failed     { background: $error 20%; }
    JobListView .job-list--separator  { color: $panel-lighten-2; }
    JobListView .job-list--muted      { color: $text-muted; }
    JobListView .job-list--error      { color: $error; }
    JobListView .job-list--bar-fill   { color: $primary; }
    JobListView .job-list--bar-complete { color: $success; }
    JobListView .job-list--bar-muted  { color: $primary 20%; }
    JobListView .job-list--btn        { background: $panel-lighten-2; }
    JobListView .job-list--btn-hover  { background: $primary; }
    """

    _jobs: list[Job]
    _start_times: dict[int, float]
    _on_action: Callable[[Job], None]
    _cached_theme: str
    _styles_by_state: dict[Job.State, Style]
    _hover_styles_by_state: dict[Job.State, Style]
    _bar_muted_style: Style
    _btn_style: Style
    _btn_hover_style: Style
    _hovered_job_index: int | None
    _hovered_btn: bool

    def __init__(self, on_action: Callable[[Job], None] | None = None) -> None:
        super().__init__()
        self._jobs = []
        self._start_times = {}
        self._on_action = on_action if on_action is not None else (lambda _: None)
        self._cached_theme = ""
        self._styles_by_state = {}
        self._hover_styles_by_state = {}
        self._bar_muted_style = Style()
        self._btn_style = Style()
        self._btn_hover_style = Style()
        self._hovered_job_index = None
        self._hovered_btn = False

    def set_jobs(self, jobs: Sequence[Job]) -> None:
        """Replace the displayed job list and trigger a repaint."""
        live_ids = {id(j) for j in jobs}
        self._start_times = {k: v for k, v in self._start_times.items() if k in live_ids}
        self._jobs = list(jobs)
        height = max(1, len(jobs) * self.ITEM_HEIGHT)
        self.virtual_size = Size(self.size.width or 80, height)
        self.refresh()

    # ── rendering ─────────────────────────────────────────────────────────────

    def _resolve_theme_styles(self) -> None:
        """Compute alpha-composited styles from CSS component classes; cached per theme name.

        Textual's get_component_rich_style drops alpha when converting to Rich Style.
        We read the raw textual.color.Color (with .a preserved) from get_component_styles()
        and composite it manually against the opaque panel background.
        """
        current = self.app.theme
        if current == self._cached_theme:
            return
        self._cached_theme = current

        # $panel is fully opaque — use it as the compositing base for all blended colors.
        panel = self.get_component_styles("job-list--running").background

        def composite_bg(name: str) -> Style:
            bg = self.get_component_styles(name).background
            if bg.a < 1.0:
                bg = bg.blend(panel, 1 - bg.a, alpha=1.0)
            return Style(bgcolor=bg.rich_color)

        def composite_color(name: str) -> Style:
            styles = self.get_component_styles(name)
            if not styles.has_rule("color"):
                return Style()
            col = styles.color
            if col.a < 1.0:
                col = col.blend(panel, 1 - col.a, alpha=1.0)
            return Style(color=col.rich_color)

        self._styles_by_state = {
            Job.State.RUNNING: composite_bg("job-list--running"),
            Job.State.COMPLETED: composite_bg("job-list--completed"),
            Job.State.CANCELED: composite_bg("job-list--canceled"),
            Job.State.FAILED: composite_bg("job-list--failed"),
        }
        self._bar_muted_style = composite_color("job-list--bar-muted")

        white = Color(255, 255, 255)
        self._hover_styles_by_state = {}
        for state, state_style in self._styles_by_state.items():
            if state_style.bgcolor is not None:
                bg = Color.from_rich_color(state_style.bgcolor)
                self._hover_styles_by_state[state] = Style(bgcolor=bg.blend(white, 0.1).rich_color)
            else:
                self._hover_styles_by_state[state] = state_style

        self._btn_style = composite_bg("job-list--btn")
        self._btn_hover_style = composite_bg("job-list--btn-hover")

    def render_line(self, y: int) -> Strip:
        self._resolve_theme_styles()
        _scroll_x, scroll_y = self.scroll_offset
        logical_y = y + scroll_y

        if not self._jobs:
            if logical_y == 0:
                text = "No jobs".center(self.size.width or 80)
                return Strip([Segment(text, style=self.rich_style)])
            return Strip([Segment(" " * (self.size.width or 80), style=self.rich_style)])

        job_index = logical_y // self.ITEM_HEIGHT
        y_in_item = logical_y % self.ITEM_HEIGHT

        if job_index >= len(self._jobs):
            return Strip([Segment(" " * (self.size.width or 80), style=self.rich_style)])

        job = self._jobs[job_index]
        return self._render_job_line(job, job_index, y_in_item)

    def _render_job_line(self, job: Job, job_index: int, y_in_item: int) -> Strip:
        full_width = self.size.width or 80
        width = max(1, full_width - self.RIGHT_PADDING)
        is_hovered = job_index == self._hovered_job_index
        styles_map = self._hover_styles_by_state if is_hovered else self._styles_by_state
        bg_style = styles_map.get(job.state, Style())
        base = self.rich_style + bg_style

        if y_in_item == self.ROW_HEIGHT:  # separator line — always neutral panel bg
            sep_base = self.rich_style + self._styles_by_state.get(Job.State.RUNNING, Style())
            sep_style = self.get_component_rich_style("job-list--separator", partial=True)
            strip = Strip([Segment("─" * width, style=sep_base + sep_style)])
            return strip.extend_cell_length(full_width, style=sep_base)

        if y_in_item == 0:
            strip = self._render_header_line(job, job_index, base, width)
        elif y_in_item == 1:
            strip = self._render_item_line(job, base, width)
        elif y_in_item == self.ROW_HEIGHT - 2:
            strip = self._render_step_bar_line(job, base, width)
        else:  # y_in_item == ROW_HEIGHT - 1
            strip = self._render_overall_bar_line(job, base, width)
        return strip.extend_cell_length(full_width, style=base)

    def _render_header_line(self, job: Job, job_index: int, base: Style, width: int) -> Strip:
        eta = self._eta_str(job)
        eta_width = 6  # e.g. " 9:59" or "59:59" with leading space = 6 chars
        gap_width = 1  # space between ETA and button
        btn_width = 3  # " ✕ "
        title_width = max(1, width - eta_width - gap_width - btn_width)

        title_text = self._display_title(job)
        title_seg = Segment(title_text[:title_width].ljust(title_width), style=base)

        eta_text = eta.rjust(eta_width) if eta else " " * eta_width
        muted = self.get_component_rich_style("job-list--muted", partial=True)
        eta_seg = Segment(eta_text, style=base + muted)
        gap_seg = Segment(" ", style=base)

        btn_icon = self._button_icon(job.state)
        btn_text = f" {btn_icon} "
        btn_meta = Style.from_meta({"job_index": job_index, "action_button": True})
        is_btn_hovered = job_index == self._hovered_job_index and self._hovered_btn
        btn_bg = self._btn_hover_style if is_btn_hovered else self._btn_style
        btn_seg = Segment(btn_text, style=base + btn_bg + btn_meta)

        return Strip([title_seg, eta_seg, gap_seg, btn_seg])

    def _render_item_line(self, job: Job, base: Style, width: int) -> Strip:
        indent = "  "
        if job.state == Job.State.FAILED and job.error:
            err_style = self.get_component_rich_style("job-list--error", partial=True)
            text = (indent + job.error.split("\n")[0])[:width].ljust(width)
            return Strip([Segment(text, style=base + err_style)])
        muted = self.get_component_rich_style("job-list--muted", partial=True)
        item = job.progress.current_item
        available = width - len(indent)
        if len(item) > available:
            item = "…" + item[-(available - 1) :]
        text = (indent + item).ljust(width)
        return Strip([Segment(text, style=base + muted)])

    def _render_step_bar_line(self, job: Job, base: Style, width: int) -> Strip:
        if job.state == Job.State.FAILED and job.error:
            err_style = self.get_component_rich_style("job-list--error", partial=True)
            lines = (job.error or "").split("\n")
            text = ("  " + (lines[1] if len(lines) > 1 else ""))[:width].ljust(width)
            return Strip([Segment(text, style=base + err_style)])

        indent = "  "
        pct_width = 5  # " XX%"
        count_width = 9  # matches overall bar layout so both bars are the same length
        bar_width = max(1, width - len(indent) - count_width - pct_width)
        progress = job.progress
        step_total = max(1, progress.step_total)
        ratio = progress.step_completed / step_total
        pct = f" {int(ratio * 100):3d}%"
        fill_cls = "job-list--bar-complete" if ratio >= 1.0 else "job-list--bar-fill"
        bar_style = self.get_component_rich_style(fill_cls, partial=True)
        bar_muted = self._bar_muted_style
        fill_text, muted_text = _generate_progress_bar_segments(ratio, bar_width)
        bar_segs: list[Segment] = []
        if fill_text:
            bar_segs.append(Segment(fill_text, style=base + bar_style))
        if muted_text:
            bar_segs.append(Segment(muted_text, style=base + bar_muted))
        return Strip(
            [
                Segment(indent, style=base),
                *bar_segs,
                Segment(" " * count_width, style=base),
                Segment(pct, style=base),
            ]
        )

    def _render_overall_bar_line(self, job: Job, base: Style, width: int) -> Strip:
        indent = "  "
        pct_width = 5  # " XX%"
        count_width = 9  # "  NNN/MMM" right-justified
        bar_width = max(1, width - len(indent) - count_width - pct_width)
        progress = job.progress
        overall_total = max(1, progress.total)
        bar_ratio = progress.effective_completed / overall_total
        display_pct = int(bar_ratio * 100)
        count = f"{progress.completed}/{overall_total}".rjust(count_width)
        pct = f" {display_pct:3d}%"
        fill_cls = "job-list--bar-complete" if bar_ratio >= 1.0 else "job-list--bar-fill"
        bar_style = self.get_component_rich_style(fill_cls, partial=True)
        bar_muted = self._bar_muted_style
        fill_text, muted_text = _generate_progress_bar_segments(bar_ratio, bar_width)
        bar_segs: list[Segment] = []
        if fill_text:
            bar_segs.append(Segment(fill_text, style=base + bar_style))
        if muted_text:
            bar_segs.append(Segment(muted_text, style=base + bar_muted))
        return Strip([Segment(indent, style=base), *bar_segs, Segment(count, style=base), Segment(pct, style=base)])

    # ── helpers ───────────────────────────────────────────────────────────────

    def _eta_str(self, job: Job) -> str:
        if job.state != Job.State.RUNNING:
            return ""
        progress = job.progress
        if progress.total == 0:
            return ""
        # Use fractional progress to match the value shown by the overall bar.
        effective_completed = progress.effective_completed
        if effective_completed == 0:
            return ""
        job_id = id(job)
        if job_id not in self._start_times:
            self._start_times[job_id] = time.monotonic()
            return ""
        elapsed = time.monotonic() - self._start_times[job_id]
        if elapsed < 1:
            return ""
        rate = effective_completed / elapsed
        remaining = (progress.total - effective_completed) / rate
        if remaining <= 0:
            return ""
        m, s = divmod(int(remaining), 60)
        return f"{m}:{s:02d}"

    @staticmethod
    def _display_title(job: Job) -> str:
        match job.state:
            case Job.State.CANCELED:
                return f"{job.title} (canceled)"
            case Job.State.FAILED:
                return f"{job.title} (failed)"
            case _:
                return job.title

    @staticmethod
    def _button_icon(state: Job.State) -> str:
        return "✕" if state == Job.State.RUNNING else ("✓" if state == Job.State.COMPLETED else "✗")

    # ── mouse handling ────────────────────────────────────────────────────────

    def _on_mouse_move(self, event: MouseMove) -> None:
        _scroll_x, scroll_y = self.scroll_offset
        logical_y = event.y + scroll_y
        job_index = logical_y // self.ITEM_HEIGHT
        y_in_item = logical_y % self.ITEM_HEIGHT
        btn_width = 3
        new_hover = job_index if 0 <= job_index < len(self._jobs) else None
        content_right = (self.size.width or 80) - self.RIGHT_PADDING
        new_btn = new_hover is not None and y_in_item == 0 and event.x >= content_right - btn_width
        if new_hover != self._hovered_job_index or new_btn != self._hovered_btn:
            self._hovered_job_index = new_hover
            self._hovered_btn = new_btn
            self.refresh()
        event.stop()

    def _on_leave(self, event: Leave) -> None:
        changed = self._hovered_job_index is not None or self._hovered_btn
        self._hovered_job_index = None
        self._hovered_btn = False
        if changed:
            self.refresh()

    async def _on_mouse_down(self, event: MouseDown) -> None:
        meta = event.style.meta
        if meta.get("action_button"):
            job_index = meta["job_index"]
            if 0 <= job_index < len(self._jobs):
                self._on_action(self._jobs[job_index])
                event.stop()


class JobsDialog(PopupWidget, can_focus=True):
    """Floating overlay that shows running and finished jobs."""

    _DIALOG_WIDTH: int = 62
    _DIALOG_MARGIN_TOP: int = 1
    _DIALOG_MARGIN_RIGHT: int = 0

    CLOSE_ON_BLUR = False
    SHOW_CLOSE_BUTTON = True

    DEFAULT_CSS = """
    JobsDialog {
        width: 62;
        height: auto;
        max-height: 31;
    }
    JobsDialog JobListView {
        height: auto;
        max-height: 28;
    }
    JobsDialog #btn_clear_all {
        width: 100%;
        height: 1;
        border: none;
        background: $panel-lighten-1;
        color: $text-muted;
    }
    """

    _registry: JobRegistry
    _job_list: JobListView

    def __init__(self, position: tuple[int, int], registry: JobRegistry) -> None:
        super().__init__("Jobs", position)
        self._registry = registry
        self._job_list = JobListView(on_action=self._handle_action)

    def compose(self) -> ComposeResult:
        yield Vertical(
            self._job_list,
            Button("Clear all", id="btn_clear_all"),
        )

    def _update_position(self) -> None:
        x = self.screen.size.width - self._DIALOG_WIDTH - self._DIALOG_MARGIN_RIGHT
        self.offset = (max(0, x), self._DIALOG_MARGIN_TOP)

    def show(self) -> None:
        self._update_position()
        super().show()
        self.call_after_refresh(self._tick)

    def on_mount(self) -> None:
        self.display = False
        self.set_interval(1.0, self._tick)

    async def _tick(self) -> None:
        self._registry.update()
        if not self.display:
            return
        desired = self._registry.running_jobs + self._registry.finished_jobs
        self._job_list.set_jobs(desired)

    def _handle_action(self, job: Job) -> None:
        if job.state == Job.State.RUNNING:
            job.cancel()
        else:
            self._registry.remove_job(job)
            # re-render immediately without waiting for next tick
            desired = self._registry.running_jobs + self._registry.finished_jobs
            self._job_list.set_jobs(desired)

    def on_button_pressed(self, event: Button.Pressed) -> None:
        if event.button.id != "btn_clear_all":
            return
        self._registry.clear_finished()
        # re-render immediately without waiting for next tick
        desired = self._registry.running_jobs + self._registry.finished_jobs
        self._job_list.set_jobs(desired)
