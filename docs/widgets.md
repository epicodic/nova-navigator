# Widgets

This document covers the reusable Textual widgets in `nova_navigator/widgets/` that are not large enough to warrant a dedicated page.
`DirectoryBrowser` and `Terminal` have their own dedicated documentation pages.

---

## Separator

**File:** `nova_navigator/widgets/separator.py`

`Separator` renders a single-row horizontal line used to divide sections of UI content visually.
It fills its full width with `─` (box-drawing) characters and uses the `$text-disabled` colour.

### Usage

Mount `Separator` between any two widgets where a visual dividing line is needed.

```python
from nova_navigator.widgets.separator import Separator

def compose(self) -> ComposeResult:
    yield HeaderWidget()
    yield Separator()
    yield BodyWidget()
```

---

## NoSelectListView

**File:** `nova_navigator/widgets/no_select_list_view.py`

`NoSelectListView` extends Textual's `ListView` with selection permanently disabled.
It re-exports `ListItem` as a class attribute so callers can import both from one place.

The standard `ListView` tracks a highlighted index; `NoSelectListView` resets that index to `None` whenever Textual tries to set it, so no item ever appears highlighted or selected.
The widget also sets `can_focus=False`, removing it from the tab-focus chain.

### When to use

Use `NoSelectListView` when you want to display a scrollable list of items without any interaction model (no keyboard navigation, no selected-item highlight).
A typical use case is a read-only status panel or an informational log view.

### Usage

```python
from nova_navigator.widgets.no_select_list_view import NoSelectListView
from textual.widgets import ListItem, Label

def compose(self) -> ComposeResult:
    yield NoSelectListView(
        ListItem(Label("First entry")),
        ListItem(Label("Second entry")),
    )
```

---

## PopupWidget

**File:** `nova_navigator/widgets/popup_widget.py`

`PopupWidget` is a base class for popup panels that float over the screen.
It uses Textual's `overlay: screen` CSS property to position itself in front of all other content.

### Construction

```python
PopupWidget(title: str, position: tuple[int, int])
```

| Parameter | Description |
|-----------|-------------|
| `title` | Text shown in the widget's border title. |
| `position` | `(x, y)` offset (in character cells) from the top-left of the screen. |

Behaviour is configured via class variables (see below), not constructor arguments.

### Class variables

| Variable | Type | Default | Description |
|----------|------|---------|-------------|
| `CLOSE_ACTION` | `CloseAction` | `HIDE` | Controls what `close()` does. |
| `CLOSE_ON_BLUR` | `bool` | `True` | If `True`, losing focus calls `close()`. |
| `SHOW_CLOSE_BUTTON` | `bool` | `False` | If `True`, renders a close button in the border. |

To suppress the default `Escape` binding, override `BINDINGS` in the subclass.

### Close actions

`CloseAction` is an enum with three values.
In all cases, focus is restored to the previously focused widget before the action runs.

| Value | Effect of `close()` |
|-------|---------------------|
| `KEEP` | Leaves the widget visible and in the DOM. |
| `HIDE` | Sets `display = False`; the widget stays in the DOM. |
| `REMOVE` | Removes the widget from the DOM entirely. |

### Focus management

On mount, `PopupWidget` saves a reference to the currently focused widget.
`show()` also refreshes this reference so that re-opening a hidden popup always restores to the most recently focused widget.
When `close()` is called it restores focus to that widget before applying the close action.
This preserves keyboard focus in the underlying panel after a popup is dismissed.

### Blur-based auto-close

When `CLOSE_ON_BLUR = True` the widget monitors both `Blur` and `DescendantBlur` events.
It calls `close()` only when neither the widget itself nor any of its children has focus.
This allows popups that contain focusable children (e.g. input fields) to stay open while the user interacts with those children.

### Key bindings

| Key | Action |
|-----|--------|
| `Escape` | Calls `close()` (defined in `BINDINGS`; override to suppress). |

### Subclassing

Subclass `PopupWidget`, set class variables to configure behaviour, and implement `compose()` to add content.

```python
from nova_navigator.widgets.popup_widget import PopupWidget

class MyPopup(PopupWidget):
    CLOSE_ACTION = PopupWidget.CloseAction.REMOVE
    SHOW_CLOSE_BUTTON = True

    def __init__(self, position: tuple[int, int]) -> None:
        super().__init__("My Popup", position)

    def compose(self) -> ComposeResult:
        yield Label("Hello from popup")
```

---

## SideBar

**File:** `nova_navigator/widgets/side_bar.py`

`SideBar` is a narrow (2-column) vertical strip docked to the left edge of the screen.
It contains a column of `SideBarButton` widgets, each displaying a single icon character.

### SideBarButton

`SideBarButton` is a minimal icon button used exclusively inside `SideBar`.
It renders a single string (intended to be an icon or emoji) and highlights with `$accent` background on hover.

### Layout

`SideBar` docks itself to the left with `height: 100%` and a `$background-lighten-2` background.
Buttons are arranged vertically inside a `Vertical` container with id `side-bar-container`.
