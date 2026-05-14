# Job Status Icon in Menu Bar — Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use skills:subagent-driven-development (recommended) or skills:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Add a clickable, animated job-status icon to the right side of the menu bar that reflects idle/running/failed state from `JobRegistry`.

**Architecture:** A generic `AnimatedIcon` widget (nova_widgets) handles glyph display and animation. `MenuBar.add_right_widget()` exposes a right-aligned slot. `JobStatusIcon` (nova_navigator/widgets) polls `JobRegistry` every 0.5 s and drives the `AnimatedIcon` state. `MainScreen` wires everything together.

**Spec:** `docs/agents/specs/2026-04-30-job-status-icon-design.md`

**Tech Stack:** Python 3.12, Textual, pytest

**Coding Conventions:** `docs/coding_conventions.md` — read before implementing

---

## File Map

| Action | File |
|---|---|
| **Create** | `src/nova_widgets/animated_icon.py` |
| **Modify** | `src/nova_widgets/__init__.py` |
| **Modify** | `src/nova_widgets/menu/_menu_bar.py` |
| **Create** | `src/nova_navigator/widgets/job_status_icon.py` |
| **Modify** | `src/nova_navigator/widgets/__init__.py` |
| **Modify** | `src/nova_navigator/nova_navigator.py` |
| **Create** | `tests/nova_widgets/test_animated_icon.py` |
| **Modify** | `tests/nova_widgets/test_menu_bar.py` |
| **Create** | `tests/widgets/test_job_status_icon.py` |

---

## Task 1: `AnimatedIcon` widget

**Files:**
- Create: `src/nova_widgets/animated_icon.py`
- Test: `tests/nova_widgets/test_animated_icon.py`

- [ ] **Step 1: Write failing tests**

Create `tests/nova_widgets/test_animated_icon.py`:

```python
import pytest
from textual.app import App, ComposeResult

from nova_widgets.animated_icon import AnimatedIcon
from nova_widgets.icon import Icon


class _TestApp(App[None]):
    def __init__(self, widget: AnimatedIcon) -> None:
        super().__init__()
        self._widget = widget

    def compose(self) -> ComposeResult:
        yield self._widget


@pytest.mark.asyncio
async def test_animated_icon_renders_static_glyph() -> None:
    icon = AnimatedIcon(Icon("A"))
    app = _TestApp(icon)
    async with app.run_test() as pilot:
        await pilot.pause()
        assert icon.renderable == str(Icon("A"))


@pytest.mark.asyncio
async def test_animated_icon_set_glyph_updates_display() -> None:
    icon = AnimatedIcon(Icon("A"))
    app = _TestApp(icon)
    async with app.run_test() as pilot:
        await pilot.pause()
        icon.set_glyph(Icon("B"))
        await pilot.pause()
        assert icon.renderable == str(Icon("B"))


@pytest.mark.asyncio
async def test_animated_icon_stop_animation_restores_static_glyph() -> None:
    icon = AnimatedIcon(Icon("A"))
    app = _TestApp(icon)
    async with app.run_test() as pilot:
        await pilot.pause()
        icon.set_animation([Icon("X"), Icon("Y")], interval=0.05)
        await pilot.pause(delay=0.15)
        icon.stop_animation()
        await pilot.pause()
        assert icon.renderable == str(Icon("A"))


@pytest.mark.asyncio
async def test_animated_icon_click_calls_action() -> None:
    triggered: list[str] = []

    class _ActionApp(App[None]):
        def __init__(self, widget: AnimatedIcon) -> None:
            super().__init__()
            self._widget = widget

        def compose(self) -> ComposeResult:
            yield self._widget

        def action_test_action(self) -> None:
            triggered.append("test_action")

    icon = AnimatedIcon(Icon("A"), action="test_action")
    app = _ActionApp(icon)
    async with app.run_test() as pilot:
        await pilot.pause()
        await pilot.hover(icon)
        await pilot.click(icon)
        await pilot.pause(delay=0.1)
        assert triggered == ["test_action"]


@pytest.mark.asyncio
async def test_animated_icon_no_action_click_is_noop() -> None:
    icon = AnimatedIcon(Icon("A"), action=None)
    app = _TestApp(icon)
    async with app.run_test() as pilot:
        await pilot.pause()
        # Should not raise
        await pilot.click(icon)
        await pilot.pause()
```

- [ ] **Step 2: Run tests to verify they fail**

```
uv run pytest tests/nova_widgets/test_animated_icon.py -v
```

Expected: `ERROR` / `ImportError` — module does not exist yet.

- [ ] **Step 3: Implement `AnimatedIcon`**

Create `src/nova_widgets/animated_icon.py`:

```python
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

    def set_glyph(self, glyph: Icon) -> None:
        """Display *glyph* statically; stop any running animation."""
        self._stop_timer()
        self._static_glyph = glyph
        self.update(str(glyph))

    def set_animation(self, frames: list[Icon], interval: float) -> None:
        """Cycle through *frames* at *interval* seconds per frame."""
        self._stop_timer()
        self._frames = frames
        self._frame_index = 0
        if frames:
            self.update(str(frames[0]))
        self._timer = self.set_interval(interval, self._advance_frame)

    def stop_animation(self) -> None:
        """Stop animation and restore the static glyph."""
        self._stop_timer()
        self.update(str(self._static_glyph))

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
            self.app.call_action(self._action)
```

- [ ] **Step 4: Run tests to verify they pass**

```
uv run pytest tests/nova_widgets/test_animated_icon.py -v
```

Expected: all 5 tests PASS.

- [ ] **Step 5: Coding-guideline follow-up checklist**

- [ ] `docs/coding_conventions.md` read
- [ ] All functions/methods have full type annotations
- [ ] `snake_case` / `UpperCamelCase` naming correct
- [ ] `_` prefix on all private attributes
- [ ] `uv run ruff check src/nova_widgets/animated_icon.py` — zero errors
- [ ] `uv run ty check .` — zero new errors

---

## Task 2: Export `AnimatedIcon` from `nova_widgets`

**Files:**
- Modify: `src/nova_widgets/__init__.py`

- [ ] **Step 1: Add export**

Current content of `src/nova_widgets/__init__.py`:
```python
from .icon import Icon
from .menu import Action, Menu, MenuBar

__all__ = ["Action", "Icon", "Menu", "MenuBar"]
```

Replace with:
```python
from .animated_icon import AnimatedIcon
from .icon import Icon
from .menu import Action, Menu, MenuBar

__all__ = ["Action", "AnimatedIcon", "Icon", "Menu", "MenuBar"]
```

- [ ] **Step 2: Verify import works**

```
uv run python -c "from nova_widgets import AnimatedIcon; print('ok')"
```

Expected: `ok`

- [ ] **Step 3: Coding-guideline follow-up checklist**

- [ ] `docs/coding_conventions.md` read
- [ ] `uv run ruff check src/nova_widgets/__init__.py` — zero errors
- [ ] `uv run ty check .` — zero new errors

---

## Task 3: `MenuBar.add_right_widget` + layout tweak

**Files:**
- Modify: `src/nova_widgets/menu/_menu_bar.py`
- Test: `tests/nova_widgets/test_menu_bar.py`

- [ ] **Step 1: Write failing test**

Append to `tests/nova_widgets/test_menu_bar.py`:

```python
@pytest.mark.asyncio
async def test_menu_bar_add_right_widget_mounts_widget() -> None:
    from textual.widgets import Static

    bar = MenuBar()
    bar.add_menu("File")
    right = Static("R", id="right-marker")
    bar.add_right_widget(right)

    app = MenuBarTestApp(bar)
    async with app.run_test() as pilot:
        await pilot.pause()
        found = app.query_one("#right-marker", Static)
        assert found is right


@pytest.mark.asyncio
async def test_menu_bar_right_widget_is_to_the_right_of_menu_items() -> None:
    from textual.widgets import Static

    bar = MenuBar()
    bar.add_menu("File")
    right = Static("R", id="right-marker")
    bar.add_right_widget(right)

    app = MenuBarTestApp(bar)
    async with app.run_test() as pilot:
        await pilot.pause()
        items = list(app.query(MenuBarItem))
        right_widget = app.query_one("#right-marker", Static)
        # Right widget's x offset must be >= rightmost menu item's x offset
        assert right_widget.region.x >= items[-1].region.x
```

- [ ] **Step 2: Run tests to verify they fail**

```
uv run pytest tests/nova_widgets/test_menu_bar.py::test_menu_bar_add_right_widget_mounts_widget tests/nova_widgets/test_menu_bar.py::test_menu_bar_right_widget_is_to_the_right_of_menu_items -v
```

Expected: FAIL — `add_right_widget` does not exist yet.

- [ ] **Step 3: Implement the change**

In `src/nova_widgets/menu/_menu_bar.py`, add `_right_widgets` to the class and update `__init__`, `add_right_widget`, and `compose`:

```python
# At top, add this import (Horizontal is already imported from textual.containers)
# No new imports needed — Widget is already imported from textual.widget

class MenuBar(Widget, ActionCollection):
    DEFAULT_CSS = """
    MenuBar {
        dock: top;
        width: 100%;
        background: $panel;
        color: $foreground;
        height: 1;
    }
    """

    DEFAULT_CLASSES = ""

    _menus: list[Menu]
    _items: list[MenuBarItem]
    _menu_opened: Menu | None
    _right_widgets: list[Widget]

    def __init__(
        self,
    ) -> None:
        Widget.__init__(self)
        ActionCollection.__init__(self)
        self._menus = []
        self._menu_opened = None
        self._right_widgets = []

    def add_menu(self, title: str, *items: Action, name: str | None = None) -> Menu:
        menu = Menu(title, *items, name=name)
        self._menus.append(menu)
        self._add_action(menu)
        return menu

    def add_right_widget(self, widget: Widget) -> None:
        """Append *widget* to the right side of the menu bar.

        Must be called before the widget is mounted (before compose runs).
        """
        self._right_widgets.append(widget)

    def compose(self) -> ComposeResult:
        self._items = [MenuBarItem(self, menu) for menu in self._menus]
        left = Horizontal(*self._items)
        left.styles.width = "1fr"
        right = Horizontal(*self._right_widgets)
        right.styles.width = "auto"
        outer = Horizontal(left, right)
        outer.styles.width = "100%"
        yield outer
```

- [ ] **Step 4: Run tests to verify they pass**

```
uv run pytest tests/nova_widgets/test_menu_bar.py -v
```

Expected: all tests PASS (including the two new ones and all pre-existing ones).

- [ ] **Step 5: Coding-guideline follow-up checklist**

- [ ] `docs/coding_conventions.md` read
- [ ] All new methods fully type-annotated
- [ ] `_right_widgets` has `_` prefix (private)
- [ ] `uv run ruff check src/nova_widgets/menu/_menu_bar.py` — zero errors
- [ ] `uv run ty check .` — zero new errors

---

## Task 4: `JobStatusIcon` widget

**Files:**
- Create: `src/nova_navigator/widgets/job_status_icon.py`
- Test: `tests/widgets/test_job_status_icon.py`

- [ ] **Step 1: Write failing tests**

Create `tests/widgets/test_job_status_icon.py`:

```python
from enum import auto
from unittest.mock import MagicMock

import pytest
from textual.app import App, ComposeResult

from nova_navigator.dialogs.job_registry import JobRegistry
from nova_navigator.scheduler import Job
from nova_navigator.widgets.job_status_icon import JobStatusIcon, _State


class _TestApp(App[None]):
    def __init__(self, widget: JobStatusIcon) -> None:
        super().__init__()
        self._widget = widget

    def compose(self) -> ComposeResult:
        yield self._widget


def _make_job(state: Job.State) -> Job:
    """Return a mock Job with the given state."""
    job = MagicMock(spec=Job)
    job.state = state
    return job


@pytest.mark.asyncio
async def test_job_status_icon_starts_idle() -> None:
    registry = JobRegistry()
    icon = JobStatusIcon(registry=registry, action="show_processes")
    app = _TestApp(icon)
    async with app.run_test() as pilot:
        await pilot.pause()
        assert icon._current_state == _State.IDLE


@pytest.mark.asyncio
async def test_job_status_icon_running_when_jobs_present() -> None:
    registry = JobRegistry()
    registry.add_job(_make_job(Job.State.RUNNING))
    icon = JobStatusIcon(registry=registry, action="show_processes")
    app = _TestApp(icon)
    async with app.run_test() as pilot:
        await pilot.pause()
        icon._update()
        assert icon._current_state == _State.RUNNING


@pytest.mark.asyncio
async def test_job_status_icon_failed_when_failed_job_in_finished() -> None:
    registry = JobRegistry()
    registry._finished.appendleft(_make_job(Job.State.FAILED))
    icon = JobStatusIcon(registry=registry, action="show_processes")
    app = _TestApp(icon)
    async with app.run_test() as pilot:
        await pilot.pause()
        icon._update()
        assert icon._current_state == _State.FAILED


@pytest.mark.asyncio
async def test_job_status_icon_failed_takes_priority_over_running() -> None:
    registry = JobRegistry()
    registry.add_job(_make_job(Job.State.RUNNING))
    registry._finished.appendleft(_make_job(Job.State.FAILED))
    icon = JobStatusIcon(registry=registry, action="show_processes")
    app = _TestApp(icon)
    async with app.run_test() as pilot:
        await pilot.pause()
        icon._update()
        assert icon._current_state == _State.FAILED


@pytest.mark.asyncio
async def test_job_status_icon_returns_to_idle_after_failed_cleared() -> None:
    registry = JobRegistry()
    failed_job = _make_job(Job.State.FAILED)
    registry._finished.appendleft(failed_job)
    icon = JobStatusIcon(registry=registry, action="show_processes")
    app = _TestApp(icon)
    async with app.run_test() as pilot:
        await pilot.pause()
        icon._update()
        assert icon._current_state == _State.FAILED

        registry.remove_job(failed_job)
        icon._update()
        assert icon._current_state == _State.IDLE
```

- [ ] **Step 2: Run tests to verify they fail**

```
uv run pytest tests/widgets/test_job_status_icon.py -v
```

Expected: `ERROR` / `ImportError` — module does not exist yet.

- [ ] **Step 3: Implement `JobStatusIcon`**

Create `src/nova_navigator/widgets/job_status_icon.py`:

```python
from enum import Enum, auto

from textual.app import ComposeResult
from textual.widget import Widget

from nova_navigator.dialogs.job_registry import JobRegistry
from nova_navigator.scheduler import Job
from nova_widgets.animated_icon import AnimatedIcon
from nova_widgets.icon import Icon

_DEFAULT_RUNNING_FRAMES: list[Icon] = [
    Icon("󰪞"),
    Icon("󰪟"),
    Icon("󰪠"),
    Icon("󰪡"),
    Icon("󰪢"),
    Icon("󰪣"),
    Icon("󰪤"),
    Icon("󰪥"),
]
_DEFAULT_RUNNING_INTERVAL: float = 0.15
_DEFAULT_IDLE_ICON: Icon = Icon("󰄳")
_DEFAULT_FAILED_ICON: Icon = Icon("󰅚")


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
    _idle_icon: Icon
    _running_frames: list[Icon]
    _running_interval: float
    _failed_icon: Icon
    _current_state: _State
    _animated_icon: AnimatedIcon

    def __init__(
        self,
        registry: JobRegistry,
        action: str,
        *,
        idle_icon: Icon = _DEFAULT_IDLE_ICON,
        running_frames: list[Icon] | None = None,
        running_interval: float = _DEFAULT_RUNNING_INTERVAL,
        failed_icon: Icon = _DEFAULT_FAILED_ICON,
    ) -> None:
        super().__init__()
        self._registry = registry
        self._action = action
        self._idle_icon = idle_icon
        self._running_frames = running_frames if running_frames is not None else _DEFAULT_RUNNING_FRAMES
        self._running_interval = running_interval
        self._failed_icon = failed_icon
        self._current_state = _State.IDLE

    def compose(self) -> ComposeResult:
        self._animated_icon = AnimatedIcon(
            self._idle_icon,
            action=self._action,
            tooltip="Jobs (Ctrl+K)",
        )
        yield self._animated_icon

    def on_mount(self) -> None:
        self.set_interval(0.5, self._update)

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
                self._animated_icon.stop_animation()
            case _State.RUNNING:
                self._animated_icon.set_animation(self._running_frames, self._running_interval)
            case _State.FAILED:
                self._animated_icon.set_glyph(self._failed_icon)
```

- [ ] **Step 4: Run tests to verify they pass**

```
uv run pytest tests/widgets/test_job_status_icon.py -v
```

Expected: all 5 tests PASS.

- [ ] **Step 5: Coding-guideline follow-up checklist**

- [ ] `docs/coding_conventions.md` read
- [ ] All methods fully type-annotated
- [ ] `_` prefix on all private attributes
- [ ] `_State` enum uses `auto()`
- [ ] `uv run ruff check src/nova_navigator/widgets/job_status_icon.py` — zero errors
- [ ] `uv run ty check .` — zero new errors

---

## Task 5: Export `JobStatusIcon` from `nova_navigator/widgets`

**Files:**
- Modify: `src/nova_navigator/widgets/__init__.py`

- [ ] **Step 1: Add export**

Current content of `src/nova_navigator/widgets/__init__.py`:
```python
from .directory_browser import DirectoryBrowser
from .footer import Footer
from .no_select_list_view import NoSelectListView
from .separator import Separator

__all__ = ["DirectoryBrowser", "Footer", "NoSelectListView", "Separator"]
```

Replace with:
```python
from .directory_browser import DirectoryBrowser
from .footer import Footer
from .job_status_icon import JobStatusIcon
from .no_select_list_view import NoSelectListView
from .separator import Separator

__all__ = ["DirectoryBrowser", "Footer", "JobStatusIcon", "NoSelectListView", "Separator"]
```

- [ ] **Step 2: Verify import works**

```
uv run python -c "from nova_navigator.widgets import JobStatusIcon; print('ok')"
```

Expected: `ok`

- [ ] **Step 3: Coding-guideline follow-up checklist**

- [ ] `uv run ruff check src/nova_navigator/widgets/__init__.py` — zero errors
- [ ] `uv run ty check .` — zero new errors

---

## Task 6: Wire `JobStatusIcon` into `MainScreen`

**Files:**
- Modify: `src/nova_navigator/nova_navigator.py`

- [ ] **Step 1: Add import**

In `src/nova_navigator/nova_navigator.py`, find the existing imports:

```python
from nova_navigator.widgets import DirectoryBrowser, Footer
```

Replace with:

```python
from nova_navigator.widgets import DirectoryBrowser, Footer, JobStatusIcon
```

- [ ] **Step 2: Add wiring in `compose()`**

In `MainScreen.compose()`, locate the block where `_menu_bar` is constructed and all `add_menu` calls finish, immediately before `yield self._menu_bar`. Add:

```python
        self._job_status_icon = JobStatusIcon(
            registry=self.app.job_registry,
            action="show_processes",
        )
        self._menu_bar.add_right_widget(self._job_status_icon)
```

Also add `_job_status_icon: JobStatusIcon` to the class-level attribute declarations alongside `_jobs_dialog: JobsDialog`.

- [ ] **Step 3: Run full test suite**

```
uv run pytest -v
```

Expected: all tests PASS, no regressions.

- [ ] **Step 4: Run QA**

```
uv run qa
```

Expected: zero lint, type, and test failures.

- [ ] **Step 5: Coding-guideline follow-up checklist**

- [ ] `docs/coding_conventions.md` read
- [ ] New attribute `_job_status_icon` declared with type at class level
- [ ] `uv run ruff check src/nova_navigator/nova_navigator.py` — zero errors
- [ ] `uv run ty check .` — zero new errors
