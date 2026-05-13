# Manage Remote Connections Dialog — Design

## Goal

Provide a full-screen modal dialog for creating, editing, and deleting saved remote connections.
The dialog edits `RemoteConfig` (backed by `remotes.toml`) using the same deep-copy + save-on-OK pattern as `EditBookmarksDialog`.

---

## Layout

```
┌─────────────────────────────────────────────────────────┐
│ Edit Remote Connections                                  │
│                                                         │
│  ┌─────────────────────────────────┐  ┌──────────────┐  │
│  │ my-server  ssh://alice@…        │  │ Add          │  │
│  │ azure-blob azure://mycontainer  │  │ Remove       │  │
│  │                                 │  └──────────────┘  │
│  └─────────────────────────────────┘                    │
│  ──────────────────────────────────────────────────     │
│  Name: [________________]  Icon: [______] […]           │
│  URI:  ssh://alice@192.168.1.10:22  (read-only)         │
│  Type: [ SSH ▾ ]                                        │
│                                                         │
│  ── SSH ────────────────────────────────────────────    │
│  Address: [_______________]  Port: [____]               │
│  Username: [_____________]                              │
│  Identity File: [_________________________________]     │
│                                                         │
│  ── Proxy ──────────────────────────────────────────    │
│  [x] Enable proxy                                       │
│  Host: [______________]  Port: [____]                   │
│  Type: [ SOCKS5 ▾ ]                                     │
│                                                         │
│                          [ OK ]  [ Cancel ]             │
└─────────────────────────────────────────────────────────┘
```

**Top panel:** `ListView` showing all remotes (name + URI summary), with Add / Remove buttons in a column to the right.

**Bottom panel:** Form with fields for the selected entry.
All form fields are disabled when no entry is selected.

The SSH section (`#ssh_section`) and proxy detail fields (`#proxy_fields`) are always in the DOM, shown or hidden via `display`.

---

## Widgets

| Widget | ID | Purpose |
|---|---|---|
| `ListView` | `#remote_list` | One `ListItem` per `RemoteConnection` |
| `Button` | `#btn_add` | Append empty `RemoteConnection`, select it |
| `Button` | `#btn_remove` | Remove selected entry; disabled when nothing selected |
| `Input` | `#input_name` | `RemoteConnection.name` (also the TOML key) |
| `Input` | `#input_icon` | `RemoteConnection.icon` |
| `Button` | `#btn_pick_icon` | Opens `IconPickerDialog` |
| `Static` | `#uri_preview` | Read-only assembled URI, updates live |
| `Select` | `#select_type` | Protocol: `[("SSH", "ssh")]`; extend for future protocols |
| `Vertical` | `#ssh_section` | Container shown only when type == "ssh" |
| `Input` | `#input_address` | `SshSettings.host` — SSH hostname or IP |
| `Input` | `#input_port` | `SshSettings.port` (numeric, placeholder "22") |
| `Input` | `#input_username` | `SshSettings.user` |
| `Input` | `#input_identity_file` | `SshSettings.identity_file` (plain text, no browse) |
| `Checkbox` | `#check_proxy` | `proxy is not None` — enables/disables proxy detail fields |
| `Vertical` | `#proxy_fields` | Container for proxy inputs; disabled when unchecked |
| `Input` | `#input_proxy_host` | `ProxySettings.host` |
| `Input` | `#input_proxy_port` | `ProxySettings.port` |
| `Select` | `#select_proxy_type` | `[("SOCKS5", "socks5"), ("HTTP", "http")]` |

---

## URI preview assembly

The `#uri_preview` static is recomputed whenever any of the following change:
`#select_type`, `#input_username`, `#input_address`, `#input_port`.

Assembly logic for SSH:
```python
def _build_uri_preview(self) -> str:
    proto = self._selected_protocol()          # "ssh"
    address = self.query_one("#input_address", Input).value.strip()
    port = self.query_one("#input_port", Input).value.strip()
    username = self.query_one("#input_username", Input).value.strip()

    if not address:
        return ""
    netloc = f"{username}@{address}" if username else address
    if port and port != "22":
        netloc = f"{netloc}:{port}"
    return f"{proto}://{netloc}"
```

The `Static` content is set to this string.
When there is no selection or address is empty, it shows an empty string.

---

## State management

`_working: list[RemoteConnection]` — deep copy of `RemoteConfig._items` at open time.
`_current_index: int | None` — index into `_working` of the selected entry.
`_syncing: bool` — flag to suppress `Input.Changed` handlers while the form is being programmatically populated.

On **Add:** append `RemoteConnection(name="new-connection")` to `_working`, rebuild list, select new entry.

On **Remove:** remove `_working[_current_index]`, rebuild list, select the previous entry (or none if list is empty).

On **OK:** `config._items = self._working; config.save()`.

On **Cancel / Escape:** dismiss with no changes.

---

## Form ↔ model sync

**List selection → form (read):**
Called by `_on_list_item_highlighted`. Sets `_syncing = True`, populates all input fields from `_working[index]`, sets SSH section visibility, sets proxy checkbox and proxy fields visibility, sets `#uri_preview`. Then `_syncing = False`.

**Form input → model (write):**
Each `on_input_changed` / `on_select_changed` / `on_checkbox_changed` handler:
- returns early if `_syncing`
- writes the changed value back to `_working[_current_index]`
- updates `#uri_preview` if a URI-relevant field changed

**SSH section visibility:**
```python
self.query_one("#ssh_section").display = (selected_protocol == "ssh")
```

**Proxy fields visibility:**
```python
proxy_enabled = self.query_one("#check_proxy", Checkbox).value
self.query_one("#proxy_fields").display = proxy_enabled
```

When proxy is enabled and was previously `None`, initialise `ProxySettings()` with defaults.
When proxy is disabled, set `proxy = None` on the working entry.

---

## Data model notes

`RemoteConnection.name` is a `key_field()` — it's the TOML section key.
The `#input_name` field writes to `working_entry.name` on change.
The `#remote_list` item label is rebuilt from `name` + `uri` on any name or URI-field change.

`SshSettings` requires a `host` field (not currently in `remotes.py`).
The `remotes.py` model must be updated before implementing the dialog:

```python
@dataclass
class SshSettings(BaseModel):
    host: str = ""
    user: str | None = None
    port: int | None = None
    identity_file: str | None = None
```

Address, port, and username are stored in `SshSettings`.
The URI (`RemoteConnection.uri`) is assembled live on every relevant field change and written immediately to `working_entry.uri`:

```python
def _assemble_and_store_uri(self) -> None:
    if self._current_index is None:
        return
    entry = self._working[self._current_index]
    entry.uri = self._build_uri_preview()
    self._rebuild_list_item(self._current_index)
```

---

## File structure

| Action | Path |
|--------|------|
| Create | `src/nova_navigator/dialogs/edit_remotes_dialog.py` |
| Edit | `src/nova_navigator/dialogs/__init__.py` (add export) |
| Create | `tests/dialogs/test_manage_remotes_dialog.py` |

---

## Tests

File: `tests/dialogs/test_manage_remotes_dialog.py`

Wrap the dialog in a minimal `App` that pushes it as a screen (same pattern as `test_manage_bookmarks_dialog.py`).

| Test | What it verifies |
|---|---|
| `test_list_shows_configured_remotes` | `ListView` has one item per `RemoteConfig` entry |
| `test_add_button_appends_and_selects` | Add creates new entry, list grows by 1, form is populated |
| `test_remove_button_removes_selected` | Remove reduces list by 1 |
| `test_form_fields_populate_on_selection` | Selecting an entry fills Name, Address, Port, Username |
| `test_uri_preview_updates_on_address_change` | Typing in Address updates `#uri_preview` |
| `test_proxy_fields_hidden_when_unchecked` | `#proxy_fields` display is False when checkbox is unchecked |
| `test_proxy_fields_shown_when_checked` | Checking proxy shows `#proxy_fields` |
| `test_ok_saves_changes` | After editing Name and clicking OK, `config._items[0].name` reflects the edit |
| `test_cancel_discards_changes` | Editing Name then Cancel leaves original config unchanged |

---

## Out of scope

- Browse button for Identity File (deferred to a dedicated file-picker task).
- Connecting to the remote from within the dialog.
- Azure or other protocol-specific fields (only SSH section implemented now).
- Reorder (move up/down) — remotes are not ordered by use-frequency; arbitrary order is fine.
