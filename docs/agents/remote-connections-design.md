# Remote Connections — Design

## Goal

Allow users to navigate remote filesystems (SSH, and others in the future) from within Nova Navigator.
Remote connections are first-class navigable locations, not a separate mode.

## GUI entry points

### Go menu

A new **Go** top-level menu is added between Selection and View:

```
Go
  Go to Path…           Ctrl+G
  ─────────────────────────────
  Go Back
  Go Forward
  Go Up
  ─────────────────────────────
  Connect to Server…    Ctrl+Shift+G
  ─────────────────────────────
  [Device List]
```

- **Go to Path…** opens a path-entry bar where the user types any local path or remote URI (e.g. `ssh://user@host/path`).
- **Connect to Server…** opens a dialog to connect to a remote server by URI, with fields for protocol-specific options.
  Ad-hoc, one-time connections that do not require saving.
- The active panel navigates inline into the remote filesystem after connecting (consistent with all dual-pane managers).

### Remote connections sidebar / dialog

Saved remote connections are managed through a dedicated **Remotes** mechanism, separate from bookmarks.
They appear in the existing **Connect to Server…** dialog and are managed via **Settings → Edit Remote Connections…**.

## Remote connection model

Remote connections are their own first-class config concept, stored in `remotes.toml`.
Each connection has a name, a URI, and optional protocol-specific settings.

```python
@dataclass
class SshSettings(BaseModel):
    user: str | None = None
    port: int | None = None
    identity_file: str | None = None

@dataclass
class ProxySettings(BaseModel):
    type: str = "socks5"   # "socks5" | "http"
    host: str = ""
    port: int = 1080

@dataclass
class RemoteConnection(BaseModel):
    name: str = key_field()   # TOML section key, used as the connection identifier
    uri: str = ""             # full URI, e.g. "ssh://user@host:22/path"
    icon: str | None = None
    ssh: SshSettings | None = None
    proxy: ProxySettings | None = None
```

## Config loader

`RemoteConfig` uses `ListConfig` — the same open-list pattern used by other config modules.
Each top-level TOML section is one `RemoteConnection`, keyed by its section name.

```python
class RemoteConfig(ListConfig):
    CONFIG_NAME = "remotes"
    _item_cls = RemoteConnection

    @classmethod
    def default_items(cls) -> list[BaseModel]:
        return []
```

## TOML representation

Remote connections are stored in `~/.config/nova-navigator/remotes.toml`:

```toml
[my-server]
uri = "ssh://192.168.1.10"
name = "My Server"
icon = "server"

[my-server.ssh]
user = "alice"
port = 2222
identity_file = "~/.ssh/id_ed25519"

[my-server.proxy]
type = "socks5"
host = "proxy.corp.com"
port = 1080
```

## What is NOT done

- Remote connections are not stored in `bookmarks.toml` — they have their own `remotes.toml`.
- No per-connection tab model (WinSCP style) — the active panel navigates inline.
