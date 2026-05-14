# Job Status Icon in Menu Bar — Design Spec

## Overview

Add a clickable, animated icon to the right side of the menu bar that reflects the current state of the job registry.
The icon replaces the keyboard-only Ctrl+K affordance with a persistent visual indicator.
Three states are shown: idle, running (animated spinner), and failed.

## Components

### 1. `AnimatedIcon` — `src/nova_widgets/animated_icon.py`

A generic `Static` subclass that displays a single `Icon` glyph and can optionally cycle through a list of `Icon` frames to produce an animation.

**Constructor:**

```python
AnimatedIcon(
    glyph: Icon,
    *,
    action: str | None = None,
    tooltip: str | None = None,
)
```

- `glyph` — the initial (and static) icon to display.
- `action` — optional Textual action name (e.g. `"show_processes"`).
  Clicking the widget calls `self.app.call_action(action)`.
- `tooltip` — optional tooltip text shown on hover.

**Public methods:**

```python
def set_glyph(self, glyph: Icon) -> None
    """Display a single static glyph; stop any running animation."""

def set_animation(self, frames: list[Icon], interval: float) -> None
    """Start cycling through frames at the given interval (seconds per frame).
    Replaces any previously running animation."""

def stop_animation(self) -> None
    """Stop animation and return to the static glyph set at construction."""
```

**Internal behaviour:**

- The displayed content is always `str(current_icon)`.
- `set_animation` cancels the previous `set_interval` handle (if any) and starts a new one.
- `stop_animation` cancels the handle and calls `set_glyph(self._static_glyph)`.
- On `_on_unmount`, any active timer handle is cancelled.

**CSS (DEFAULT_CSS):**

```css
AnimatedIcon {
    width: auto;
    content-align: center middle;
    padding: 0 1;

    &:hover {
        background: $panel-lighten-2;
        color: $text;
    }
}
```

**Export:** added to `src/nova_widgets/__init__.py`.

---

### 2. `MenuBar.add_right_widget` — `src/nova_widgets/menu/_menu_bar.py`

`MenuBar` gains a list of right-aligned widgets and a new public method:

```python
def add_right_widget(self, widget: Widget) -> None:
    """Append widget to the right side of the menu bar.
    Must be called before the widget is mounted (i.e. before compose runs)."""
```

`compose()` is restructured from a single `Horizontal(*items)` to:

```
Horizontal(
    *self._items,                   # left side, width: auto each
    Spacer(),                       # fills remaining horizontal space
    *self._right_widgets,           # right side, width: auto each
)
```

`compose` yields a single outer `Horizontal` containing two inner containers:

- Left: a `Horizontal` with `width: 1fr` containing the `MenuBarItem` widgets — expands to fill available space.
- Right: a `Horizontal` with `width: auto` containing `_right_widgets` — shrinks to fit content, pushed to the right edge.

No existing `MenuBar` behaviour changes.

---

### 3. `JobStatusIcon` — `src/nova_navigator/widgets/job_status_icon.py`

A `Widget` subclass that owns a single `AnimatedIcon`, polls `JobRegistry`, and drives state transitions.

**Constructor:**

```python
JobStatusIcon(
    registry: JobRegistry,
    action: str,
    *,
    idle_icon: Icon = Icon("󰄳"),
    running_frames: list[Icon] = [Icon("󰪞"), Icon("󰪟"), Icon("󰪠"), Icon("󰪡"), Icon("󰪢"), Icon("󰪣"), Icon("󰪤"), Icon("󰪥")],  # NerdFont material spinner
    running_interval: float = 0.15,
    failed_icon: Icon = Icon("󰅚"),
)
```

All icon arguments have sensible defaults (NerdFont spinner for running, checkmark for idle, error circle for failed).
The `action` string is forwarded to the inner `AnimatedIcon`.

**State machine:**

```
IDLE  ←→  RUNNING  ←→  FAILED
```

Priority rule (evaluated each tick):
1. If any `finished_jobs` has `state == FAILED` → FAILED state.
2. Else if `running_jobs` is non-empty → RUNNING state.
3. Else → IDLE state.

The failed state persists until the user dismisses the failed job from `JobsDialog` (which removes it from `registry.finished_jobs`), at which point the next tick reverts to IDLE or RUNNING.

**Polling:**

A `set_interval(0.5)` timer calls `_update()` on each tick.
`_update()` computes the new state and calls the appropriate `AnimatedIcon` method **only if the state has changed** (avoids unnecessary redraws).

**`compose()`:**

```python
def compose(self) -> ComposeResult:
    self._icon = AnimatedIcon(self._idle_icon, action=self._action, tooltip="Jobs (Ctrl+K)")
    yield self._icon
```

**Export:** added to `src/nova_navigator/widgets/__init__.py`.

---

### 4. Wiring — `src/nova_navigator/nova_navigator.py`

In `MainScreen.compose()`, after `_menu_bar` is constructed and menus are added:

```python
self._job_status_icon = JobStatusIcon(
    registry=self.app.job_registry,
    action="show_processes",
)
self._menu_bar.add_right_widget(self._job_status_icon)
```

The `Ctrl+K` → `action_show_processes` binding is **kept unchanged**.
Both the icon click and the keyboard shortcut call the same action.

---

## Data Flow

```
JobRegistry.running_jobs / finished_jobs
        ↓  (polled every 0.5 s)
JobStatusIcon._update()
        ↓  (only on state change)
AnimatedIcon.set_glyph() / set_animation() / stop_animation()
        ↓
Static.update(str(icon))  →  renders in MenuBar right slot
```

---

## Error Handling

- If `JobRegistry` raises (it currently cannot — it's a plain list wrapper), the timer callback silently ignores the exception and retries next tick.
- If `action` is `None`, clicking the icon is a no-op.

---

## Testing

### `tests/nova_widgets/test_animated_icon.py` (new)

- Static glyph is rendered on mount.
- `set_animation` cycles through frames at the given interval.
- `stop_animation` returns to the original static glyph.
- Clicking the widget fires the configured action.
- No timer leak after widget is unmounted.

### `tests/nova_widgets/test_menu_bar.py` (extend)

- `add_right_widget` mounts the widget inside the menu bar.
- Right widget appears to the right of all menu items.

### `tests/widgets/test_job_status_icon.py` (new, in existing `tests/widgets/` directory)

- Starts in IDLE state.
- Transitions to RUNNING when a running job is added to the registry.
- Transitions to FAILED when a finished job has `state == FAILED`.
- Returns to IDLE once failed jobs are cleared.
- Does not call `set_animation` / `set_glyph` on ticks where state is unchanged.

---

## Files Changed

| File | Change |
|---|---|
| `src/nova_widgets/animated_icon.py` | **new** |
| `src/nova_widgets/__init__.py` | export `AnimatedIcon` |
| `src/nova_widgets/menu/_menu_bar.py` | `add_right_widget`, layout tweak |
| `src/nova_navigator/widgets/job_status_icon.py` | **new** |
| `src/nova_navigator/widgets/__init__.py` | export `JobStatusIcon` |
| `src/nova_navigator/nova_navigator.py` | wire `JobStatusIcon` into menu bar |
| `tests/nova_widgets/test_animated_icon.py` | **new** |
| `tests/nova_widgets/test_menu_bar.py` | extend |
| `tests/widgets/test_job_status_icon.py` | **new** |
