# Dialog Base Class

`Dialog` is the standard base class for all modal dialogs in Nova Navigator.
It lives in `nova_widgets/dialog.py` and is imported with `from nova_widgets import Dialog, ButtonSpec, DefaultButton, Response`.
It is a `ModalScreen[Response | None]` that provides a titled bordered box, a configurable button row, keyboard shortcuts, and a `run()` helper.

`Response` is defined in `nova_widgets/response.py`.
`nova_widgets` never imports `nova_navigator`.
`MessageBox` (with `MessageDialog` as alias) lives in `nova_widgets/message_box.py` and is the generic confirmation and message dialog.

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
from nova_widgets import ButtonSpec, Response

buttons=[
    ButtonSpec(Response.OK, label="Save", variant="primary"),
    ButtonSpec(Response.CANCEL, label="Discard", variant="error"),
]
```

Enter presses the focused button, and the first button holds the initial focus, so put the harmless button first.
`Dialog` keeps the **last** accept-role button (`response.is_accepted`) as its accept button and the **last** reject-role button (`response.is_rejected`) as its dismiss button (the constructor loop overwrites).
Escape dismisses with the dismiss button, and with `None` when there is none.
A button of another role (for example `Response.DISCARD`, the destructive role) or an accept button that is not the last one dismisses with its own `Response`.
Give a dialog at most one reject button.
Do not use `Response.custom(...)`: its `ButtonSpec.id` is `None`, and pressing it raises `KeyError`.

## Showing a Dialog from a Screen

`await dialog.run()` pushes the dialog and waits for its answer, so it must run in a worker, never in `on_event` or a key handler.
Start the work with `self.run_worker(coroutine, group="flow", exit_on_error=True)` and `await dialog.run()` inside it.
Only that worker waits; the event loop and the timers keep running.
`EditorScreen` serialises its flows with one `asyncio.Lock` and counts them before the worker starts, so a second request in the same instant sees the first.
A flow that needs another dialog awaits it directly and never starts and awaits another flow.

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

## KeybindingsDialog and KeyCaptureDialog

`KeybindingsDialog` in `nova_widgets/keybindings_dialog.py` displays all known actions with their current key bindings in an editable table.
It takes three parameters:

- `actions: list[Action]` — the actions to display (typically filtered to a subset like `editor.*` actions).
- `config: KeybindingsConfig` — the keybindings configuration object that loads and saves overrides.
- `key_display_style: KeyFormatStyle | None = None` — optional style for formatting keys (defaults to `KeyFormatStyle.CLASSIC`).

The host passes the actions it wants listed (the editor passes its `editor.*` actions); the dialog keeps unmapped actions unmapped on save.

`KeyCaptureDialog` is a helper modal that opens when the user edits a binding.
It captures a key sequence (supporting multi-chord sequences) and returns the captured sequence or `None` if dismissed.
It takes two parameters:

- `action: Action` — the action being edited.
- `key_display_style: KeyFormatStyle | None = None` — optional style for formatting keys (defaults to `KeyFormatStyle.CLASSIC`).

## File Dialog

`FileDialog` is a modal dialog for selecting files or directories.

It is importable from `nova_widgets`.

### Usage

```python
from nova_widgets import FileDialog, FileDialogMode
from pathlib import Path

dialog = FileDialog(
    mode=FileDialogMode.OPEN,
    start_path=Path.home(),
    title="Select a file",
)
```

### Provider protocol

By default, `FileDialog` browses the local filesystem via `LocalFileProvider`.

To use a custom filesystem backend, pass a `provider` that implements `FileProvider` protocol:

```python
from nova_widgets import FileDialog, FileDialogMode, InMemoryFileProvider
from pathlib import PurePath

provider = InMemoryFileProvider()
provider.add_dir("/")
provider.add_dir("/home/user")
provider.add_file("/home/user/config.txt")

dialog = FileDialog(
    mode=FileDialogMode.OPEN,
    start_path=PurePath("/home/user"),
    provider=provider,
)
```

### Icon provider

The dialog renders folder and file icons via an `icon_provider` callback.

By default (None), the dialog renders blank placeholders.

To use custom icons, pass a callable `Callable[[str], Icon]`:

```python
from nova_navigator.icons import ico_
from nova_widgets import FileDialog, FileDialogMode

dialog = FileDialog(
    mode=FileDialogMode.OPEN,
    icon_provider=ico_,  # Use navigator icons
)
```

### File selection modes

- `FileDialogMode.OPEN`: Select an existing file.
- `FileDialogMode.SAVE`: Select or create a file (accepts non-existent files in existing directories).
- `FileDialogMode.DIR`: Select a directory.

### Result

After dismissal, check `dialog.selected_path`:

```python
if dialog.selected_path:
    print(f"Selected: {dialog.selected_path}")
```

### Pre-filled name

In SAVE mode `FileDialog(..., filename="notes.txt")` fills the name input and gives it the initial focus, so Enter saves that name; `None` (the default) keeps the listing focused.
The prefill survives the first highlight of the listing and is replaced by a later one.
`filename` is ignored in the other modes.

## MessageBox

`MessageBox(..., width="90%")` sets the width of the box for a row of more buttons than the default 50 % holds; `None` keeps the CSS.
Button labels longer than 16 characters wrap, because `Dialog` limits a button to 20 columns.
