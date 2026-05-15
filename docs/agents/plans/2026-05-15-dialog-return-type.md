# Dialog Return-Type Cleanup Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use skills:subagent-driven-development (recommended) or skills:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Make `Dialog` return `Decision | None` instead of `str`, route all OK button clicks through `action_accept_dialog()`, and move `.save()` calls out of dialogs into callers.

**Architecture:** Change `Dialog(ModalScreen[str])` → `Dialog(ModalScreen[Decision | None])`. Update `ButtonSpec` to carry a `Decision` value. Route accept button clicks through `action_accept_dialog()` in the base class. Subclasses with only dialog buttons (OK/CANCEL) need no `on_button_pressed` override; subclasses with custom action buttons replace their monolithic override with `@on` handlers. Remove `.save()` from dialogs; callers save after a positive result.

**Tech Stack:** Python 3.12, pytest

**Coding Conventions:** `docs/coding_conventions.md` — read before implementing

---

## File Map

| File | Change |
|---|---|
| `src/nova_navigator/dialogs/dialog.py` | Overhaul base class |
| `src/nova_navigator/dialogs/icon_picker_dialog.py` | Fix `action_accept_dialog`; add `selected_icon` property |
| `src/nova_navigator/dialogs/file_dialog.py` | Drop `on_button_pressed` override |
| `src/nova_navigator/dialogs/files_dialog.py` | Drop `on_button_pressed` override |
| `src/nova_navigator/dialogs/settings_dialog.py` | Drop `on_button_pressed`; remove `.save()` |
| `src/nova_navigator/dialogs/connect_to_dialog.py` | Drop `on_button_pressed` override |
| `src/nova_navigator/dialogs/edit_bookmarks_dialog.py` | Replace `on_button_pressed` with `@on`; fix icon-picked callback; remove `.save()` |
| `src/nova_navigator/dialogs/edit_remotes_dialog.py` | Replace `on_button_pressed` with `@on`; fix icon/identity callbacks; remove `.save()` |
| `src/nova_navigator/dialogs/bookmarks_dialog.py` | Fix callback type; add `.save()` |
| `src/nova_navigator/nova_navigator.py` | Fix result comparisons; add `.save()` |
| `src/nova_navigator/filemanager/jobs.py` | Fix result comparisons; add `Decision` import |
| `src/nova_navigator/remotes/ssh.py` | Fix result comparisons |
| `tests/filemanager/test_jobs.py` | Update mocks to return `Decision` values |

---

## Task 1: Overhaul `Dialog` base class

**Files:**
- Modify: `src/nova_navigator/dialogs/dialog.py`

This is the foundation. All other tasks depend on it.

- [ ] **Step 1: Update `ButtonSpec` and remove `_default_button`**

Replace the existing `ButtonSpec` dataclass and `_default_button` function with:

```python
@dataclass
class ButtonSpec:
    decision: Decision
    label: str | None = None          # None → use decision.tr
    variant: ButtonVariant | None = None  # None → derive from is_positive

    @property
    def id(self) -> str:
        return self.decision.name

    @property
    def display_label(self) -> str:
        return self.label if self.label is not None else self.decision.tr

    @property
    def display_variant(self) -> ButtonVariant:
        if self.variant is not None:
            return self.variant
        return "primary" if self.decision.is_positive else "error"
```

Delete the `_default_button` function entirely. `DefaultButton = Decision` alias stays — it is still used by callers.

- [ ] **Step 2: Update `Dialog.__init__`**

The loop that builds `self._buttons` becomes:

```python
self._buttons = []
for button in buttons or [DefaultButton.OK]:
    if isinstance(button, Decision):
        button_to_add = ButtonSpec(decision=button)
    else:
        button_to_add = button  # already a ButtonSpec
    if button_to_add.decision.is_positive:
        self._button_accept = button_to_add
    elif button_to_add.decision.is_negative:
        self._button_dismiss = button_to_add
    self._buttons.append(button_to_add)
```

- [ ] **Step 3: Update `Dialog.compose` to use new ButtonSpec properties**

```python
self._button_box = ButtonBox(
    [
        Button(
            button.display_label,
            id=button.id,
            variant=button.display_variant,
        )
        for button in self._buttons
    ],
    id="button_box",
)
```

- [ ] **Step 4: Update `Dialog` class declaration and `run()`**

```python
class Dialog(ModalScreen[Decision | None]):
    ...
    async def run(self) -> Decision | None:
        self.focus()
        return await self.app.push_screen_wait(screen=self)
```

- [ ] **Step 5: Update `on_button_pressed` — filter non-dialog buttons, route accept through hook**

```python
def on_button_pressed(self, event: Button.Pressed) -> None:
    _dialog_button_ids = {b.id for b in self._buttons}
    if event.button.id not in _dialog_button_ids:
        return  # custom action button — handled by subclass @on handler
    if self._button_accept and event.button.id == self._button_accept.id:
        self.action_accept_dialog()
    else:
        self.dismiss(Decision[event.button.id])
```

- [ ] **Step 6: Update `action_accept_dialog` and `action_dismiss_dialog`**

```python
def action_accept_dialog(self) -> None:
    if self._button_accept:
        self.dismiss(self._button_accept.decision)

def action_dismiss_dialog(self) -> None:
    if self._button_dismiss:
        self.dismiss(self._button_dismiss.decision)
    else:
        self.dismiss(None)
```

- [ ] **Step 7: Run QA**

```sh
uv run qa
```

Expected: zero failures. If type errors arise, fix them before continuing.

- [ ] **Step 8: Coding-guideline follow-up checklist (mandatory before task completion)**
  - [ ] Conventions file read: `docs/coding_conventions.md`
  - [ ] All new/changed symbols have full type annotations
  - [ ] No `# noqa` or `# type: ignore` added
  - [ ] `uv run qa` passes

---

## Task 2: Fix `IconPickerDialog`

**Files:**
- Modify: `src/nova_navigator/dialogs/icon_picker_dialog.py`

`IconPickerDialog.action_accept_dialog` currently dismisses with the icon name string. After Task 1, `Dialog` is `ModalScreen[Decision | None]`, so dismissing with a string is a type error. Fix by adding a `selected_icon` property and delegating to `super()`.

- [ ] **Step 1: Add `selected_icon` property and fix `action_accept_dialog`**

```python
@property
def selected_icon(self) -> str | None:
    """The icon name selected by the user, or None if nothing is selected."""
    return self._selected_icon

def action_accept_dialog(self) -> None:
    if self._selected_icon is not None:
        super().action_accept_dialog()  # dismisses with Decision.OK
    # If nothing selected, do not dismiss — keep dialog open.
```

- [ ] **Step 2: Run QA**

```sh
uv run qa
```

- [ ] **Step 3: Coding-guideline follow-up checklist**
  - [ ] `uv run qa` passes

---

## Task 3: Fix `FileDialog`

**Files:**
- Modify: `src/nova_navigator/dialogs/file_dialog.py`

`FileDialog.on_button_pressed` exists only to validate on OK and call `self.dismiss(self._button_accept.id)`. After Task 1, the base class routes OK clicks through `action_accept_dialog()` which already contains the full validation logic. Drop the override entirely.

- [ ] **Step 1: Remove `on_button_pressed` from `FileDialog`**

Delete the entire method:

```python
# Delete this method:
def on_button_pressed(self, event: Button.Pressed) -> None:
    """Intercept OK to validate; let Cancel fall through to Dialog base."""
    if self._button_accept and event.button.id == self._button_accept.id:
        event.stop()
        if self._validate_and_store():
            self.dismiss(self._button_accept.id)
    else:
        super().on_button_pressed(event)
```

The base class now routes OK to `action_accept_dialog()` which already does validation and calls `super().action_accept_dialog()`. CANCEL is handled by the base class dismissing with `Decision.CANCEL`.

- [ ] **Step 2: Run QA**

```sh
uv run qa
```

- [ ] **Step 3: Coding-guideline follow-up checklist**
  - [ ] `uv run qa` passes

---

## Task 4: Fix `CopyMoveFilesDialog`

**Files:**
- Modify: `src/nova_navigator/dialogs/files_dialog.py`

`CopyMoveFilesDialog.on_button_pressed` calls `_capture_filename()` on OK, then delegates to super. After Task 1, the base class routes OK to `action_accept_dialog()` which already calls `_capture_filename()`. Drop the override.

- [ ] **Step 1: Remove `on_button_pressed` from `CopyMoveFilesDialog`**

Delete the entire method:

```python
# Delete this method:
def on_button_pressed(self, event: Button.Pressed) -> None:
    if self._button_accept and event.button.id == self._button_accept.id:
        self._capture_filename()
    super().on_button_pressed(event)
```

`action_accept_dialog` already handles filename capture and the base class handles dismissal.

- [ ] **Step 2: Run QA**

```sh
uv run qa
```

- [ ] **Step 3: Coding-guideline follow-up checklist**
  - [ ] `uv run qa` passes

---

## Task 5: Fix `SettingsDialog`

**Files:**
- Modify: `src/nova_navigator/dialogs/settings_dialog.py`

`SettingsDialog.on_button_pressed` routes OK to `action_accept_dialog`. After Task 1, the base class does this automatically. Drop the override. Also remove `.save()` from `action_accept_dialog` — saving moves to the caller.

- [ ] **Step 1: Remove `on_button_pressed` and drop `.save()` from `action_accept_dialog`**

Delete the entire `on_button_pressed` method:

```python
# Delete this method:
def on_button_pressed(self, event: Button.Pressed) -> None:
    if self._button_accept and event.button.id == self._button_accept.id:
        self.action_accept_dialog()
    else:
        self.dismiss(event.button.id)
```

Update `action_accept_dialog`:

```python
def action_accept_dialog(self) -> None:
    for field_name, editor in self._editors.items():
        editor.apply(getattr(self._settings, field_name))
    super().action_accept_dialog()
```

- [ ] **Step 2: Run QA**

```sh
uv run qa
```

- [ ] **Step 3: Coding-guideline follow-up checklist**
  - [ ] `uv run qa` passes

---

## Task 6: Fix `ConnectToDialog`

**Files:**
- Modify: `src/nova_navigator/dialogs/connect_to_dialog.py`

`ConnectToDialog.on_button_pressed` routes OK to `action_accept_dialog`. After Task 1, the base class does this. Drop the override.

- [ ] **Step 1: Remove `on_button_pressed` from `ConnectToDialog`**

Delete the entire method:

```python
# Delete this method:
def on_button_pressed(self, event: Button.Pressed) -> None:
    if self._button_accept and event.button.id == self._button_accept.id:
        event.stop()
        self.action_accept_dialog()
    else:
        super().on_button_pressed(event)
```

`action_accept_dialog` already stores `selected_connection` and dismisses. The base class routes OK clicks there automatically.

- [ ] **Step 2: Run QA**

```sh
uv run qa
```

- [ ] **Step 3: Coding-guideline follow-up checklist**
  - [ ] `uv run qa` passes

---

## Task 7: Fix `EditBookmarksDialog`

**Files:**
- Modify: `src/nova_navigator/dialogs/edit_bookmarks_dialog.py`

Replace the monolithic `on_button_pressed` with individual `@on` handlers for each custom action button. Remove `.save()` from `_action_ok`. Fix `_on_icon_picked` to receive `Decision | None` and read the icon from the dialog instance.

- [ ] **Step 1: Replace `on_button_pressed` with `@on` handlers**

Delete the entire `on_button_pressed` method and `action_accept_dialog` wrapper, and add:

```python
@on(Button.Pressed, "#btn_add_group")
def _on_btn_add_group(self, _event: Button.Pressed) -> None:
    self.action_add_group()

@on(Button.Pressed, "#btn_add_entry")
def _on_btn_add_entry(self, _event: Button.Pressed) -> None:
    self.action_add_entry()

@on(Button.Pressed, "#btn_remove")
def _on_btn_remove(self, _event: Button.Pressed) -> None:
    self.action_remove_item()

@on(Button.Pressed, "#btn_move_up")
def _on_btn_move_up(self, _event: Button.Pressed) -> None:
    self.action_move_up()

@on(Button.Pressed, "#btn_move_down")
def _on_btn_move_down(self, _event: Button.Pressed) -> None:
    self.action_move_down()

@on(Button.Pressed, "#btn_move_to_group")
def _on_btn_move_to_group(self, _event: Button.Pressed) -> None:
    self._run_move_to_group()

@on(Button.Pressed, "#btn_pick_icon")
def _on_btn_pick_icon(self, _event: Button.Pressed) -> None:
    self._run_pick_icon()
```

The `action_accept_dialog` (which delegates to `_action_ok`) stays but is now only invoked by the base class on OK. Keep `action_accept_dialog` as the entry point:

```python
def action_accept_dialog(self) -> None:
    self._action_ok()
```

- [ ] **Step 2: Remove `.save()` from `_action_ok`**

```python
def _action_ok(self) -> None:
    self._config.groups = self._working.groups
    self.dismiss(Decision.OK)
```

- [ ] **Step 3: Fix `_run_pick_icon` and `_on_icon_picked`**

Capture the dialog instance so the callback can read `selected_icon`:

```python
def _run_pick_icon(self) -> None:
    current = self.query_one("#input_icon", Input).value or None
    _dlg = IconPickerDialog(initial_icon=current)

    def _on_icon_picked(result: Decision | None) -> None:
        if result != Decision.OK:
            return
        if _dlg.selected_icon is not None:
            self.query_one("#input_icon", Input).value = _dlg.selected_icon

    self.app.push_screen(_dlg, callback=_on_icon_picked)
```

Delete the old `_on_icon_picked` method (it is now replaced by the nested function above).

- [ ] **Step 4: Run QA**

```sh
uv run qa
```

- [ ] **Step 5: Coding-guideline follow-up checklist**
  - [ ] `uv run qa` passes

---

## Task 8: Fix `EditRemotesDialog`

**Files:**
- Modify: `src/nova_navigator/dialogs/edit_remotes_dialog.py`

Same pattern as Task 7.

- [ ] **Step 1: Replace `on_button_pressed` with `@on` handlers**

Delete the entire `on_button_pressed` method and add:

```python
@on(Button.Pressed, "#btn_add")
def _on_btn_add(self, _event: Button.Pressed) -> None:
    self._on_add()

@on(Button.Pressed, "#btn_remove")
def _on_btn_remove(self, _event: Button.Pressed) -> None:
    self._on_remove()

@on(Button.Pressed, "#btn_pick_icon")
def _on_btn_pick_icon(self, _event: Button.Pressed) -> None:
    self._run_pick_icon()

@on(Button.Pressed, "#btn_pick_identity_file")
def _on_btn_pick_identity_file(self, _event: Button.Pressed) -> None:
    self._run_pick_identity_file()
```

- [ ] **Step 2: Remove `.save()` from `action_accept_dialog`**

```python
def action_accept_dialog(self) -> None:
    self._config._items = self._working
    self.dismiss(Decision.OK)
```

- [ ] **Step 3: Fix `_run_pick_icon` and `_on_icon_picked`**

Capture the dialog instance so the callback can read `selected_icon`. Delete the existing `_on_icon_picked` method and rewrite `_run_pick_icon`:

```python
def _run_pick_icon(self) -> None:
    current_icon = self.query_one("#input_icon", Input).value or None
    _dlg = IconPickerDialog(initial_icon=current_icon)

    def _on_icon_picked(result: Decision | None) -> None:
        if result != Decision.OK:
            return
        if _dlg.selected_icon is not None:
            self.query_one("#input_icon", Input).value = _dlg.selected_icon

    self.app.push_screen(_dlg, callback=_on_icon_picked)
```

- [ ] **Step 4: Fix `_run_pick_identity_file` callback**

The existing closure `_on_picked` checks `result != Decision.CANCEL.name`. Update the callback type and comparison:

```python
def _run_pick_identity_file(self) -> None:
    current = self.query_one("#input_identity_file", Input).value.strip()
    _ssh_dir = pathlib.Path.home() / ".ssh"
    if current:
        start = pathlib.Path(current).parent
    elif _ssh_dir.is_dir():
        start = _ssh_dir
    else:
        start = pathlib.Path.home()
    dialog = FileDialog(mode=FileDialogMode.OPEN, start_path=start, title="Select Identity File")

    def _on_picked(result: Decision | None) -> None:
        if result == Decision.OK and dialog.selected_path is not None:
            self.query_one("#input_identity_file", Input).value = str(dialog.selected_path)

    self.app.push_screen(dialog, callback=_on_picked)
```

- [ ] **Step 5: Run QA**

```sh
uv run qa
```

- [ ] **Step 6: Coding-guideline follow-up checklist**
  - [ ] `uv run qa` passes

---

## Task 9: Fix callers

**Files:**
- Modify: `src/nova_navigator/dialogs/bookmarks_dialog.py`
- Modify: `src/nova_navigator/nova_navigator.py`
- Modify: `src/nova_navigator/filemanager/jobs.py`
- Modify: `src/nova_navigator/remotes/ssh.py`

All callers currently compare `dialog.run()` results against strings. Update them to compare against `Decision` enum values and add `.save()` calls where dialogs no longer save.

- [ ] **Step 1: Fix `bookmarks_dialog.py`**

Update the `_after_edit` callback in `on_button_pressed`:

```python
def on_button_pressed(self, event: Button.Pressed) -> None:
    if event.button.id != "btn_edit":
        return

    async def _after_edit(result: Decision | None) -> None:
        if result == Decision.OK:
            conf_.bookmarks.save()
            self._rebuild_tree()
            self.query_one(Tree).focus()

    self.app.push_screen(EditBookmarksDialog(conf_.bookmarks), callback=_after_edit)
```

Add `from nova_navigator.decision import Decision` to imports if not already present.

- [ ] **Step 2: Fix `nova_navigator.py` — settings, bookmarks, remotes actions**

`_action_settings`:
```python
@work
async def _action_settings(self) -> None:
    dialog = SettingsDialog(conf_.settings)
    if await dialog.run() == Decision.OK:
        conf_.settings.save()
```

`_action_edit_bookmarks`:
```python
@work
async def _action_edit_bookmarks(self) -> None:
    dialog = EditBookmarksDialog(conf_.bookmarks)
    if await dialog.run() == Decision.OK:
        conf_.bookmarks.save()
```

`_action_manage_remotes`:
```python
@work
async def _action_manage_remotes(self) -> None:
    dialog = EditRemotesDialog(conf_.remotes)
    if await dialog.run() == Decision.OK:
        conf_.remotes.save()
```

`_action_add_to_bookmarks`:
```python
@work
async def _action_add_to_bookmarks(self) -> None:
    path = self.active_panel().path_item_under_cursor
    if path is None:
        return
    dialog = EditBookmarksDialog(
        conf_.bookmarks,
        prefill=(DEFAULT_BOOKMARKS_GROUP, path.name, str(path.path)),
    )
    if await dialog.run() == Decision.OK:
        conf_.bookmarks.save()
```

`_action_connect_to`:
```python
result = await dialog.run()
if result != Decision.OK:
    return
```

- [ ] **Step 3: Fix `filemanager/jobs.py`**

Add `from nova_navigator.decision import Decision` import.

```python
result = await dialog.run()
if result != Decision.OK:
    _logger.info("%s cancelled by user", "Move" if move else "Copy")
    return None
```

```python
result = await dialog.run()
if result != Decision.YES:
    _logger.info("Delete cancelled by user")
    return None
```

- [ ] **Step 4: Fix `remotes/ssh.py`**

```python
if await confirm.run() != Decision.OK:
    return None
```

```python
if await cred_dialog.run() != Decision.OK:
    return None
```

- [ ] **Step 5: Run QA**

```sh
uv run qa
```

- [ ] **Step 6: Coding-guideline follow-up checklist**
  - [ ] `uv run qa` passes

---

## Task 10: Update tests

**Files:**
- Modify: `tests/filemanager/test_jobs.py`

The mock helpers return strings; update them to return `Decision` values.

- [ ] **Step 1: Update mock helpers and test assertions**

Add import:
```python
from nova_navigator.decision import Decision
```

Update helpers:
```python
def _mock_copy_dialog(result: Decision, filename: str | None = None) -> MagicMock:
    dialog = MagicMock()
    dialog.run = AsyncMock(return_value=result)
    dialog.filename = filename
    return dialog


def _mock_delete_dialog(result: Decision) -> MagicMock:
    dialog = MagicMock()
    dialog.run = AsyncMock(return_value=result)
    return dialog
```

Update all call sites:
- `_mock_copy_dialog("CANCEL")` → `_mock_copy_dialog(Decision.CANCEL)`
- `_mock_copy_dialog("OK", ...)` → `_mock_copy_dialog(Decision.OK, ...)`
- `_mock_delete_dialog("NO")` → `_mock_delete_dialog(Decision.NO)`
- `_mock_delete_dialog("YES")` → `_mock_delete_dialog(Decision.YES)`

- [ ] **Step 2: Run tests**

```sh
uv run pytest tests/filemanager/test_jobs.py -v
```

Expected: all tests pass.

- [ ] **Step 3: Run full QA**

```sh
uv run qa
```

Expected: zero failures.

- [ ] **Step 4: Coding-guideline follow-up checklist**
  - [ ] `uv run qa` passes

---

## Self-Review

**Spec coverage:**
- ✅ `Dialog` returns `Decision | None` — Task 1
- ✅ `on_button_pressed` in base routes OK through `action_accept_dialog` — Task 1
- ✅ No derived class needs to override `on_button_pressed` for OK/CANCEL routing — Tasks 3–6 (all drop the override)
- ✅ Custom action buttons use `@on` — Tasks 7–8
- ✅ `.save()` removed from dialogs — Tasks 5, 7, 8
- ✅ Callers add `.save()` — Task 9
- ✅ All result comparisons updated from strings to `Decision` — Tasks 9, 10

**Placeholder scan:** None found.

**Type consistency:**
- `ButtonSpec.decision: Decision` defined in Task 1, used in all subsequent tasks consistently.
- `selected_icon: str | None` property defined in Task 2, read in Tasks 7 and 8.
- `Decision | None` as callback parameter type used consistently in Tasks 7, 8, 9.
