# Remote Connections — Implementation Spec

## Overview

This spec covers three concrete changes needed to support remote connections in Nova Navigator:

1. Consolidate the duplicate URI registry into the VFS layer.
2. Implement the `RemoteConfig` dataclass and serialization.
3. Add missing tests for both.

SSH filesystem support already exists (`vfs/filesystems/ssh.py`).
This spec does not cover GUI entry points or SSH connection UX — those are separate tasks.

---

## Part 1: URI consolidation

### Problem

There are two URI modules:

- `uri.py` — a `SchemeRegistry` mapping scheme strings to `VPath`-producing handler functions.
  Uses `urllib.parse.urlparse`, has camelCase method names (`registerScheme`), and has no tests.
- `vfs/parse_uri.py` — a proper nested-scheme URI parser, fully tested, part of the VFS layer.

These two modules serve different purposes but overlap: `uri.py` should use `parse_uri` for parsing but currently re-implements parsing independently via `urlparse`.

### Changes

**Delete `src/nova_navigator/uri.py`.**
Move its contents into a new file `src/nova_navigator/vfs/scheme_registry.py`.

**`src/nova_navigator/vfs/scheme_registry.py` contains:**

```python
import os
from collections.abc import Callable

from nova_navigator.vfs.parse_uri import parse_uri
from nova_navigator.vfs.vpath import VPath
from nova_navigator.vfs.filesystems import LocalFilesystem

SchemeHandler = Callable[[str, str | None], VPath]
# handler receives (path, netloc) from the parsed URI component
# NOTE: this changes the signature vs. the old uri.py (which only passed path).
# All handlers must be updated when uri.py is deleted.


class SchemeRegistry:
    _schemes: dict[str, SchemeHandler]

    def __init__(self) -> None:
        self._schemes = {}

    def register_scheme(self, scheme: str, handler: SchemeHandler) -> None:
        self._schemes[scheme] = handler

    def find(self, scheme: str) -> SchemeHandler | None:
        return self._schemes.get(scheme)


SCHEME_REGISTRY = SchemeRegistry()


def vfspath_from_uri(uri: str) -> VPath:
    result = parse_uri(uri)
    component = result.components[0]
    scheme = component.scheme or ""
    handler = SCHEME_REGISTRY.find(scheme)
    if not handler:
        raise ValueError(f"Unknown scheme: {scheme!r}")
    return handler(component.path, component.netloc)


def local_uri(path: str, netloc: str | None) -> VPath:
    path = os.path.expandvars(path)
    return LocalFilesystem.singleton().path(path)


def register_common_schemes() -> None:
    SCHEME_REGISTRY.register_scheme("file", local_uri)
    SCHEME_REGISTRY.register_scheme("", local_uri)
```

**Update imports:**
- `nova_navigator/nova_navigator.py`: `from nova_navigator.uri import vfspath_from_uri` → `from nova_navigator.vfs.scheme_registry import vfspath_from_uri`
- `nova_navigator/nova_navigator_core.py`: `from nova_navigator.uri import register_common_schemes, vfspath_from_uri` → `from nova_navigator.vfs.scheme_registry import register_common_schemes, vfspath_from_uri`

### Tests

New file: `tests/vfs/test_scheme_registry.py`

Tests:
- `test_register_and_find` — registered scheme is returned by `find`
- `test_find_unknown_returns_none` — unregistered scheme returns `None`
- `test_vfspath_from_uri_local` — `file:///tmp/foo` resolves via the local handler
- `test_vfspath_from_uri_unknown_scheme_raises` — `ftp://host/path` raises `ValueError`
- `test_vfspath_from_uri_no_scheme` — bare path `/tmp/foo` uses the `""` handler

---

## Part 2: Remote connection config model

### New file: `src/nova_navigator/config/remotes.py`

```python
"""RemoteConfig — saved remote connection entries."""

from __future__ import annotations

from dataclasses import dataclass
from typing import ClassVar

from nova_navigator.config.loader import ListConfig
from nova_navigator.config.model import BaseModel, key_field


@dataclass
class SshSettings(BaseModel):
    user: str | None = None
    port: int | None = None
    identity_file: str | None = None


@dataclass
class ProxySettings(BaseModel):
    type: str = "socks5"
    host: str = ""
    port: int = 1080


@dataclass
class RemoteConnection(BaseModel):
    name: str = key_field()
    uri: str = ""
    icon: str | None = None
    ssh: SshSettings | None = None
    proxy: ProxySettings | None = None


class RemoteConfig(ListConfig):
    """Saved remote connection config backed by remotes.toml."""

    CONFIG_NAME: ClassVar[str] = "remotes"
    _item_cls: ClassVar[type[BaseModel]] = RemoteConnection

    @classmethod
    def default_items(cls) -> list[BaseModel]:
        return []
```

### TOML format

Stored in `~/.config/nova-navigator/remotes.toml`.
Each top-level section is one `RemoteConnection`, keyed by its section name (the `name` key field).
Optional subtables use compact dotted-key form:

```toml
[my-server]
uri = "ssh://192.168.1.10"
ssh.user = "alice"
ssh.port = 2222
ssh.identity_file = "~/.ssh/id_ed25519"
proxy.type = "socks5"
proxy.host = "proxy.corp.com"
proxy.port = 1080

[azure-blob]
uri = "azure://mycontainer"
```

`SshSettings` and `ProxySettings` are omitted from the TOML when `None` (handled by `_populate_container` which skips `None` values).

### Tests

New file: `tests/config/test_remotes.py`

Tests:
- `test_remote_config_empty_defaults` — `RemoteConfig.load()` returns an empty list when no file exists
- `test_remote_config_writes_file_on_first_load` — `remotes.toml` is created on first load
- `test_remote_connection_round_trip` — a connection with all fields set survives save → load
- `test_remote_connection_optional_fields_none` — a connection with only `uri` set loads with `ssh=None`, `proxy=None`
- `test_remote_connection_key_field` — `name` is populated from the TOML section key

---

## Part 3: Update docs/remote-connections-design.md

Update the design doc to reflect the final model:
- Reference `RemoteConfig(ListConfig)` with `remotes.toml`.
- Show the compact dotted-key TOML example.
- Note that `uri.py` is replaced by `vfs/scheme_registry.py`.

*(The doc has already been partially updated in a prior session; verify it matches this spec.)*

---

## File changes summary

| Action | File |
|--------|------|
| Delete | `src/nova_navigator/uri.py` |
| Create | `src/nova_navigator/vfs/scheme_registry.py` |
| Edit | `src/nova_navigator/nova_navigator.py` (import path) |
| Edit | `src/nova_navigator/nova_navigator_core.py` (import path) |
| Create | `src/nova_navigator/config/remotes.py` |
| Edit | `docs/remote-connections-design.md` (verify/update) |
| Create | `tests/vfs/test_scheme_registry.py` |
| Create | `tests/config/test_remotes.py` |

---

## Out of scope

- GUI for managing remotes (Connect to Server… dialog, Settings → Edit Remote Connections…).
- SSH connection UX (auth prompts, host key verification).
- `SSHFilesystem` unit tests (require real SSH or complex mocking — separate task).
- Azure and other protocol implementations.
