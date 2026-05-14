# Manage Remote Connections Dialog — Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use skills:subagent-driven-development (recommended) or skills:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Implement `EditRemotesDialog` — a full-screen modal for creating, editing, and deleting saved remote connections, backed by `RemoteConfig`.

**Architecture:** Four sequential tasks. Task 1 extends the data model (`SshSettings.host`). Task 2 implements the full dialog (`edit_remotes_dialog.py`). Task 3 exports the dialog and adds tests. Task 4 runs final QA.

**Tech Stack:** Python 3.12, Textual, pytest, pytest-asyncio

**Coding Conventions:** `docs/coding_conventions.md` — read before implementing

**Spec:** `docs/agents/specs/2026-05-11-manage-remotes-dialog-design.md`

---

## File Map

| Action | Path | Purpose |
|--------|------|---------|
| Edit | `src/nova_navigator/config/remotes.py` | Add `host: str = ""` to `SshSettings` |
| Edit | `tests/config/test_remotes.py` | Update round-trip test to cover `host` field |
| Create | `src/nova_navigator/dialogs/edit_remotes_dialog.py` | `EditRemotesDialog` widget |
| Edit | `src/nova_navigator/dialogs/__init__.py` | Export `EditRemotesDialog` |
| Create | `tests/dialogs/test_manage_remotes_dialog.py` | 9 async dialog tests |

---

## Task 1: Add `host` field to `SshSettings`

**Files:**
- Edit: `src/nova_navigator/config/remotes.py`
- Edit: `tests/config/test_remotes.py`

The dialog stores SSH hostname in `SshSettings.host`.
This field is missing from the current model.

- [ ] **Step 1: Add `host` to `SshSettings`**

In `src/nova_navigator/config/remotes.py`, change `SshSettings` to:

```python
@dataclass
class SshSettings(BaseModel):
    """SSH protocol settings for a remote connection."""

    host: str = ""
    user: str | None = None
    port: int | None = None
    identity_file: str | None = None
```

- [ ] **Step 2: Update `test_remote_connection_round_trip` to cover `host`**

In `tests/config/test_remotes.py`, update the `SshSettings(...)` call in `test_remote_connection_round_trip` to include `host="192.168.1.10"`, and add an assertion:

```python
ssh=SshSettings(host="192.168.1.10", user="alice", port=2222, identity_file="~/.ssh/id_ed25519"),
```

And add:
```python
assert loaded.ssh.host == "192.168.1.10"
```

- [ ] **Step 3: Run config tests**

```
uv run pytest tests/config/test_remotes.py -v
```
Expected: all PASS

- [ ] **Step 4: Run full test suite to check for regressions**

```
uv run pytest -x -q
```
Expected: all PASS

- [ ] **Step 5: Coding-guideline follow-up checklist**

- [ ] Conventions file read: `docs/coding_conventions.md`
- [ ] `uv run ruff check src/nova_navigator/config/remotes.py` — no errors
- [ ] `uv run ty check .` — no new errors introduced

---

## Task 2: Implement `EditRemotesDialog`

**Files:**
- Create: `src/nova_navigator/dialogs/edit_remotes_dialog.py`

This is the main task. Read the full spec at `docs/agents/specs/2026-05-11-manage-remotes-dialog-design.md` before starting.

Also read `src/nova_navigator/dialogs/edit_bookmarks_dialog.py` for the established patterns (deep-copy working model, `_syncing` flag, form↔model sync, `Dialog` base class usage).

- [ ] **Step 1: Create `src/nova_navigator/dialogs/edit_remotes_dialog.py`**

```python
"""Remote connection editor dialog."""

from __future__ import annotations

import copy
from typing import ClassVar

from textual import on
from textual.app import ComposeResult
from textual.binding import Binding, BindingType
from textual.containers import Horizontal, Vertical
from textual.widgets import Button, Checkbox, Input, Label, ListItem, ListView, Select, Static

from nova_navigator.config.remotes import ProxySettings, RemoteConfig, RemoteConnection, SshSettings
from nova_navigator.decision import Decision
from nova_navigator.dialogs.dialog import Dialog
from nova_navigator.dialogs.icon_picker_dialog import IconPickerDialog
from nova_navigator.icons import ICONS

_PROTOCOL_OPTIONS: list[tuple[str, str]] = [("SSH", "ssh")]
_PROXY_TYPE_OPTIONS: list[tuple[str, str]] = [("SOCKS5", "socks5"), ("HTTP", "http")]


class EditRemotesDialog(Dialog):
    """Full-screen modal for editing saved remote connections."""

    DEFAULT_CSS = """
    EditRemotesDialog {
        #dialog_box {
            width: 85%;
            height: 90%;
        }

        #list_row {
            height: 1fr;
        }

        #remote_list {
            width: 1fr;
            border: inner $surface;
        }

        #action_col {
            width: auto;
            height: 1fr;
        }

        #form_container {
            height: auto;
            margin-top: 1;
            padding: 0 1;
        }

        .form_row {
            height: auto;
        }

        .form_label {
            width: auto;
            border: inner transparent;
        }

        Input {
            border: inner $surface;
        }

        #input_name {
            width: 1fr;
        }

        #input_icon {
            width: 20;
        }

        #btn_pick_icon {
            width: 5;
            max-width: 5;
            margin: 0 0 0 1;
        }

        #uri_preview {
            width: 1fr;
            color: $text-muted;
            border: inner transparent;
            padding: 0 1;
        }

        #select_type {
            width: 20;
        }

        #ssh_section {
            height: auto;
        }

        #input_address {
            width: 1fr;
        }

        #input_port {
            width: 10;
        }

        #input_username {
            width: 1fr;
        }

        #input_identity_file {
            width: 1fr;
        }

        #proxy_section {
            height: auto;
        }

        #proxy_fields {
            height: auto;
        }

        #input_proxy_host {
            width: 1fr;
        }

        #input_proxy_port {
            width: 10;
        }

        #select_proxy_type {
            width: 14;
        }
    }
    """

    BINDINGS: ClassVar[list[BindingType]] = [
        Binding("delete", "remove_item", "Remove", show=False),
        Binding("f8", "remove_item", "Remove", show=False),
    ]

    _config: RemoteConfig
    _working: list[RemoteConnection]
    _current_index: int | None
    _syncing: bool

    def __init__(self, config: RemoteConfig) -> None:
        super().__init__("Edit Remote Connections", buttons=[Decision.OK, Decision.CANCEL])
        self._config = config
        self._working = copy.deepcopy(config._items)
        self._current_index = None
        self._syncing = False

    # ------------------------------------------------------------------ compose

    def compose_content(self) -> ComposeResult:
        yield Horizontal(
            ListView(id="remote_list"),
            Vertical(
                Button("Add", id="btn_add", flat=True),
                Button("Remove", id="btn_remove", disabled=True, flat=True),
                id="action_col",
            ),
            id="list_row",
        )
        yield Vertical(
            Horizontal(
                Label("Name: ", classes="form_label"),
                Input(placeholder="Name", id="input_name", disabled=True),
                Label("  Icon: ", classes="form_label"),
                Input(placeholder="Icon", id="input_icon", disabled=True),
                Button("…", id="btn_pick_icon", flat=True, disabled=True),
                classes="form_row",
            ),
            Horizontal(
                Label("URI: ", classes="form_label"),
                Static("", id="uri_preview"),
                classes="form_row",
            ),
            Horizontal(
                Label("Type: ", classes="form_label"),
                Select(
                    options=[(label, value) for label, value in _PROTOCOL_OPTIONS],
                    id="select_type",
                    disabled=True,
                ),
                classes="form_row",
            ),
            Vertical(
                Static("── SSH ──", classes="form_label"),
                Horizontal(
                    Label("Address: ", classes="form_label"),
                    Input(placeholder="hostname or IP", id="input_address", disabled=True),
                    Label("  Port: ", classes="form_label"),
                    Input(placeholder="22", id="input_port", disabled=True),
                    classes="form_row",
                ),
                Horizontal(
                    Label("Username: ", classes="form_label"),
                    Input(placeholder="user", id="input_username", disabled=True),
                    classes="form_row",
                ),
                Horizontal(
                    Label("Identity File: ", classes="form_label"),
                    Input(placeholder="~/.ssh/id_ed25519", id="input_identity_file", disabled=True),
                    classes="form_row",
                ),
                id="ssh_section",
            ),
            Vertical(
                Static("── Proxy ──", classes="form_label"),
                Checkbox("Enable proxy", id="check_proxy", disabled=True),
                Vertical(
                    Horizontal(
                        Label("Host: ", classes="form_label"),
                        Input(placeholder="proxy host", id="input_proxy_host", disabled=True),
                        Label("  Port: ", classes="form_label"),
                        Input(placeholder="1080", id="input_proxy_port", disabled=True),
                        classes="form_row",
                    ),
                    Horizontal(
                        Label("Type: ", classes="form_label"),
                        Select(
                            options=[(label, value) for label, value in _PROXY_TYPE_OPTIONS],
                            id="select_proxy_type",
                            disabled=True,
                        ),
                        classes="form_row",
                    ),
                    id="proxy_fields",
                ),
                id="proxy_section",
            ),
            id="form_container",
        )

    def on_mount(self) -> None:
        self._rebuild_list(select_index=0 if self._working else None)

    # ------------------------------------------------------------------ list

    def _make_list_label(self, entry: RemoteConnection) -> str:
        icon = ICONS.get_icon(entry.icon).glyph + " " if entry.icon else ""
        uri = entry.uri or ""
        return f"{icon}{entry.name}  {uri}"

    def _rebuild_list(self, select_index: int | None) -> None:
        lv = self.query_one("#remote_list", ListView)
        lv.clear()
        for entry in self._working:
            lv.append(ListItem(Label(self._make_list_label(entry))))
        if select_index is not None and self._working:
            idx = min(select_index, len(self._working) - 1)
            lv.index = idx

    def _update_list_item_label(self, index: int) -> None:
        lv = self.query_one("#remote_list", ListView)
        items = list(lv.query(ListItem))
        if index < len(items):
            label = items[index].query_one(Label)
            label.update(self._make_list_label(self._working[index]))

    # ------------------------------------------------------------------ form sync

    def _set_form_disabled(self, disabled: bool) -> None:
        for widget_id in (
            "#input_name", "#input_icon", "#btn_pick_icon", "#select_type",
            "#input_address", "#input_port", "#input_username", "#input_identity_file",
            "#check_proxy",
        ):
            self.query_one(widget_id).disabled = disabled  # type: ignore[union-attr]

    def _sync_form(self, index: int | None) -> None:
        self._current_index = index
        self._syncing = True
        try:
            if index is None:
                self._set_form_disabled(True)
                self.query_one("#input_name", Input).value = ""
                self.query_one("#input_icon", Input).value = ""
                self.query_one("#uri_preview", Static).update("")
                self.query_one("#input_address", Input).value = ""
                self.query_one("#input_port", Input).value = ""
                self.query_one("#input_username", Input).value = ""
                self.query_one("#input_identity_file", Input).value = ""
                self.query_one("#check_proxy", Checkbox).value = False
                self.query_one("#input_proxy_host", Input).value = ""
                self.query_one("#input_proxy_port", Input).value = ""
                self.query_one("#ssh_section").display = False
                self.query_one("#proxy_fields").display = False
                self._update_remove_button()
                return

            entry = self._working[index]
            self._set_form_disabled(False)

            self.query_one("#input_name", Input).value = entry.name
            self.query_one("#input_icon", Input).value = entry.icon or ""
            self.query_one("#uri_preview", Static).update(entry.uri or "")

            # protocol
            proto = "ssh"  # only supported for now
            self.query_one("#select_type", Select).value = proto
            self.query_one("#ssh_section").display = (proto == "ssh")

            # SSH fields
            ssh = entry.ssh or SshSettings()
            self.query_one("#input_address", Input).value = ssh.host
            self.query_one("#input_port", Input).value = str(ssh.port) if ssh.port is not None else ""
            self.query_one("#input_username", Input).value = ssh.user or ""
            self.query_one("#input_identity_file", Input).value = ssh.identity_file or ""

            # proxy
            proxy_enabled = entry.proxy is not None
            self.query_one("#check_proxy", Checkbox).value = proxy_enabled
            self.query_one("#proxy_fields").display = proxy_enabled
            proxy = entry.proxy or ProxySettings()
            self.query_one("#input_proxy_host", Input).value = proxy.host
            self.query_one("#input_proxy_port", Input).value = str(proxy.port) if proxy.port != 1080 else ""  # noqa: PLR2004
            self.query_one("#select_proxy_type", Select).value = proxy.type
            self.query_one("#input_proxy_host", Input).disabled = not proxy_enabled
            self.query_one("#input_proxy_port", Input).disabled = not proxy_enabled
            self.query_one("#select_proxy_type", Select).disabled = not proxy_enabled

            self._update_remove_button()
        finally:
            self._syncing = False

    def _update_remove_button(self) -> None:
        self.query_one("#btn_remove", Button).disabled = self._current_index is None

    # ------------------------------------------------------------------ URI assembly

    def _build_uri_preview(self) -> str:
        proto = "ssh"
        address = self.query_one("#input_address", Input).value.strip()
        port_str = self.query_one("#input_port", Input).value.strip()
        username = self.query_one("#input_username", Input).value.strip()
        if not address:
            return ""
        netloc = f"{username}@{address}" if username else address
        if port_str and port_str != "22":
            netloc = f"{netloc}:{port_str}"
        return f"{proto}://{netloc}"

    def _assemble_and_store_uri(self) -> None:
        if self._current_index is None:
            return
        uri = self._build_uri_preview()
        self._working[self._current_index].uri = uri
        self.query_one("#uri_preview", Static).update(uri)
        self._update_list_item_label(self._current_index)

    # ------------------------------------------------------------------ event handlers

    @on(ListView.Highlighted)
    def _on_list_highlighted(self, event: ListView.Highlighted) -> None:
        index = event.list_view.index
        self._sync_form(index)

    @on(Button.Pressed, "#btn_add")
    def _on_add(self) -> None:
        new_entry = RemoteConnection(name="new-connection", ssh=SshSettings())
        self._working.append(new_entry)
        lv = self.query_one("#remote_list", ListView)
        lv.append(ListItem(Label(self._make_list_label(new_entry))))
        lv.index = len(self._working) - 1

    @on(Button.Pressed, "#btn_remove")
    def _on_remove(self) -> None:
        if self._current_index is None:
            return
        idx = self._current_index
        self._working.pop(idx)
        new_index = min(idx, len(self._working) - 1) if self._working else None
        self._rebuild_list(select_index=new_index)
        if new_index is None:
            self._sync_form(None)

    @on(Button.Pressed, "#btn_pick_icon")
    async def _on_pick_icon(self) -> None:
        current_icon = self.query_one("#input_icon", Input).value or None
        result = await self.app.push_screen_wait(IconPickerDialog(initial_icon=current_icon))
        if result and result != Decision.CANCEL.name:
            self.query_one("#input_icon", Input).value = result

    @on(Input.Changed, "#input_name")
    def _on_name_changed(self, event: Input.Changed) -> None:
        if self._syncing or self._current_index is None:
            return
        self._working[self._current_index].name = event.value
        self._update_list_item_label(self._current_index)

    @on(Input.Changed, "#input_icon")
    def _on_icon_changed(self, event: Input.Changed) -> None:
        if self._syncing or self._current_index is None:
            return
        self._working[self._current_index].icon = event.value or None
        self._update_list_item_label(self._current_index)

    @on(Input.Changed, "#input_address")
    def _on_address_changed(self, event: Input.Changed) -> None:
        if self._syncing or self._current_index is None:
            return
        entry = self._working[self._current_index]
        if entry.ssh is None:
            entry.ssh = SshSettings()
        entry.ssh.host = event.value
        self._assemble_and_store_uri()

    @on(Input.Changed, "#input_port")
    def _on_port_changed(self, event: Input.Changed) -> None:
        if self._syncing or self._current_index is None:
            return
        entry = self._working[self._current_index]
        if entry.ssh is None:
            entry.ssh = SshSettings()
        port_str = event.value.strip()
        entry.ssh.port = int(port_str) if port_str.isdigit() else None
        self._assemble_and_store_uri()

    @on(Input.Changed, "#input_username")
    def _on_username_changed(self, event: Input.Changed) -> None:
        if self._syncing or self._current_index is None:
            return
        entry = self._working[self._current_index]
        if entry.ssh is None:
            entry.ssh = SshSettings()
        entry.ssh.user = event.value or None
        self._assemble_and_store_uri()

    @on(Input.Changed, "#input_identity_file")
    def _on_identity_file_changed(self, event: Input.Changed) -> None:
        if self._syncing or self._current_index is None:
            return
        entry = self._working[self._current_index]
        if entry.ssh is None:
            entry.ssh = SshSettings()
        entry.ssh.identity_file = event.value or None

    @on(Checkbox.Changed, "#check_proxy")
    def _on_proxy_toggled(self, event: Checkbox.Changed) -> None:
        if self._syncing or self._current_index is None:
            return
        entry = self._working[self._current_index]
        proxy_enabled = event.value
        if proxy_enabled and entry.proxy is None:
            entry.proxy = ProxySettings()
        elif not proxy_enabled:
            entry.proxy = None
        self.query_one("#proxy_fields").display = proxy_enabled
        for widget_id in ("#input_proxy_host", "#input_proxy_port", "#select_proxy_type"):
            self.query_one(widget_id).disabled = not proxy_enabled  # type: ignore[union-attr]
        if proxy_enabled and entry.proxy:
            self._syncing = True
            try:
                self.query_one("#input_proxy_host", Input).value = entry.proxy.host
                self.query_one("#input_proxy_port", Input).value = str(entry.proxy.port) if entry.proxy.port != 1080 else ""  # noqa: PLR2004
                self.query_one("#select_proxy_type", Select).value = entry.proxy.type
            finally:
                self._syncing = False

    @on(Input.Changed, "#input_proxy_host")
    def _on_proxy_host_changed(self, event: Input.Changed) -> None:
        if self._syncing or self._current_index is None:
            return
        entry = self._working[self._current_index]
        if entry.proxy is None:
            return
        entry.proxy.host = event.value

    @on(Input.Changed, "#input_proxy_port")
    def _on_proxy_port_changed(self, event: Input.Changed) -> None:
        if self._syncing or self._current_index is None:
            return
        entry = self._working[self._current_index]
        if entry.proxy is None:
            return
        port_str = event.value.strip()
        entry.proxy.port = int(port_str) if port_str.isdigit() else 1080  # noqa: PLR2004

    @on(Select.Changed, "#select_proxy_type")
    def _on_proxy_type_changed(self, event: Select.Changed) -> None:
        if self._syncing or self._current_index is None:
            return
        entry = self._working[self._current_index]
        if entry.proxy is None:
            return
        entry.proxy.type = str(event.value)

    # ------------------------------------------------------------------ dialog result

    def action_accept_dialog(self) -> None:
        self._config._items = self._working
        self._config.save()
        self.dismiss(Decision.OK.name)

    def action_remove_item(self) -> None:
        self._on_remove()
```

- [ ] **Step 2: Run a quick smoke check**

```
uv run python -c "from nova_navigator.dialogs.edit_remotes_dialog import EditRemotesDialog; print('OK')"
```
Expected: `OK`

- [ ] **Step 3: Coding-guideline follow-up checklist**

- [ ] Conventions file read: `docs/coding_conventions.md`
- [ ] All methods have full type annotations
- [ ] `uv run ruff check src/nova_navigator/dialogs/edit_remotes_dialog.py` — no errors
- [ ] `uv run ty check .` — no new errors

---

## Task 3: Export dialog and write tests

**Files:**
- Edit: `src/nova_navigator/dialogs/__init__.py`
- Create: `tests/dialogs/test_manage_remotes_dialog.py`

- [ ] **Step 1: Add export to `dialogs/__init__.py`**

Add `EditRemotesDialog` import and `__all__` entry:

```python
from .edit_remotes_dialog import EditRemotesDialog
```

And add `"EditRemotesDialog"` to `__all__`.

- [ ] **Step 2: Create `tests/dialogs/test_manage_remotes_dialog.py`**

```python
# tests/dialogs/test_manage_remotes_dialog.py
from __future__ import annotations

from pathlib import Path
from unittest.mock import patch

import pytest
from textual.app import App, ComposeResult
from textual.widgets import Button, Checkbox, Input, ListItem, ListView, Static

from nova_navigator.config.remotes import ProxySettings, RemoteConfig, RemoteConnection, SshSettings
from nova_navigator.dialogs.edit_remotes_dialog import EditRemotesDialog


def _fixture_config() -> RemoteConfig:
    cfg = object.__new__(RemoteConfig)
    cfg._items = [
        RemoteConnection(
            name="my-server",
            uri="ssh://alice@192.168.1.10",
            ssh=SshSettings(host="192.168.1.10", user="alice", port=None, identity_file=None),
        ),
        RemoteConnection(
            name="dev-box",
            uri="ssh://dev.example.com",
            ssh=SshSettings(host="dev.example.com"),
        ),
    ]
    return cfg


def _make_app(cfg: RemoteConfig) -> tuple[EditRemotesDialog, type[App[None]]]:
    dialog = EditRemotesDialog(cfg)

    class _App(App[None]):
        def compose(self) -> ComposeResult:
            return iter([])

        async def on_mount(self) -> None:
            await self.push_screen(dialog)

    _App.run_test = lambda self, **kw: App.run_test(self, size=kw.pop("size", (120, 50)), **kw)  # type: ignore[method-assign]

    return dialog, _App


@pytest.mark.asyncio
async def test_list_shows_configured_remotes() -> None:
    cfg = _fixture_config()
    dialog, _App = _make_app(cfg)
    app = _App()
    async with app.run_test() as pilot:
        await pilot.pause()
        lv = app.screen.query_one("#remote_list", ListView)
        items = list(lv.query(ListItem))
        assert len(items) == 2


@pytest.mark.asyncio
async def test_add_button_appends_and_selects() -> None:
    cfg = _fixture_config()
    dialog, _App = _make_app(cfg)
    app = _App()
    async with app.run_test() as pilot:
        await pilot.pause()
        await pilot.click(app.screen.query_one("#btn_add", Button))
        await pilot.pause()
        assert len(dialog._working) == 3
        assert dialog._working[-1].name == "new-connection"
        lv = app.screen.query_one("#remote_list", ListView)
        assert lv.index == 2


@pytest.mark.asyncio
async def test_remove_button_removes_selected() -> None:
    cfg = _fixture_config()
    dialog, _App = _make_app(cfg)
    app = _App()
    async with app.run_test() as pilot:
        await pilot.pause()
        lv = app.screen.query_one("#remote_list", ListView)
        lv.focus()
        await pilot.press("down")  # select first item
        await pilot.pause()
        await pilot.click(app.screen.query_one("#btn_remove", Button))
        await pilot.pause()
        assert len(dialog._working) == 1
        assert dialog._working[0].name == "dev-box"


@pytest.mark.asyncio
async def test_form_fields_populate_on_selection() -> None:
    cfg = _fixture_config()
    dialog, _App = _make_app(cfg)
    app = _App()
    async with app.run_test() as pilot:
        await pilot.pause()
        # First item is selected by default (index 0)
        await pilot.pause(delay=0.1)
        assert app.screen.query_one("#input_name", Input).value == "my-server"
        assert app.screen.query_one("#input_address", Input).value == "192.168.1.10"
        assert app.screen.query_one("#input_username", Input).value == "alice"


@pytest.mark.asyncio
async def test_uri_preview_updates_on_address_change() -> None:
    cfg = _fixture_config()
    dialog, _App = _make_app(cfg)
    app = _App()
    async with app.run_test() as pilot:
        await pilot.pause()
        await pilot.pause(delay=0.1)
        addr_input = app.screen.query_one("#input_address", Input)
        addr_input.value = "10.0.0.1"
        await pilot.pause()
        preview = app.screen.query_one("#uri_preview", Static)
        assert "10.0.0.1" in str(preview.renderable)


@pytest.mark.asyncio
async def test_proxy_fields_hidden_when_unchecked() -> None:
    cfg = _fixture_config()
    dialog, _App = _make_app(cfg)
    app = _App()
    async with app.run_test() as pilot:
        await pilot.pause()
        await pilot.pause(delay=0.1)
        # first entry has no proxy
        proxy_fields = app.screen.query_one("#proxy_fields")
        assert not proxy_fields.display


@pytest.mark.asyncio
async def test_proxy_fields_shown_when_checked() -> None:
    cfg = _fixture_config()
    dialog, _App = _make_app(cfg)
    app = _App()
    async with app.run_test() as pilot:
        await pilot.pause()
        await pilot.pause(delay=0.1)
        checkbox = app.screen.query_one("#check_proxy", Checkbox)
        checkbox.value = True
        await pilot.pause()
        proxy_fields = app.screen.query_one("#proxy_fields")
        assert proxy_fields.display


@pytest.mark.asyncio
async def test_ok_saves_changes(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    from nova_navigator.config import loader
    monkeypatch.setattr(loader, "_APP_CONFIG_DIR", tmp_path)

    cfg = _fixture_config()
    dialog, _App = _make_app(cfg)
    app = _App()
    async with app.run_test() as pilot:
        await pilot.pause()
        await pilot.pause(delay=0.1)
        name_input = app.screen.query_one("#input_name", Input)
        name_input.value = "renamed-server"
        await pilot.pause()
        with patch.object(cfg, "save"):
            await pilot.press("enter")
            await pilot.pause()
    assert cfg._items[0].name == "renamed-server"


@pytest.mark.asyncio
async def test_cancel_discards_changes() -> None:
    cfg = _fixture_config()
    original_name = cfg._items[0].name
    dialog, _App = _make_app(cfg)
    app = _App()
    async with app.run_test() as pilot:
        await pilot.pause()
        await pilot.pause(delay=0.1)
        name_input = app.screen.query_one("#input_name", Input)
        name_input.value = "should-not-persist"
        await pilot.pause()
        await pilot.press("escape")
        await pilot.pause()
    assert cfg._items[0].name == original_name
```

- [ ] **Step 3: Run tests**

```
uv run pytest tests/dialogs/test_manage_remotes_dialog.py -v
```
Expected: all PASS. If any test fails due to Textual async timing, add `await pilot.pause(delay=0.1)` before the failing assertion.

- [ ] **Step 4: Coding-guideline follow-up checklist**

- [ ] All test functions annotated `-> None`
- [ ] `uv run ruff check tests/dialogs/test_manage_remotes_dialog.py` — no errors

---

## Task 4: Final QA

- [ ] **Step 1: Run full QA**

```
uv run qa
```
Expected: all tests PASS, no new lint or type errors (pre-existing `settings.py` E501 and `copy_stat` ty errors are not caused by this work)

- [ ] **Step 2: Verify export is present**

```
uv run python -c "from nova_navigator.dialogs import EditRemotesDialog; print('OK')"
```
Expected: `OK`

- [ ] **Step 3: Verify `host` field round-trips through TOML**

```
uv run pytest tests/config/test_remotes.py::test_remote_connection_round_trip -v
```
Expected: PASS
