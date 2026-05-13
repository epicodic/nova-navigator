# Remote Connections Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use skills:subagent-driven-development (recommended) or skills:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Consolidate the duplicate URI registry, implement the `RemoteConfig` dataclass with TOML serialization, and add tests for both.

**Architecture:** Three sequential tasks: (1) delete `uri.py` and replace it with `vfs/scheme_registry.py` using the existing `parse_uri` parser; (2) add `config/remotes.py` with `RemoteConnection`, `SshSettings`, `ProxySettings`, and `RemoteConfig`; (3) add tests for both.
SSH filesystem code already exists and is out of scope.

**Tech Stack:** Python 3.12, pytest

**Coding Conventions:** `docs/coding_conventions.md` — read before implementing

**Spec:** `docs/agents/specs/2026-05-11-remote-connections-design.md`

---

## File Map

| Action | Path | Purpose |
|--------|------|---------|
| Delete | `src/nova_navigator/uri.py` | Replaced by scheme_registry.py |
| Create | `src/nova_navigator/vfs/scheme_registry.py` | `SchemeRegistry`, `SCHEME_REGISTRY`, `vfspath_from_uri`, `register_common_schemes` |
| Edit | `src/nova_navigator/nova_navigator_core.py` | Update import from `uri` → `vfs.scheme_registry` |
| Edit | `src/nova_navigator/nova_navigator.py` | Update import from `uri` → `vfs.scheme_registry` |
| Create | `src/nova_navigator/config/remotes.py` | `SshSettings`, `ProxySettings`, `RemoteConnection`, `RemoteConfig` |
| Create | `tests/vfs/test_scheme_registry.py` | Tests for `SchemeRegistry` and `vfspath_from_uri` |
| Create | `tests/config/test_remotes.py` | Tests for `RemoteConfig` round-trip |

---

## Task 1: Create `vfs/scheme_registry.py`

**Files:**
- Create: `src/nova_navigator/vfs/scheme_registry.py`

- [ ] **Step 1: Write the failing import test**

```python
# tests/vfs/test_scheme_registry.py
from __future__ import annotations


def test_import() -> None:
    from nova_navigator.vfs.scheme_registry import SchemeRegistry, SCHEME_REGISTRY, vfspath_from_uri, register_common_schemes  # noqa: F401
    assert True
```

- [ ] **Step 2: Run test to verify it fails**

```
uv run pytest tests/vfs/test_scheme_registry.py::test_import -v
```
Expected: FAIL — module does not exist yet

- [ ] **Step 3: Create `src/nova_navigator/vfs/scheme_registry.py`**

```python
"""SchemeRegistry — maps URI scheme strings to VPath-producing handler functions."""

from __future__ import annotations

import os
from collections.abc import Callable

from nova_navigator.vfs.filesystems import LocalFilesystem
from nova_navigator.vfs.parse_uri import parse_uri
from nova_navigator.vfs.vpath import VPath

SchemeHandler = Callable[[str, str | None], VPath]
"""Handler callable receiving ``(path, netloc)`` from the parsed URI component."""


class SchemeRegistry:
    """Registry mapping URI scheme strings to :data:`SchemeHandler` callables."""

    _schemes: dict[str, SchemeHandler]

    def __init__(self) -> None:
        self._schemes = {}

    def register_scheme(self, scheme: str, handler: SchemeHandler) -> None:
        """Register *handler* for *scheme*."""
        self._schemes[scheme] = handler

    def find(self, scheme: str) -> SchemeHandler | None:
        """Return the handler for *scheme*, or ``None`` if not registered."""
        return self._schemes.get(scheme)


SCHEME_REGISTRY: SchemeRegistry = SchemeRegistry()
"""Process-wide scheme registry."""


def vfspath_from_uri(uri: str) -> VPath:
    """Resolve *uri* to a :class:`~nova_navigator.vfs.VPath` using the registered handlers.

    Only the outermost URI component is resolved.

    Raises:
        ValueError: If the scheme has no registered handler.
    """
    result = parse_uri(uri)
    component = result.components[0]
    scheme = component.scheme or ""
    handler = SCHEME_REGISTRY.find(scheme)
    if not handler:
        raise ValueError(f"Unknown scheme: {scheme!r}")
    return handler(component.path, component.netloc)


def local_uri(path: str, netloc: str | None) -> VPath:
    """Resolve a local ``file://`` or bare-path URI to a :class:`~nova_navigator.vfs.VPath`."""
    path = os.path.expandvars(path)
    return LocalFilesystem.singleton().path(path)


def register_common_schemes() -> None:
    """Register built-in URI schemes: ``file`` and the empty (bare-path) scheme."""
    SCHEME_REGISTRY.register_scheme("file", local_uri)
    SCHEME_REGISTRY.register_scheme("", local_uri)
```

- [ ] **Step 4: Run test to verify it passes**

```
uv run pytest tests/vfs/test_scheme_registry.py::test_import -v
```
Expected: PASS

- [ ] **Step 5: Coding-guideline follow-up checklist**

- [ ] Conventions file read: `docs/coding_conventions.md`
- [ ] All functions have full type annotations
- [ ] Names are snake_case for functions/variables, UpperCamelCase for classes
- [ ] `uv run ruff check src/nova_navigator/vfs/scheme_registry.py` — no errors

---

## Task 2: Update imports and delete `uri.py`

**Files:**
- Edit: `src/nova_navigator/nova_navigator_core.py`
- Edit: `src/nova_navigator/nova_navigator.py`
- Delete: `src/nova_navigator/uri.py`

- [ ] **Step 1: Update `nova_navigator_core.py`**

Change line:
```python
from nova_navigator.uri import register_common_schemes, vfspath_from_uri  # noqa: F401 (re-exported)
```
to:
```python
from nova_navigator.vfs.scheme_registry import register_common_schemes, vfspath_from_uri  # noqa: F401 (re-exported)
```

- [ ] **Step 2: Update `nova_navigator.py`**

Change line:
```python
from nova_navigator.uri import vfspath_from_uri
```
to:
```python
from nova_navigator.vfs.scheme_registry import vfspath_from_uri
```

- [ ] **Step 3: Delete `uri.py`**

```
rm src/nova_navigator/uri.py
```

- [ ] **Step 4: Run all tests to verify nothing is broken**

```
uv run pytest -x -v
```
Expected: all tests PASS (no import errors)

- [ ] **Step 5: Coding-guideline follow-up checklist**

- [ ] `uv run ruff check .` — no errors
- [ ] `uv run ty check .` — no errors

---

## Task 3: Add `SchemeRegistry` tests

**Files:**
- Edit: `tests/vfs/test_scheme_registry.py`

- [ ] **Step 1: Replace the placeholder import test with the full test suite**

```python
from __future__ import annotations

import pytest

from nova_navigator.vfs.scheme_registry import SchemeRegistry, vfspath_from_uri, register_common_schemes, SCHEME_REGISTRY
from nova_navigator.vfs.vpath import VPath
from nova_navigator.vfs.filesystems import LocalFilesystem


def test_register_and_find() -> None:
    registry = SchemeRegistry()
    handler_called: list[tuple[str, str | None]] = []

    def _handler(path: str, netloc: str | None) -> VPath:
        handler_called.append((path, netloc))
        return LocalFilesystem.singleton().path(path)

    registry.register_scheme("myscheme", _handler)
    found = registry.find("myscheme")
    assert found is _handler


def test_find_unknown_returns_none() -> None:
    registry = SchemeRegistry()
    assert registry.find("notregistered") is None


def test_vfspath_from_uri_local(tmp_path: pytest.TempPathFactory) -> None:
    register_common_schemes()
    vpath = vfspath_from_uri("/tmp")
    assert str(vpath.path) == "/tmp"
    assert isinstance(vpath.filesystem, LocalFilesystem)


def test_vfspath_from_uri_file_scheme() -> None:
    register_common_schemes()
    vpath = vfspath_from_uri("file:///tmp")
    assert str(vpath.path) == "/tmp"
    assert isinstance(vpath.filesystem, LocalFilesystem)


def test_vfspath_from_uri_unknown_scheme_raises() -> None:
    registry = SchemeRegistry()
    with pytest.raises(ValueError, match="Unknown scheme"):
        # Use a fresh registry with no handlers, bypass SCHEME_REGISTRY global
        from nova_navigator.vfs import parse_uri as pu
        result = pu.parse_uri("ftp://host/path")
        component = result.components[0]
        scheme = component.scheme or ""
        handler = registry.find(scheme)
        if not handler:
            raise ValueError(f"Unknown scheme: {scheme!r}")


def test_vfspath_from_uri_no_scheme() -> None:
    register_common_schemes()
    vpath = vfspath_from_uri("/usr/local/bin")
    assert str(vpath.path) == "/usr/local/bin"
```

- [ ] **Step 2: Run tests**

```
uv run pytest tests/vfs/test_scheme_registry.py -v
```
Expected: all PASS

- [ ] **Step 3: Coding-guideline follow-up checklist**

- [ ] All test functions have full type annotations
- [ ] `uv run ruff check tests/vfs/test_scheme_registry.py` — no errors

---

## Task 4: Create `config/remotes.py`

**Files:**
- Create: `src/nova_navigator/config/remotes.py`

- [ ] **Step 1: Write the failing import test**

```python
# tests/config/test_remotes.py
from __future__ import annotations


def test_import() -> None:
    from nova_navigator.config.remotes import RemoteConfig, RemoteConnection, SshSettings, ProxySettings  # noqa: F401
    assert True
```

- [ ] **Step 2: Run test to verify it fails**

```
uv run pytest tests/config/test_remotes.py::test_import -v
```
Expected: FAIL — module does not exist yet

- [ ] **Step 3: Create `src/nova_navigator/config/remotes.py`**

```python
"""RemoteConfig — saved remote connection entries."""

from __future__ import annotations

from dataclasses import dataclass
from typing import ClassVar

from nova_navigator.config.loader import ListConfig
from nova_navigator.config.model import BaseModel, key_field


@dataclass
class SshSettings(BaseModel):
    """SSH protocol settings for a remote connection."""

    user: str | None = None
    port: int | None = None
    identity_file: str | None = None


@dataclass
class ProxySettings(BaseModel):
    """Proxy settings for a remote connection."""

    type: str = "socks5"
    host: str = ""
    port: int = 1080


@dataclass
class RemoteConnection(BaseModel):
    """A single saved remote connection entry."""

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

- [ ] **Step 4: Run import test to verify it passes**

```
uv run pytest tests/config/test_remotes.py::test_import -v
```
Expected: PASS

- [ ] **Step 5: Coding-guideline follow-up checklist**

- [ ] All fields annotated; `X | None` used (not `Optional[X]`)
- [ ] `uv run ruff check src/nova_navigator/config/remotes.py` — no errors
- [ ] `uv run ty check .` — no errors

---

## Task 5: Add `RemoteConfig` tests

**Files:**
- Edit: `tests/config/test_remotes.py`

- [ ] **Step 1: Replace the placeholder import test with the full test suite**

```python
from __future__ import annotations

from pathlib import Path

import pytest


def test_import() -> None:
    from nova_navigator.config.remotes import RemoteConfig, RemoteConnection, SshSettings, ProxySettings  # noqa: F401
    assert True


def test_remote_config_empty_defaults(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    from nova_navigator.config import loader
    monkeypatch.setattr(loader, "_APP_CONFIG_DIR", tmp_path)

    from nova_navigator.config.remotes import RemoteConfig

    cfg = RemoteConfig.load()
    assert cfg.items() == []


def test_remote_config_writes_file_on_first_load(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    from nova_navigator.config import loader
    monkeypatch.setattr(loader, "_APP_CONFIG_DIR", tmp_path)

    from nova_navigator.config.remotes import RemoteConfig

    RemoteConfig.load()
    assert (tmp_path / "remotes.toml").exists()


def test_remote_connection_round_trip(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    from nova_navigator.config import loader
    monkeypatch.setattr(loader, "_APP_CONFIG_DIR", tmp_path)

    from nova_navigator.config.remotes import ProxySettings, RemoteConfig, RemoteConnection, SshSettings

    conn = RemoteConnection(
        name="my-server",
        uri="ssh://192.168.1.10",
        icon="server",
        ssh=SshSettings(user="alice", port=2222, identity_file="~/.ssh/id_ed25519"),
        proxy=ProxySettings(type="socks5", host="proxy.corp.com", port=1080),
    )
    cfg = RemoteConfig.load()
    cfg._items = [conn]
    cfg.save()

    cfg2 = RemoteConfig.load()
    items = cfg2.items()
    assert len(items) == 1
    loaded = items[0]
    assert isinstance(loaded, RemoteConnection)
    assert loaded.uri == "ssh://192.168.1.10"
    assert loaded.icon == "server"
    assert loaded.ssh is not None
    assert loaded.ssh.user == "alice"
    assert loaded.ssh.port == 2222
    assert loaded.ssh.identity_file == "~/.ssh/id_ed25519"
    assert loaded.proxy is not None
    assert loaded.proxy.type == "socks5"
    assert loaded.proxy.host == "proxy.corp.com"
    assert loaded.proxy.port == 1080


def test_remote_connection_optional_fields_none(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    from nova_navigator.config import loader
    monkeypatch.setattr(loader, "_APP_CONFIG_DIR", tmp_path)

    from nova_navigator.config.remotes import RemoteConfig, RemoteConnection

    conn = RemoteConnection(name="bare", uri="ssh://somehost")
    cfg = RemoteConfig.load()
    cfg._items = [conn]
    cfg.save()

    cfg2 = RemoteConfig.load()
    items = cfg2.items()
    assert len(items) == 1
    loaded = items[0]
    assert isinstance(loaded, RemoteConnection)
    assert loaded.ssh is None
    assert loaded.proxy is None


def test_remote_connection_key_field(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    from nova_navigator.config import loader
    monkeypatch.setattr(loader, "_APP_CONFIG_DIR", tmp_path)

    from nova_navigator.config.remotes import RemoteConfig, RemoteConnection

    conn = RemoteConnection(name="my-box", uri="ssh://10.0.0.1")
    cfg = RemoteConfig.load()
    cfg._items = [conn]
    cfg.save()

    cfg2 = RemoteConfig.load()
    loaded = cfg2.items()[0]
    assert isinstance(loaded, RemoteConnection)
    assert loaded.name == "my-box"
```

- [ ] **Step 2: Check what `items()` method looks like on `ListConfig` and adjust if needed**

Look at `src/nova_navigator/config/loader.py` — `ListConfig` exposes `_items`.
If there is no `items()` method, use `cfg._items` directly in the tests (replace all `cfg.items()` calls with `cfg._items`).

- [ ] **Step 3: Run tests**

```
uv run pytest tests/config/test_remotes.py -v
```
Expected: all PASS

- [ ] **Step 4: Coding-guideline follow-up checklist**

- [ ] All test functions have full type annotations
- [ ] `uv run ruff check tests/config/test_remotes.py` — no errors

---

## Task 6: Final QA

- [ ] **Step 1: Run full QA**

```
uv run qa
```
Expected: zero lint errors, zero type errors, all tests PASS

- [ ] **Step 2: Verify `uri.py` is gone and nothing imports it**

```
grep -r "from nova_navigator.uri" src/ tests/
```
Expected: no output

- [ ] **Step 3: Verify `remotes.toml` format manually**

```python
# Run in a Python REPL or quick script:
import tempfile, pathlib, sys
sys.path.insert(0, "src")
import nova_navigator.config.loader as loader
with tempfile.TemporaryDirectory() as d:
    loader._APP_CONFIG_DIR = pathlib.Path(d)
    from nova_navigator.config.remotes import RemoteConfig, RemoteConnection, SshSettings
    conn = RemoteConnection(name="test", uri="ssh://host", ssh=SshSettings(user="bob", port=22))
    cfg = RemoteConfig.load()
    cfg._items = [conn]
    cfg.save()
    print(pathlib.Path(d, "remotes.toml").read_text())
```
Expected output resembles:
```toml
[test]
uri = "ssh://host"

[test.ssh]
user = "bob"
port = 22
```
