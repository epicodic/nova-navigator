# Dialog Base Class

`Dialog` (`nova_navigator.dialogs.dialog`) is the standard base class for all modal dialogs in Nova Navigator.
It is a `ModalScreen[Response | None]` that provides a titled bordered box, a configurable button row, keyboard shortcuts, and a `run()` helper.

## Anatomy

A dialog is composed of two parts stacked vertically inside `#dialog_box`:

1. **Content** — whatever `compose_content()` yields.
2. **Button row** — rendered automatically from the `buttons=` list passed to `__init__`.

The `border_title` of `#dialog_box` is set to the `title=` argument automatically.
A `Footer` widget is rendered below the box to show active key bindings.

## Creating a Dialog

Subclass `Dialog`, call `super().__init__()` with a title and button list, then override `compose_content()`.

```python
class ConfirmDeleteDialog(Dialog):
    DEFAULT_CSS = """
    ConfirmDeleteDialog {
        #dialog_box { width: 50; height: auto; }
    }
    """

    def __init__(self, filename: str) -> None:
        super().__init__(title="Confirm Delete", buttons=[DefaultButton.OK, DefaultButton.CANCEL])
        self._filename = filename

    def compose_content(self) -> ComposeResult:
        yield Label(f"Delete {self._filename!r}?")
```

## Returning a Value

`Dialog.run()` returns the `Response` of the button that was pressed, or `None` if the dialog was dismissed without a button press.

For dialogs that collect input, store the result in instance attributes and expose it via a `@property`.
The caller checks the response first, then reads the property.

```python
class InputNameDialog(Dialog):
    def compose_content(self) -> ComposeResult:
        yield Input(id="name_input")

    @property
    def value(self) -> str:
        return self.query_one("#name_input", Input).value

# Caller:
dialog = InputNameDialog(...)
response = await dialog.run()
if response == Response.OK:
    name = dialog.value
```

## Buttons

Pass a list of `DefaultButton` (which is an alias for `Response`) values to `__init__`.
Common combinations:

| Pattern | Buttons |
|---------|---------|
| Acknowledge | `[DefaultButton.OK]` |
| Confirm / cancel | `[DefaultButton.OK, DefaultButton.CANCEL]` |
| Yes / No | `[DefaultButton.YES, DefaultButton.NO]` |

Use a `ButtonSpec` when you need a custom label or variant on an existing `Response`:

```python
from .dialog import ButtonSpec, DefaultButton

buttons=[
    ButtonSpec(Response.OK, label="Save", variant="primary"),
    ButtonSpec(Response.CANCEL, label="Discard", variant="error"),
]
```

The accept button (first button whose `response.is_accepted` is `True`) is dismissed on Enter.
The reject button (first button whose `response.is_rejected` is `True`) is dismissed on Escape.

## Keyboard Handling

`Dialog` already handles:

- **Escape** (priority binding) → `action_dismiss_dialog()` → dismisses with the reject response.
- **Enter** → if a `Button` has focus, presses it; otherwise calls `action_accept_dialog()`.

### Overriding Key Behaviour

Override `_on_key` when the dialog needs to intercept keys before the base class handles them (e.g., `KeyCaptureDialog` captures every keypress to build a sequence).
Always call `event.prevent_default()` at the end to stop `Dialog._on_key` from also running (Textual calls every `_on_key` in the MRO).

```python
async def _on_key(self, event: events.Key) -> None:
    if event.key == "enter":
        self.action_accept_dialog()   # confirm the capture
    elif event.key == "backspace":
        ...
    elif event.key != "escape":       # escape is handled by priority binding
        ...
    event.prevent_default()           # REQUIRED — stops Dialog._on_key in MRO
    event.stop()
```

Do **not** call `prevent_default()` if you only handle a subset of keys and want `Dialog._on_key` to run for the rest.

## Registering a New Dialog

Every new dialog class must be registered in `src/tools/dialog_tester.py` with a `DialogEntry`.
Add a `factory` lambda and optionally a `result_fn` that prints the response and any `value` properties.

```python
DialogEntry(
    "MyDialog",
    "Short description.",
    lambda: MyDialog(...),
    result_fn=lambda d, r: f"Result: {r}  value={repr(d.value) if r == Response.OK else None}",
),
```

Verify and smoke-test with:

```sh
uv run dialog_tester --list
uv run dialog_tester MyDialog
```

## Pitfall: No `with` Statements in `compose_content()`

`Dialog.compose()` unpacks `compose_content()` with `*self.compose_content()` outside a proper compose parent context.
Using `with SomeContainer():` inside `compose_content()` causes the container to attach directly to the screen instead of to `#dialog_box`, breaking the layout.
Always use the constructor form:

```python
# WRONG
def compose_content(self) -> ComposeResult:
    with Horizontal():
        yield Label("Name:")
        yield Input(id="name")

# CORRECT
def compose_content(self) -> ComposeResult:
    yield Horizontal(Label("Name:"), Input(id="name"))
```
