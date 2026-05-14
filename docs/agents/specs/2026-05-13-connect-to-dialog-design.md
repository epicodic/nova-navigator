# Connect To Dialog — Design Spec

## Overview

Implement a "Connect To" feature that lets the user pick a saved remote connection and navigate the active panel to its home directory over SSH.

## Decisions

- **Connection lifecycle:** A new `SSHFilesystem` is created each time the user connects.
  There is no connection cache.
- **Initial path:** The panel navigates to `SSHFilesystem.home()` after connecting.
- **On failure:** A modal error dialog (`MessageDialog`) is shown; the panel stays on its current path.

---

## New Files

### `src/nova_navigator/dialogs/connect_to_dialog.py`

`ConnectToDialog(Dialog)` — a simple list-picker dialog.

**Constructor:** `ConnectToDialog(remotes: RemoteConfig)`

**`compose_content()`:**
- If `remotes` is empty: yields `Label("No remotes configured.")`.
  The OK button is disabled in this case.
- Otherwise: yields a `ListView` with one `ListItem` per `RemoteConnection`.
  Each item renders the connection icon (via `ico_()`) and name.

**Result:** `selected_connection: RemoteConnection | None` is set to the highlighted entry before `dismiss("OK")`.
It is `None` if the dialog is cancelled or no item is highlighted.

**Interaction:**
- Double-clicking a list item accepts immediately (same as OK).
- `Enter` triggers `action_accept_dialog` (inherited).
- `Escape` triggers `action_dismiss_dialog` (inherited).

**CSS:**
```css
ConnectToDialog {
    #dialog_box { width: 40; height: auto; }
}
```
`align: center middle` is inherited from `Dialog`.

---

### `src/nova_navigator/dialogs/message_dialog.py`

`MessageDialog(Dialog)` — reusable plain-text message / error dialog.

**Constructor:** `MessageDialog(message: str, title: str = "Error", button: DefaultButton = DefaultButton.OK)`

**`compose_content()`:** yields `Label(self._message)`.

Used by `_action_connect_to` to display SSH connection errors.

---

## Modified Files

### `src/nova_navigator/dialogs/__init__.py`

Add exports for `ConnectToDialog` and `MessageDialog`.

---

### `src/nova_navigator/nova_navigator.py`

**1. Uncomment the binding:**
```python
Binding("ctrl+shift+g", "connect_to", "Connect to Remote", show=False),
```

**2. Add imports:** `ConnectToDialog`, `MessageDialog`, `SSHFilesystem`, `asyncio`.

**3. Add handler** alongside other `_action_*` methods:

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

`SSHFilesystem.__init__` and `fs.home()` are blocking (paramiko + SFTP), so both run in `asyncio.to_thread`.

---

## Tests

### `tests/dialogs/test_connect_to_dialog.py`

| Test | What it checks |
|------|----------------|
| `test_empty_remotes` | No remotes → "No remotes configured" label shown, OK button disabled |
| `test_single_remote_selected_on_ok` | Single remote in list, click OK → `selected_connection` is set |
| `test_cancel_returns_none` | Press Escape → `selected_connection` is `None` |
| `test_double_click_accepts` | Hover + click list item twice → dismissed with selected connection |

### `tests/dialogs/test_message_dialog.py`

| Test | What it checks |
|------|----------------|
| `test_renders_message` | Dialog body contains the message text |
| `test_ok_dismisses` | Pressing Enter dismisses with result `"OK"` |

No integration tests for `_action_connect_to` — the SSH layer would require a mock `SSHFilesystem` which is out of scope; the handler logic is minimal.
