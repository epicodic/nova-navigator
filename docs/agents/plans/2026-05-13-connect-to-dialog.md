# Connect To Dialog — Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use skills:subagent-driven-development (recommended) or skills:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Add a "Connect To" dialog that lets users pick a saved SSH remote and navigate the active panel to its home directory.

**Architecture:** Approach A — `ConnectToDialog` is pure list-picker UI; `MessageDialog` is a thin reusable error dialog; connection logic lives in `_action_connect_to` in `MainScreen` decorated with `@work`.

**Tech Stack:** Python 3.12, Textual 6.9.0, pytest, pytest-asyncio

**Coding Conventions:** `docs/coding_conventions.md` — read before implementing

---

## File Map

| File | Action | Responsibility |
|------|--------|----------------|
| `src/nova_navigator/dialogs/message_dialog.py` | Create | Reusable plain-text message/error dialog |
| `src/nova_navigator/dialogs/connect_to_dialog.py` | Create | Remote connection list-picker dialog |
| `src/nova_navigator/dialogs/__init__.py` | Modify | Export `MessageDialog`, `ConnectToDialog` |
| `src/nova_navigator/nova_navigator.py` | Modify | Uncomment binding + add `_action_connect_to` handler |
| `tests/dialogs/test_message_dialog.py` | Create | Tests for `MessageDialog` |
| `tests/dialogs/test_connect_to_dialog.py` | Create | Tests for `ConnectToDialog` |

---

## Task 1: `MessageDialog`

**Files:**
- Create: `src/nova_navigator/dialogs/message_dialog.py`
- Test: `tests/dialogs/test_message_dialog.py`

- [ ] **Step 1: Write the failing tests**

```python
# tests/dialogs/test_message_dialog.py
from __future__ import annotations

import pytest
from textual.app import App, ComposeResult
from textual.widgets import Label

from nova_navigator.dialogs.message_dialog import MessageDialog


def _make_app(dialog: MessageDialog) -> type[App[None]]:
    class _App(App[None]):
        def compose(self) -> ComposeResult:
            return iter([])

        async def on_mount(self) -> None:
            await self.push_screen(dialog)

    return _App


@pytest.mark.asyncio
async def test_renders_message() -> None:
    dialog = MessageDialog("Something went wrong")
    app = _make_app(dialog)()
    async with app.run_test(size=(80, 24)) as pilot:
        await pilot.pause()
        labels = list(app.screen.query(Label))
        texts = [str(lbl.renderable) for lbl in labels]
        assert any("Something went wrong" in t for t in texts)


@pytest.mark.asyncio
async def test_ok_dismisses() -> None:
    dismissed: list[str] = []
    dialog = MessageDialog("Error occurred")

    def _capture(result: str) -> None:
        dismissed.append(result)

    dialog.dismiss = _capture  # type: ignore[method-assign]

    app = _make_app(dialog)()
    async with app.run_test(size=(80, 24)) as pilot:
        await pilot.pause()
        await pilot.press("enter")
        await pilot.pause()
        assert dismissed == ["OK"]
```

- [ ] **Step 2: Run tests to verify they fail**

```
uv run pytest tests/dialogs/test_message_dialog.py -v
```
Expected: FAIL — `ModuleNotFoundError: message_dialog`

- [ ] **Step 3: Implement `MessageDialog`**

```python
# src/nova_navigator/dialogs/message_dialog.py
"""MessageDialog — reusable plain-text message/error dialog."""

from __future__ import annotations

from textual.app import ComposeResult
from textual.widgets import Label

from .dialog import DefaultButton, Dialog


class MessageDialog(Dialog):
    """Modal dialog that displays a plain text message with an OK button."""

    _message: str

    def __init__(self, message: str, title: str = "Error") -> None:
        super().__init__(title=title, buttons=[DefaultButton.OK])
        self._message = message

    def compose_content(self) -> ComposeResult:
        yield Label(self._message)
```

- [ ] **Step 4: Run tests to verify they pass**

```
uv run pytest tests/dialogs/test_message_dialog.py -v
```
Expected: PASS (2 tests)

- [ ] **Step 5: Coding-guideline follow-up checklist**

Run this checklist and record PASS/FAIL with file evidence:
- [ ] Conventions file read: `docs/coding_conventions.md`
- [ ] Naming: `MessageDialog` (UpperCamelCase), `_message` (`_` prefix for private), `compose_content` (snake_case)
- [ ] Full type annotations on all methods
- [ ] `from __future__ import annotations` present
- [ ] No `with` context managers in `compose_content()`

---

## Task 2: `ConnectToDialog`

**Files:**
- Create: `src/nova_navigator/dialogs/connect_to_dialog.py`
- Test: `tests/dialogs/test_connect_to_dialog.py`

- [ ] **Step 1: Write the failing tests**

```python
# tests/dialogs/test_connect_to_dialog.py
from __future__ import annotations

import pytest
from textual.app import App, ComposeResult
from textual.widgets import Button, Label, ListItem, ListView

from nova_navigator.config.remotes import RemoteConfig, RemoteConnection, SshSettings
from nova_navigator.dialogs.connect_to_dialog import ConnectToDialog


def _make_config(*connections: RemoteConnection) -> RemoteConfig:
    cfg = object.__new__(RemoteConfig)
    cfg._items = list(connections)
    return cfg


def _make_app(dialog: ConnectToDialog) -> type[App[None]]:
    class _App(App[None]):
        def compose(self) -> ComposeResult:
            return iter([])

        async def on_mount(self) -> None:
            await self.push_screen(dialog)

    return _App


@pytest.mark.asyncio
async def test_empty_remotes_shows_label() -> None:
    cfg = _make_config()
    dialog = ConnectToDialog(cfg)
    app = _make_app(dialog)()
    async with app.run_test(size=(80, 24)) as pilot:
        await pilot.pause()
        labels = list(app.screen.query(Label))
        texts = [str(lbl.renderable) for lbl in labels]
        assert any("No remotes configured" in t for t in texts)


@pytest.mark.asyncio
async def test_empty_remotes_ok_button_disabled() -> None:
    cfg = _make_config()
    dialog = ConnectToDialog(cfg)
    app = _make_app(dialog)()
    async with app.run_test(size=(80, 24)) as pilot:
        await pilot.pause()
        ok_btn = app.screen.query_one("#OK", Button)
        assert ok_btn.disabled


@pytest.mark.asyncio
async def test_single_remote_selected_on_ok() -> None:
    conn = RemoteConnection(name="my-server", ssh=SshSettings(host="1.2.3.4"))
    cfg = _make_config(conn)
    dialog = ConnectToDialog(cfg)
    app = _make_app(dialog)()
    async with app.run_test(size=(80, 24)) as pilot:
        await pilot.pause()
        await pilot.press("enter")
        await pilot.pause()
        assert dialog.selected_connection is conn


@pytest.mark.asyncio
async def test_cancel_returns_none() -> None:
    conn = RemoteConnection(name="my-server", ssh=SshSettings(host="1.2.3.4"))
    cfg = _make_config(conn)
    dialog = ConnectToDialog(cfg)
    dismissed: list[str] = []
    app = _make_app(dialog)()
    async with app.run_test(size=(80, 24)) as pilot:
        await pilot.pause()
        await pilot.press("escape")
        await pilot.pause()
        assert dialog.selected_connection is None


@pytest.mark.asyncio
async def test_double_click_accepts() -> None:
    conn = RemoteConnection(name="my-server", ssh=SshSettings(host="1.2.3.4"))
    cfg = _make_config(conn)
    dialog = ConnectToDialog(cfg)
    app = _make_app(dialog)()
    async with app.run_test(size=(80, 24)) as pilot:
        await pilot.pause()
        lv = app.screen.query_one("#remote_list", ListView)
        item = lv.query(ListItem).first()
        await pilot.hover(item)
        await pilot.click(item)
        await pilot.pause()
        # second click = double-click
        await pilot.hover(item)
        await pilot.click(item)
        await pilot.pause()
        assert dialog.selected_connection is conn
```

- [ ] **Step 2: Run tests to verify they fail**

```
uv run pytest tests/dialogs/test_connect_to_dialog.py -v
```
Expected: FAIL — `ModuleNotFoundError: connect_to_dialog`

- [ ] **Step 3: Implement `ConnectToDialog`**

```python
# src/nova_navigator/dialogs/connect_to_dialog.py
"""ConnectToDialog — pick a saved remote connection."""

from __future__ import annotations

from typing import ClassVar

from textual import events
from textual.app import ComposeResult
from textual.widgets import Label, ListItem, ListView

from nova_navigator.config.remotes import RemoteConfig, RemoteConnection
from nova_navigator.icons import ico_

from .dialog import DefaultButton, Dialog


class _RemoteListItem(ListItem):
    """A list item representing a single remote connection."""

    connection: RemoteConnection

    def __init__(self, connection: RemoteConnection) -> None:
        icon = ico_(connection.icon).glyph if connection.icon else ico_("remote").glyph
        super().__init__(Label(f"{icon} {connection.name}"))
        self.connection = connection


class ConnectToDialog(Dialog):
    """Modal dialog for picking a saved remote connection."""

    DEFAULT_CSS = """
    ConnectToDialog {
        #dialog_box { width: 40; height: auto; }
        #remote_list { height: auto; max-height: 20; border: inner $surface; }
    }
    """

    _DOUBLE_CLICK: ClassVar[int] = 2

    _remotes: RemoteConfig
    _click_is_double: bool
    _last_event_was_click: bool
    selected_connection: RemoteConnection | None

    def __init__(self, remotes: RemoteConfig) -> None:
        super().__init__(title="Connect To", buttons=[DefaultButton.OK, DefaultButton.CANCEL])
        self._remotes = remotes
        self._click_is_double = False
        self._last_event_was_click = False
        self.selected_connection = None

    def compose_content(self) -> ComposeResult:
        connections = list(self._remotes)
        if not connections:
            yield Label("No remotes configured.")
            return
        yield ListView(
            *[_RemoteListItem(c) for c in connections],
            id="remote_list",
        )

    def on_mount(self) -> None:
        connections = list(self._remotes)
        if not connections:
            ok = self.query_one("#OK")
            ok.disabled = True

    def on_list_view_selected(self, event: ListView.Selected) -> None:
        event.stop()
        # Single click → highlight only. Double-click or Enter → accept.
        if self._last_event_was_click:
            self._last_event_was_click = False
            if not self._click_is_double:
                return
        item = event.item
        if isinstance(item, _RemoteListItem):
            self.selected_connection = item.connection
            self.dismiss("OK")

    def on_click(self, event: events.Click) -> None:
        self._click_is_double = event.chain == self._DOUBLE_CLICK
        self._last_event_was_click = True

    def action_accept_dialog(self) -> None:
        if not list(self._remotes):
            return  # OK is disabled; guard against priority Enter binding
        lv = self.query_one("#remote_list", ListView)
        item = lv.highlighted_child
        if isinstance(item, _RemoteListItem):
            self.selected_connection = item.connection
        self.dismiss("OK")
```

- [ ] **Step 4: Run tests to verify they pass**

```
uv run pytest tests/dialogs/test_connect_to_dialog.py -v
```
Expected: PASS (5 tests)

- [ ] **Step 5: Coding-guideline follow-up checklist**

Run this checklist and record PASS/FAIL with file evidence:
- [ ] Conventions file read: `docs/coding_conventions.md`
- [ ] Naming: `ConnectToDialog`, `_RemoteListItem` (UpperCamelCase); `_remotes`, `_click_is_double` (`_` prefix private); all snake_case methods
- [ ] Full type annotations on all methods
- [ ] `from __future__ import annotations` present
- [ ] No `with` context managers in `compose_content()`

---

## Task 3: Export new dialogs

**Files:**
- Modify: `src/nova_navigator/dialogs/__init__.py`

- [ ] **Step 1: Add imports and `__all__` entries**

In `src/nova_navigator/dialogs/__init__.py`, add after the `EditRemotesDialog` import line:

```python
from .connect_to_dialog import ConnectToDialog
from .message_dialog import MessageDialog
```

And add to `__all__`:

```python
    "ConnectToDialog",
    "MessageDialog",
```

- [ ] **Step 2: Verify imports work**

```
uv run python -c "from nova_navigator.dialogs import ConnectToDialog, MessageDialog; print('OK')"
```
Expected output: `OK`

- [ ] **Step 3: Coding-guideline follow-up checklist**

Run this checklist and record PASS/FAIL with file evidence:
- [ ] Naming conventions match existing entries in `__init__.py`
- [ ] `__all__` list remains sorted alphabetically

---

## Task 4: Wire `_action_connect_to` in `MainScreen`

**Files:**
- Modify: `src/nova_navigator/nova_navigator.py`

- [ ] **Step 1: Uncomment the binding**

In `MainScreen.BINDINGS` (around line 67), change:

```python
        #        Binding("ctrl+shift+g", "connect_to", "Connect to Remote", show=False),
```

to:

```python
        Binding("ctrl+shift+g", "connect_to", "Connect to Remote", show=False),
```

- [ ] **Step 2: Add imports**

Change the existing `from nova_navigator.dialogs import ...` line:

```python
from nova_navigator.dialogs import BookmarksDialog, EditBookmarksDialog, EditRemotesDialog, JobsDialog
```

to:

```python
from nova_navigator.dialogs import (
    BookmarksDialog,
    ConnectToDialog,
    EditBookmarksDialog,
    EditRemotesDialog,
    JobsDialog,
    MessageDialog,
)
```

And change the existing `from nova_navigator.vfs.filesystems import LocalFilesystem` line:

```python
from nova_navigator.vfs.filesystems import LocalFilesystem
```

to:

```python
from nova_navigator.vfs.filesystems import LocalFilesystem, SSHFilesystem
```

- [ ] **Step 3: Add `_action_connect_to` handler**

Add this method alongside the other `_action_*` methods (e.g. after `_action_manage_remotes`):

```python
    @work
    async def _action_connect_to(self) -> None:
        dialog = ConnectToDialog(conf_.remotes)
        result = await dialog.run()
        if result != "OK":
            return
        conn = dialog.selected_connection
        if conn is None or conn.ssh is None:
            return
        ssh = conn.ssh
        try:
            fs = await asyncio.to_thread(SSHFilesystem, ssh.host, ssh.port or 22)
        except Exception as exc:
            error_dialog = MessageDialog(f"Could not connect to {conn.name!r}:\n{exc}")
            await error_dialog.run()
            return
        home = await asyncio.to_thread(fs.home)
        self.active_panel().set_path(home)
```

- [ ] **Step 4: Run QA**

```
uv run qa
```
Expected: zero failures

- [ ] **Step 5: Coding-guideline follow-up checklist**

Run this checklist and record PASS/FAIL with file evidence:
- [ ] Conventions file read: `docs/coding_conventions.md`
- [ ] `_action_connect_to` follows `_action_*` naming pattern matching all other handlers
- [ ] Full type annotations (`async def _action_connect_to(self) -> None`)
- [ ] `@work` decorator present (required for async action handlers that use `await`)
- [ ] `asyncio.to_thread` used for both blocking calls (`SSHFilesystem.__init__` and `fs.home()`)
- [ ] `uv run qa` passes with zero failures
