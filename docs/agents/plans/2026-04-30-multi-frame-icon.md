# Multi-frame Icon Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use skills:subagent-driven-development (recommended) or skills:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Replace `Icon(str)` with a proper frozen dataclass that holds one or more display frames, enabling animated spinners to be defined in `icons.csv` and retrieved with a single `ico_()` call.

**Architecture:** `Icon` becomes a `@dataclass(frozen=True)` with a `_frames: tuple[str, ...]` field; the CSV parser splits nerdfont columns on repeated `U+XXXX` tokens and unicode columns on grapheme clusters (stdlib `re`); `AnimatedIcon` methods accept `Icon` directly and use `icon.frames` internally.

**Tech Stack:** Python 3.12, pytest

**Coding Conventions:** `docs/coding_conventions.md` — read before implementing

**Spec:** `docs/agents/specs/2026-04-30-multi-frame-icon-design.md`

---

## File Map

| Action | File | What changes |
|--------|------|--------------|
| Modify | `src/nova_widgets/icon.py` | Rewrite as frozen dataclass |
| Modify | `src/nova_widgets/animated_icon.py` | Method signatures: `str` → `Icon` |
| Modify | `src/nova_navigator/icons.py` | Storage → `list[str]` per variant; grapheme-split parser |
| Modify | `src/nova_widgets/menu/_action.py` | `IconProvider` / `_icon` type: `str` → `Icon` |
| Modify | `src/nova_widgets/menu/_menu.py` | Render: `.glyph`/`.markup` on icon values |
| Modify | `src/nova_widgets/menu/_symbol_table.py` | Values: `str` → `Icon` |
| Modify | `src/nova_navigator/nova_navigator_core.py` | SYMBOL_TABLE uses `Icon` values |
| Modify | `src/nova_navigator/widgets/job_status_icon.py` | Collapse spinner to `ico_("spinner")` |
| Modify | `src/nova_navigator/dialogs/bookmarks_dialog.py` | `.glyph` on result of `get_icon` |
| Modify | `src/nova_navigator/dialogs/edit_bookmarks_dialog.py` | `.glyph` on result of `get_icon` |
| Modify | `src/nova_navigator/widgets/directory_browser.py` | `.glyph` on result of `ico_` |
| Modify | `config/default/icons.csv` | Replace `circle_*` with `spinner` |
| Modify | `tests/nova_widgets/test_icon.py` | Rewrite for new API |

---

## Task 1: Rewrite `Icon` as a frozen dataclass

**Files:**
- Modify: `src/nova_widgets/icon.py`
- Modify: `tests/nova_widgets/test_icon.py`

- [ ] **Step 1: Write failing tests**

Replace the full content of `tests/nova_widgets/test_icon.py`:

```python
import pytest
from nova_widgets.icon import Icon


# --- Icon.of ---

def test_of_none_produces_blank_glyph() -> None:
    icon = Icon.of(None)
    assert icon.glyph == "  "


def test_of_none_is_not_animated() -> None:
    assert not Icon.of(None).is_animated


def test_of_ascii_glyph_padded_to_width_2() -> None:
    icon = Icon.of("x")
    assert icon.glyph == "x "


def test_of_wide_glyph_no_extra_padding() -> None:
    icon = Icon.of("⭐")
    assert icon.glyph == "⭐"


def test_of_single_frame_is_not_animated() -> None:
    assert not Icon.of("●").is_animated


def test_of_color_stored() -> None:
    icon = Icon.of("●", color=(255, 0, 0))
    assert icon.color == (255, 0, 0)


def test_of_markup_plain_when_no_color() -> None:
    icon = Icon.of("●")
    assert icon.markup == "● "


def test_of_markup_with_color() -> None:
    icon = Icon.of("●", color=(255, 0, 0))
    assert icon.markup == "[rgb(255,0,0)]● [/]"


# --- Icon.from_glyphs ---

def test_from_glyphs_multi_frame_is_animated() -> None:
    icon = Icon.from_glyphs(["○", "◔", "◑", "◕", "●"])
    assert icon.is_animated


def test_from_glyphs_single_frame_is_not_animated() -> None:
    icon = Icon.from_glyphs(["●"])
    assert not icon.is_animated


def test_from_glyphs_empty_produces_blank() -> None:
    icon = Icon.from_glyphs([])
    assert icon.glyph == "  "


def test_from_glyphs_frames_count() -> None:
    icon = Icon.from_glyphs(["○", "◔", "◑", "◕", "●"])
    assert len(icon.frames) == 5


def test_from_glyphs_frames_are_single_frame_icons() -> None:
    icon = Icon.from_glyphs(["○", "●"])
    for frame in icon.frames:
        assert not frame.is_animated


def test_from_glyphs_frames_carry_color() -> None:
    icon = Icon.from_glyphs(["○", "●"], color=(1, 2, 3))
    for frame in icon.frames:
        assert frame.color == (1, 2, 3)


# --- Icon() blank constructor ---

def test_blank_constructor_produces_blank() -> None:
    icon = Icon()
    assert icon.glyph == "  "


def test_blank_constructor_not_animated() -> None:
    assert not Icon().is_animated


# --- frames property on single-frame icon ---

def test_single_frame_icon_frames_returns_self_list() -> None:
    icon = Icon.of("●")
    assert icon.frames == [icon]


# --- not a str subclass ---

def test_icon_is_not_str() -> None:
    assert not isinstance(Icon.of("x"), str)
```

- [ ] **Step 2: Run tests to confirm they fail**

```
uv run pytest tests/nova_widgets/test_icon.py -v
```
Expected: multiple FAILs / import errors.

- [ ] **Step 3: Rewrite `src/nova_widgets/icon.py`**

```python
from __future__ import annotations

from dataclasses import dataclass, field
from typing import ClassVar

from .unicode import ljust


@dataclass(frozen=True)
class Icon:
    """A fixed-width terminal icon, holding one or more display frames.

    Use :meth:`Icon.of` for a single-frame icon and :meth:`Icon.from_glyphs`
    for multi-frame (animated) icons.
    """

    ICON_WIDTH: ClassVar[int] = 2

    _frames: tuple[str, ...] = field(default_factory=tuple)
    color: tuple[int, int, int] | None = None

    # ------------------------------------------------------------------
    # Constructors
    # ------------------------------------------------------------------

    @classmethod
    def of(cls, glyph: str | None = None, *, color: tuple[int, int, int] | None = None) -> "Icon":
        """Single-frame constructor. ``None`` or omitted → blank placeholder."""
        if glyph is None or glyph == "":
            return cls(_frames=(), color=color)
        return cls(_frames=(ljust(glyph, cls.ICON_WIDTH),), color=color)

    @classmethod
    def from_glyphs(cls, glyphs: list[str], *, color: tuple[int, int, int] | None = None) -> "Icon":
        """Multi-frame constructor. Empty list → blank placeholder."""
        return cls(_frames=tuple(ljust(g, cls.ICON_WIDTH) for g in glyphs), color=color)

    # ------------------------------------------------------------------
    # Properties
    # ------------------------------------------------------------------

    @property
    def glyph(self) -> str:
        """First frame string, padded to ICON_WIDTH. Two spaces when empty."""
        return self._frames[0] if self._frames else " " * self.ICON_WIDTH

    @property
    def markup(self) -> str:
        """Glyph wrapped in Rich ``[rgb(r,g,b)]...[/]`` markup when color is set."""
        if self.color is not None:
            r, g, b = self.color
            return f"[rgb({r},{g},{b})]{self.glyph}[/]"
        return self.glyph

    @property
    def frames(self) -> list["Icon"]:
        """Each animation frame as a single-frame Icon carrying the same color."""
        if not self._frames:
            return [self]
        if len(self._frames) == 1:
            return [self]
        return [Icon(_frames=(f,), color=self.color) for f in self._frames]

    @property
    def is_animated(self) -> bool:
        """True when there are two or more frames."""
        return len(self._frames) > 1
```

Note: `Icon()` with no arguments produces a blank icon because `_frames` defaults to `()`.

- [ ] **Step 4: Run tests to confirm they pass**

```
uv run pytest tests/nova_widgets/test_icon.py -v
```
Expected: all PASS.

- [ ] **Step 5: Coding-guideline follow-up checklist**

- [ ] `docs/coding_conventions.md` read
- [ ] All new symbols use `snake_case` / `UpperCamelCase` per conventions
- [ ] Full type annotations on every method
- [ ] `uv run pytest tests/nova_widgets/test_icon.py` passes

---

## Task 2: Update `AnimatedIcon` to accept `Icon`

**Files:**
- Modify: `src/nova_widgets/animated_icon.py`

- [ ] **Step 1: Read the current file**

Read `src/nova_widgets/animated_icon.py` lines 1–130 fully before editing.

- [ ] **Step 2: Rewrite `src/nova_widgets/animated_icon.py`**

```python
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

    _static_icon: Icon
    _action: str | None
    _timer: Timer | None
    _frame_index: int
    _frames: list[Icon]

    def __init__(
        self,
        icon: Icon,
        *,
        action: str | None = None,
        tooltip: str | None = None,
    ) -> None:
        super().__init__(icon.markup)
        self._static_icon = icon
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

    def icon_static(self, icon: Icon) -> None:
        """Display *icon* statically; stop any running animation."""
        self._stop_timer()
        self._static_icon = icon
        self.update(icon.markup)
        self._frames = []

    def icon_animate(self, icon: Icon, interval: float) -> None:
        """Cycle through *icon*'s frames at *interval* seconds per frame."""
        self._stop_timer()
        self._frames = icon.frames
        self._frame_index = 0
        if self._frames:
            self.update(self._frames[0].markup)
        self._timer = self.set_interval(interval, self._advance_frame)

    def icon_pulse(
        self,
        icon: Icon,
        *,
        bright: tuple[int, int, int],
        dim: tuple[int, int, int],
        n: int = 12,
        interval: float = 0.1,
    ) -> None:
        """Animate *icon*'s glyph pulsing between *bright* and *dim* colours via sin()."""
        glyph = icon.glyph
        frames: list[Icon] = []
        for i in range(n):
            t = i / n * 2 * math.pi
            blend = (math.sin(t) + 1) / 2
            r = round(dim[0] + (bright[0] - dim[0]) * blend)
            g = round(dim[1] + (bright[1] - dim[1]) * blend)
            b = round(dim[2] + (bright[2] - dim[2]) * blend)
            frames.append(Icon.of(glyph, color=(r, g, b)))
        self._stop_timer()
        self._frames = frames
        self._frame_index = 0
        if frames:
            self.update(frames[0].markup)
        self._timer = self.set_interval(interval, self._advance_frame)

    def stop_icon_animation(self) -> None:
        """Stop animation and restore the static icon."""
        self._stop_timer()
        self.update(self._static_icon.markup)
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
```

- [ ] **Step 3: Run QA**

```
uv run qa
```
Expected: errors only in files not yet updated (e.g. `job_status_icon.py`). No errors in `animated_icon.py` or `icon.py`.

- [ ] **Step 4: Coding-guideline follow-up checklist**

- [ ] Full type annotations present
- [ ] `_static_glyph` rename to `_static_icon` consistent throughout the file
- [ ] No `str` used where `Icon` is now expected

---

## Task 3: Update `IconSet` / `icons.py` with multi-frame parsing

**Files:**
- Modify: `src/nova_navigator/icons.py`

- [ ] **Step 1: Write failing test**

Add to `tests/nova_widgets/test_icon.py` (append after existing tests):

```python
# --- CSV multi-frame loading ---

import io
from nova_navigator.icons import IconSet


def test_spinner_from_csv_is_animated() -> None:
    csv = "spinner,U+ee06U+ee07U+ee08U+ee09U+ee0a,○◔◑◕●\n"
    iconset = IconSet()
    iconset.load_icons(io.StringIO(csv))
    iconset.set_variant(IconSet.Variants.UNICODE)
    icon = iconset.get_icon("spinner")
    assert icon.is_animated
    assert len(icon.frames) == 5


def test_spinner_nerdfont_frames_from_csv() -> None:
    csv = "spinner,U+ee06U+ee07U+ee08U+ee09U+ee0a,○◔◑◕●\n"
    iconset = IconSet()
    iconset.load_icons(io.StringIO(csv))
    iconset.set_variant(IconSet.Variants.NERDFONT)
    icon = iconset.get_icon("spinner")
    assert icon.is_animated
    assert len(icon.frames) == 5


def test_single_glyph_csv_row_is_not_animated() -> None:
    csv = "file,U+f15b,📄\n"
    iconset = IconSet()
    iconset.load_icons(io.StringIO(csv))
    iconset.set_variant(IconSet.Variants.UNICODE)
    icon = iconset.get_icon("file")
    assert not icon.is_animated


def test_variation_selector_glyph_is_one_frame() -> None:
    # ✏️ is U+270F + U+FE0F — should be treated as a single grapheme cluster
    csv = "edit,U+f044,✏️\n"
    iconset = IconSet()
    iconset.load_icons(io.StringIO(csv))
    iconset.set_variant(IconSet.Variants.UNICODE)
    icon = iconset.get_icon("edit")
    assert not icon.is_animated
```

- [ ] **Step 2: Run tests to confirm they fail**

```
uv run pytest tests/nova_widgets/test_icon.py -k "csv" -v
```
Expected: FAIL.

- [ ] **Step 3: Rewrite `src/nova_navigator/icons.py`**

```python
import csv
import re
from collections.abc import Iterator
from enum import Enum
from pathlib import Path
from typing import ClassVar, TextIO

from nova_widgets import Icon

# Matches one grapheme cluster: base codepoint + optional variation selector / combining marks.
# Covers all glyphs in icons.csv. ZWJ sequences are not supported.
_GRAPHEME_RE = re.compile(r".\ufe0f?[\u0300-\u036f\ufe00-\ufe0f]*", re.DOTALL)

# Matches a single U+XXXX or \uXXXX codepoint token.
_CODEPOINT_RE = re.compile(r"(?:U\+|\\u)([0-9A-Fa-f]{4,6})")


def _parse_nerdfont_frames(cell: str) -> list[str]:
    """Return list of nerdfont glyph strings from a cell like ``U+ee06U+ee07``."""
    matches = _CODEPOINT_RE.findall(cell)
    # nerdfont glyphs take 2 columns but are 1 codepoint; pad with a trailing space
    return [chr(int(cp, 16)) + " " for cp in matches]


def _parse_unicode_frames(cell: str) -> list[str]:
    """Return list of grapheme-cluster strings from a cell like ``○◔◑◕●``."""
    # First expand any U+XXXX or \uXXXX escape sequences that may remain
    def _expand(m: re.Match[str]) -> str:
        return chr(int(m.group(1), 16))

    expanded = _CODEPOINT_RE.sub(_expand, cell)
    return _GRAPHEME_RE.findall(expanded)


class IconSet:
    class Variants(Enum):
        NERDFONT = 0
        UNICODE = 1

    _glyph_variant: ClassVar[Variants] = Variants.NERDFONT

    # Each entry stores (nerdfont_frames, unicode_frames)
    Glyphs = tuple[list[str], list[str]]

    _icons: dict[str, Glyphs]

    def load_icons(self, f: TextIO | Path) -> None:
        if isinstance(f, Path):
            with f.open(encoding="utf-8") as file:
                self._load_icons(file)
        else:
            self._load_icons(f)

    def _load_icons(self, f: TextIO) -> None:
        reader = csv.reader(
            filter(lambda row: len(row.strip()) > 0 and row[0] != "#", f),
            delimiter=",",
            quotechar='"',
        )
        icons: dict[str, IconSet.Glyphs] = {}
        for row in reader:
            name = row[0]
            nf_frames = _parse_nerdfont_frames(row[1])
            uni_frames = _parse_unicode_frames(row[2])
            icons[name] = (nf_frames, uni_frames)
        self._icons = icons

    @classmethod
    def set_variant(cls, variant: Variants) -> None:
        cls._glyph_variant = variant

    @classmethod
    def get_variant(cls) -> Variants:
        return cls._glyph_variant

    def get_icon(self, name: str | None, default: Icon | None = None, variant: Variants | None = None) -> Icon:
        if default is None:
            default = Icon()
        if name is None:
            return default
        if variant is None:
            variant = IconSet._glyph_variant
        glyphs = self._icons.get(name)
        if glyphs is None:
            return default
        frames = glyphs[variant.value]
        if not frames:
            return default
        return Icon.from_glyphs(frames)

    def __iter__(self) -> Iterator[tuple[str, Glyphs]]:
        return iter(self._icons.items())


ICONS = IconSet()


def ico_(name: str | None, default: Icon | None = None) -> Icon:
    return ICONS.get_icon(name, default)
```

- [ ] **Step 4: Run icon tests**

```
uv run pytest tests/nova_widgets/test_icon.py -v
```
Expected: all PASS.

- [ ] **Step 5: Coding-guideline follow-up checklist**

- [ ] Full type annotations on all functions
- [ ] `_GRAPHEME_RE` and `_CODEPOINT_RE` are module-level constants (`UPPER_CASE`)
- [ ] `uv run pytest tests/nova_widgets/test_icon.py` passes

---

## Task 4: Update `icons.csv`

**Files:**
- Modify: `config/default/icons.csv`

- [ ] **Step 1: Replace the `# job status` section**

In `config/default/icons.csv`, replace the entire `# job status` block:

```csv
# job status
circle_empty,U+ee06,○
circle_quarter,U+ee07,◔
circle_half,U+ee08,◑
circle_three_quarter,U+ee09,◕
circle_full,U+ee0a,●
```

with:

```csv
# job status
spinner,U+ee06U+ee07U+ee08U+ee09U+ee0a,○◔◑◕●
spinner_full,U+ee0a,●
```

`spinner_full` is a single-frame alias for the "all filled" state used in the idle and failed cases of `job_status_icon.py`.

- [ ] **Step 2: Update the format comment at the top of icons.csv**

Change the first line from:

```
# format: name,nerdfont,unicode
```

to:

```
# format: name,nerdfont,unicode
# nerdfont: one or more U+XXXX codepoints concatenated (e.g. U+ee06U+ee07)
# unicode:  one or more grapheme clusters concatenated (e.g. ○◔◑◕●)
```

- [ ] **Step 3: Run icon loading test**

```
uv run pytest tests/nova_widgets/test_icon.py -k "csv" -v
```
Expected: all PASS.

---

## Task 5: Update the menu system (`_action.py`, `_symbol_table.py`, `_menu.py`)

**Files:**
- Modify: `src/nova_widgets/menu/_action.py`
- Modify: `src/nova_widgets/menu/_symbol_table.py`
- Modify: `src/nova_widgets/menu/_menu.py`

These three files form one logical unit — the menu rendering pipeline — and must be updated together.

- [ ] **Step 1: Update `src/nova_widgets/menu/_symbol_table.py`**

Change the values from plain strings to `Icon.of(...)`.
Read the file first, then replace its full content with:

```python
from nova_widgets.icon import Icon

SYMBOL_TABLE: dict[str, tuple[Icon, Icon]] = {
    "checkbox": (Icon.of("🞎 "), Icon.of("⛝ ")),
    "radio": (Icon.of("🞅 "), Icon.of("⦿ ")),
}
```

Note: these glyphs already include a trailing space in the original CSV; preserve that here.

- [ ] **Step 2: Update `src/nova_widgets/menu/_action.py`**

Change `IconProvider` type alias, `_icon` field, and `icon` property from `str` to `Icon`.
Read the full file first, then apply these targeted changes:

```python
# Change the type alias (line ~5)
# Before:
IconProvider = Callable[[str], str]
# After:
IconProvider = Callable[[str], Icon]
```

```python
# Change the default provider (line ~9)
# Before:
ICON_PROVIDER: IconProvider = lambda s: s  # noqa: E731
# After:
ICON_PROVIDER: IconProvider = Icon.of  # noqa: E731
```

Add the import at the top of the file:

```python
from nova_widgets.icon import Icon
```

Change the class field declaration and property return type:

```python
# Before:
_icon: str | None

# After:
_icon: Icon | None
```

```python
# Before:
def icon(self) -> str | None:
    return self._icon

# After:
def icon(self) -> Icon | None:
    return self._icon
```

- [ ] **Step 3: Update `src/nova_widgets/menu/_menu.py`** render sites

There are two sites in `render_line` that use icon/symbol values as bare strings. Read `render_line` in full first, then make these changes:

```python
# SYMBOL_TABLE render — use .glyph (it's already padded to 2 cols)
# Before:
segments.append(Segment(SYMBOL_TABLE[kind][1 if action.checked else 0], item_style))
# After:
segments.append(Segment(SYMBOL_TABLE[kind][1 if action.checked else 0].glyph, item_style))
```

```python
# Action icon render — use .glyph
# Before:
icon_text = ""
if action.icon:
    icon_text = action.icon
segments.append(Segment(icon_text.ljust(3), item_style))
# After:
icon_glyph = action.icon.glyph if action.icon else ""
segments.append(Segment(icon_glyph.ljust(3), item_style))
```

- [ ] **Step 4: Update `src/nova_navigator/nova_navigator_core.py`**

`SYMBOL_TABLE` now holds `Icon` values; the assignment lines pass `Icon` objects directly, which is already correct after Task 5 Step 1. Verify the lines read:

```python
SYMBOL_TABLE["checkbox"] = (ICONS.get_icon("checkbox"), ICONS.get_icon("checkbox_checked"))
SYMBOL_TABLE["radio"] = (ICONS.get_icon("radio"), ICONS.get_icon("radio_checked"))
```

These now assign `Icon` objects into a `dict[str, tuple[Icon, Icon]]` — no code change needed, but confirm the types align after running `ty check`.

- [ ] **Step 5: Run QA**

```
uv run qa
```
Expected: errors only in files not yet updated (`job_status_icon.py`, dialogs, directory browser). No errors in menu files.

- [ ] **Step 6: Coding-guideline follow-up checklist**

- [ ] `IconProvider = Callable[[str], Icon]` — type alias updated consistently
- [ ] `Icon.of` used as default provider (not a lambda returning `str`)
- [ ] `uv run qa` has no errors in `src/nova_widgets/menu/`

---

## Task 6: Update `job_status_icon.py`

**Files:**
- Modify: `src/nova_navigator/widgets/job_status_icon.py`

- [ ] **Step 1: Read the file**

Read `src/nova_navigator/widgets/job_status_icon.py` in full.

- [ ] **Step 2: Remove the `circle_*` fallback imports and simplify**

Replace the full content of `src/nova_navigator/widgets/job_status_icon.py`:

```python
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

_IDLE_ICON: Icon = ico_("spinner_full", default=Icon.of("●"))
_SPINNER_ICON: Icon = ico_("spinner", default=Icon.from_glyphs(["○", "◔", "◑", "◕", "●"]))


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
            _IDLE_ICON,
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
                self._animated_icon.icon_static(_IDLE_ICON)
            case _State.RUNNING:
                self._animated_icon.icon_animate(_SPINNER_ICON, _RUNNING_INTERVAL)
            case _State.FAILED:
                self._animated_icon.icon_pulse(
                    _IDLE_ICON,
                    bright=_FAILED_BRIGHT,
                    dim=_FAILED_DIM,
                    n=_FAILED_N,
                    interval=_FAILED_INTERVAL,
                )
```

- [ ] **Step 3: Run QA**

```
uv run qa
```
Expected: errors only in files not yet updated (dialogs, directory browser).

- [ ] **Step 4: Coding-guideline follow-up checklist**

- [ ] Module-level constants `_IDLE_ICON` and `_SPINNER_ICON` — `UPPER_CASE` for constants; since they're private use the `_` prefix convention: name them `_IDLE_ICON` / `_SPINNER_ICON` ✓
- [ ] No bare `Icon("●")` calls remain — all use `Icon.of()`

---

## Task 7: Update dialog and browser call sites

**Files:**
- Modify: `src/nova_navigator/dialogs/bookmarks_dialog.py`
- Modify: `src/nova_navigator/dialogs/edit_bookmarks_dialog.py`
- Modify: `src/nova_navigator/widgets/directory_browser.py`

- [ ] **Step 1: Read all three files**

Read each file in full before editing. Identify every call site that does `get_icon(...) + ...` or `ico_(...) + ...`.

- [ ] **Step 2: Update `bookmarks_dialog.py`** (2 lines)

```python
# Before:
group_node = tree.root.add(ICONS.get_icon(group.icon) + " " + group.name, expand=True)
# After:
group_node = tree.root.add(ICONS.get_icon(group.icon).glyph + " " + group.name, expand=True)
```

```python
# Before:
group_node.add_leaf(ICONS.get_icon(name=bookmark.icon) + " " + bookmark.name, bookmark.path)
# After:
group_node.add_leaf(ICONS.get_icon(name=bookmark.icon).glyph + " " + bookmark.name, bookmark.path)
```

- [ ] **Step 3: Update `edit_bookmarks_dialog.py`** (4 lines — all same pattern)

Find all occurrences of `ICONS.get_icon(...) + " "` (there are 4) and append `.glyph` before ` + " "` in each.

Lines ~254, ~261, ~437, ~443. Pattern is identical in all four:

```python
# Before:
icon = ICONS.get_icon(group.icon) + " " if group.icon else ""
# After:
icon = ICONS.get_icon(group.icon).glyph + " " if group.icon else ""
```

```python
# Before:
eicon = ICONS.get_icon(entry.icon) + " " if entry.icon else ""
# After:
eicon = ICONS.get_icon(entry.icon).glyph + " " if entry.icon else ""
```

- [ ] **Step 4: Update `directory_browser.py`** (2 lines)

```python
# Before:
icon_str = ico_("broken link") + "!"
# After:
icon_str = ico_("broken link").glyph + "!"
```

```python
# Before:
Button(ico_("xmark"), id="close-button", compact=True),
# After:
Button(ico_("xmark").glyph, id="close-button", compact=True),
```

- [ ] **Step 5: Run QA**

```
uv run qa
```
Expected: zero errors.

- [ ] **Step 6: Coding-guideline follow-up checklist**

- [ ] No `+` concatenation between `Icon` and `str` remains anywhere
- [ ] `uv run qa` is clean

---

## Task 8: Final verification

- [ ] **Step 1: Run full test suite**

```
uv run pytest -v
```
Expected: all tests pass.

- [ ] **Step 2: Run full QA**

```
uv run qa
```
Expected: zero lint, type, or test failures.

- [ ] **Step 3: Verify spinner CSV round-trip manually**

```python
# Quick sanity check — run in a Python shell via: uv run python -c "..."
import io
from nova_navigator.icons import IconSet

csv = "spinner,U+ee06U+ee07U+ee08U+ee09U+ee0a,○◔◑◕●\n"
s = IconSet()
s.load_icons(io.StringIO(csv))
s.set_variant(IconSet.Variants.UNICODE)
icon = s.get_icon("spinner")
print("animated:", icon.is_animated)
print("frames:", [f.glyph for f in icon.frames])
```

Expected output:
```
animated: True
frames: ['○ ', '◔ ', '◑ ', '◕ ', '● ']
```

- [ ] **Step 4: Verify no `Icon(str)` subclass references remain**

```
uv run grep -rn "isinstance.*Icon.*str\|Icon(str)" src/ tests/
```

Expected: no matches.
