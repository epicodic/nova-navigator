# Multi-frame Icon Design

## Overview

Extend the `Icon` type from a single-glyph `str` subclass to a proper value class that holds one or more display frames.
This allows animated spinners to be expressed as a single named icon in `icons.csv` and retrieved with one `ico_()` call.

## Motivation

`job_status_icon.py` currently assembles a 5-frame spinner from five separate named icons:

```python
self._animated_icon.icon_animate([
    ico_("circle_empty",         default=Icon("○")),
    ico_("circle_quarter",       default=Icon("◔")),
    ico_("circle_half",          default=Icon("◑")),
    ico_("circle_three_quarter", default=Icon("◕")),
    ico_("circle_full",          default=Icon("●")),
], _RUNNING_INTERVAL)
```

After this change that collapses to:

```python
self._animated_icon.icon_animate(ico_("spinner"), _RUNNING_INTERVAL)
```

## 1. `Icon` class (`src/nova_widgets/icon.py`)

Drop `str` subclassing.
`Icon` becomes a frozen dataclass.

```python
@dataclass(frozen=True)
class Icon:
    ICON_WIDTH: ClassVar[int] = 2
    _frames: tuple[str, ...]           # internal; each padded to ICON_WIDTH
    color: tuple[int, int, int] | None = None

    @classmethod
    def of(cls, glyph: str | None = None, *, color: tuple[int,int,int] | None = None) -> "Icon":
        """Single-frame constructor. None or empty string produces a blank icon."""

    @classmethod
    def from_glyphs(cls, glyphs: list[str], *, color: tuple[int,int,int] | None = None) -> "Icon":
        """Multi-frame constructor."""

    @property
    def glyph(self) -> str:
        """First frame string, padded to ICON_WIDTH. Blank if no frames."""

    @property
    def markup(self) -> str:
        """glyph wrapped in Rich [rgb(r,g,b)]...[/] markup when color is set."""

    @property
    def frames(self) -> list["Icon"]:
        """Each frame as a single-frame Icon, carrying the same color."""

    @property
    def is_animated(self) -> bool:
        """True when there are two or more frames."""
```

`Icon()` with no arguments produces a blank two-space icon (preserves current `get_icon` default behaviour).

The `ljust` helper from `unicode.py` is still used to pad each frame to `ICON_WIDTH`.

## 2. CSV format (`config/default/icons.csv`)

### Header comment update

```
# format: name,nerdfont,unicode
# nerdfont: one or more U+XXXX codepoints concatenated (e.g. U+ee06U+ee07)
# unicode:  one or more grapheme clusters concatenated (e.g. ○◔◑◕●)
```

### Frame parsing

**Nerdfont column** — extract all `U+XXXX` or `\uXXXX` tokens (existing regex), collect as list instead of joining into one string.

**Unicode column** — split into grapheme clusters with:

```python
_GRAPHEME_RE = re.compile(r'.\ufe0f?[\u0300-\u036f\ufe00-\ufe0f]*', re.DOTALL)
frames = _GRAPHEME_RE.findall(cell)
```

This correctly handles variation selectors (e.g. `✏️` = U+270F + U+FE0F as one cluster) while splitting bare characters (e.g. `○◔◑◕●` into five frames).
ZWJ sequences are not supported; none exist in the current CSV.

### Change to `circle_*` entries

Remove the five individual `circle_empty/quarter/half/three_quarter/full` entries.
Replace with a single `spinner` entry:

```csv
spinner,U+ee06U+ee07U+ee08U+ee09U+ee0a,○◔◑◕●
```

All existing single-glyph rows are syntactically unchanged and parse to single-frame `Icon` objects.

## 3. `IconSet` (`src/nova_navigator/icons.py`)

Internal storage changes from `tuple[str, str]` to `tuple[list[str], list[str]]` (per-variant frame lists).

`get_icon()` return type stays `Icon`; now potentially multi-frame:

```python
def get_icon(self, name: str | None, default: Icon | None = None,
             variant: Variants | None = None) -> Icon:
    ...
    return Icon.from_glyphs(frames_for_variant)
```

The convenience function `ico_()` is unchanged.

## 4. `AnimatedIcon` (`src/nova_widgets/animated_icon.py`)

Method signatures change to accept `Icon` instead of mixing `Icon` and `str`:

```python
def icon_static(self, icon: Icon) -> None:
    # uses icon.markup

def icon_animate(self, icon: Icon, interval: float) -> None:
    # uses icon.frames; each frame is a single-frame Icon with .markup

def icon_pulse(self, icon: Icon, *, bright: tuple[int,int,int],
               dim: tuple[int,int,int], n: int = 12,
               interval: float = 0.1) -> None:
    # uses icon.glyph as the character; builds color-interpolated Icon.of() frames
```

For a single-frame icon, `icon.frames` is `[icon]`, so `icon_animate` degenerates to a static display with no special case.

Internal `_frames: list[Icon]` field is unchanged in shape.
`_static_glyph: Icon` renamed to `_static_icon: Icon` for clarity.

## 5. Call site changes

All changes are mechanical.

### String concatenation sites (7 occurrences)

```python
# Before
ICONS.get_icon(group.icon) + " " + group.name
ico_("broken link") + "!"

# After
ICONS.get_icon(group.icon).glyph + " " + group.name
ico_("broken link").glyph + "!"
```

Files affected:
- `src/nova_navigator/dialogs/bookmarks_dialog.py` (2 lines)
- `src/nova_navigator/dialogs/edit_bookmarks_dialog.py` (4 lines)
- `src/nova_navigator/widgets/directory_browser.py` (1 line)

### Widget constructor site

```python
# Before
Button(ico_("xmark"), id="close-button", compact=True)

# After
Button(ico_("xmark").glyph, id="close-button", compact=True)
```

File: `src/nova_navigator/widgets/directory_browser.py`

### `job_status_icon.py`

```python
# Before
self._animated_icon.icon_animate([
    ico_("circle_empty",         default=Icon("○")),
    ico_("circle_quarter",       default=Icon("◔")),
    ico_("circle_half",          default=Icon("◑")),
    ico_("circle_three_quarter", default=Icon("◕")),
    ico_("circle_full",          default=Icon("●")),
], _RUNNING_INTERVAL)

# After
self._animated_icon.icon_animate(ico_("spinner", default=Icon.of("●")), _RUNNING_INTERVAL)
```

Static and pulse uses:
```python
# Before
ico_("circle_full", default=Icon("●"))

# After
ico_("circle_full", default=Icon.of("●"))   # if circle_full is kept as an alias
# or
Icon.of("●")                                # if circle_full is removed entirely
```

Since `circle_full` is removed from the CSV, fall back directly to `Icon.of("●")`.

### `icon_pulse` call in `job_status_icon.py`

```python
# Before
self._animated_icon.icon_pulse(
    ico_("circle_full", default=Icon("●")),
    bright=_FAILED_BRIGHT, dim=_FAILED_DIM, n=_FAILED_N, interval=_FAILED_INTERVAL,
)

# After (circle_full removed from CSV, use Icon.of directly)
self._animated_icon.icon_pulse(
    Icon.of("●"),
    bright=_FAILED_BRIGHT, dim=_FAILED_DIM, n=_FAILED_N, interval=_FAILED_INTERVAL,
)
```

### `nova_navigator_core.py`

No change — `get_icon("checkbox")` etc. still return single-frame `Icon` objects.
`SYMBOL_TABLE` values are set to `Icon` instances; the menu rendering code must be updated accordingly (see below).

### `nova_widgets/menu/_action.py`

`IconProvider` type changes from `Callable[[str], str]` to `Callable[[str], Icon]`.
`_icon: str | None` → `_icon: Icon | None`.
`icon` property return type → `Icon | None`.

### `nova_widgets/menu/_menu.py`

Two render sites use the result of `ICON_PROVIDER` and `SYMBOL_TABLE` as strings and must switch to `.glyph` or `.markup`:

```python
# SYMBOL_TABLE lookup (checkbox/radio rendering)
# Before
segments.append(Segment(SYMBOL_TABLE[kind][1 if action.checked else 0], item_style))
# After
segments.append(Segment(SYMBOL_TABLE[kind][1 if action.checked else 0].markup, item_style))

# Action icon rendering
# Before
icon_text = action.icon          # was str
segments.append(Segment(icon_text.ljust(3), item_style))
# After
icon_text = action.icon          # now Icon
segments.append(Segment(icon_text.glyph.ljust(3), item_style))
```

## 6. Tests

Existing tests in `tests/nova_widgets/test_icon.py` (if present) must be updated to use `Icon.of()` and check `.glyph` instead of `str(icon)`.

New unit tests:
- `Icon.from_glyphs(["○","◔","◑","◕","●"]).is_animated == True`
- `Icon.from_glyphs(["●"]).is_animated == False`
- `Icon.of("●").frames == [Icon.of("●")]`
- `Icon().glyph == "  "` (two spaces)
- CSV loading: `ICONS.get_icon("spinner").is_animated == True` with 5 frames
- CSV loading: `ICONS.get_icon("file").is_animated == False`
- Grapheme regex: `✏️` parses as 1 frame; `○◔◑◕●` parses as 5 frames

## 7. Files changed

| File | Change |
|---|---|
| `src/nova_widgets/icon.py` | Rewrite `Icon` as frozen dataclass |
| `src/nova_navigator/icons.py` | Update storage and `get_icon()` |
| `config/default/icons.csv` | Remove `circle_*`, add `spinner` |
| `src/nova_widgets/animated_icon.py` | Update method signatures |
| `src/nova_navigator/widgets/job_status_icon.py` | Simplify spinner construction |
| `src/nova_navigator/dialogs/bookmarks_dialog.py` | `.glyph` on icon |
| `src/nova_navigator/dialogs/edit_bookmarks_dialog.py` | `.glyph` on icon |
| `src/nova_navigator/widgets/directory_browser.py` | `.glyph` on icon |
| `tests/nova_widgets/test_icon.py` | Update + new tests |
| `src/nova_widgets/menu/_action.py` | `IconProvider` and `_icon` type update |
| `src/nova_widgets/menu/_menu.py` | Use `.glyph`/`.markup` on icon values |
