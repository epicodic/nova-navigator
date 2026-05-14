# Config System Redesign Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use skills:subagent-driven-development (recommended) or skills:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Replace `config.py` and `toml_config.py` with a new `nova_navigator/config/` subpackage that uses dataclasses for schema declaration, provides generic TOML serialisation/deserialisation, and auto-creates config files from code-defined defaults on first run.

**Architecture:** `model.py` provides `ConfigModel` base class, `computed()`/`key_field()` field helpers, and generic `from_toml`/`to_toml` functions. `loader.py` provides `ModelConfig` and `ListConfig` which handle the file lifecycle (locate → read or create+write). Concrete config classes (`FileTypeConfig`, `BookmarkConfig`, `Settings`) live in separate files under `nova_navigator/config/`.

**Spec:** `docs/agents/specs/2026-04-27-config-system-design.md`

**Tech Stack:** Python 3.12, pytest, tomlkit

**Coding Conventions:** `docs/coding_conventions.md` — read before implementing

---

## File Map

| Action | Path | Responsibility |
|--------|------|----------------|
| Create | `src/nova_navigator/config/__init__.py` | Re-exports public API |
| Create | `src/nova_navigator/config/model.py` | `ConfigModel`, helpers, `from_toml`, `to_toml` |
| Create | `src/nova_navigator/config/loader.py` | `ConfigBase`, `ModelConfig`, `ListConfig`, path helpers |
| Create | `src/nova_navigator/config/filetypes.py` | `Section`, `FileTypeConfig` |
| Create | `src/nova_navigator/config/bookmarks.py` | `Bookmark`, `Group`, `BookmarkConfig` |
| Create | `src/nova_navigator/config/settings.py` | `GeneralSettings`, `NetworkSettings`, `Settings` |
| Create | `src/nova_navigator/config/global_config.py` | `GlobalConfig`, `conf_` |
| Create | `tests/config/__init__.py` | Test package marker |
| Create | `tests/config/test_model.py` | Unit tests for model.py |
| Create | `tests/config/test_loader.py` | Unit tests for loader.py |
| Create | `tests/config/test_filetypes.py` | Integration tests for FileTypeConfig |
| Create | `tests/config/test_bookmarks.py` | Integration tests for BookmarkConfig |
| Create | `tests/config/test_settings.py` | Integration tests for Settings |
| Modify | `src/nova_navigator/main.py` | Update import path |
| Modify | `src/nova_navigator/widgets/directory_browser.py` | Update import path |
| Modify | `src/nova_navigator/dialogs/bookmarks_dialog.py` | Update import path |
| Modify | `tests/widgets/conftest.py` | Update import path |
| Delete | `src/nova_navigator/config.py` | Replaced by package |
| Delete | `src/nova_navigator/toml_config.py` | Replaced by model.py |

---

## Task 1: `model.py` — Core schema framework

**Files:**
- Create: `src/nova_navigator/config/model.py`
- Test: `tests/config/test_model.py`

- [ ] **Step 1: Create `tests/config/__init__.py`**

```python
```
(Empty file.)

- [ ] **Step 2: Write failing tests for `ConfigModel`, `computed`, `key_field`**

Create `tests/config/test_model.py`:

```python
import dataclasses
from dataclasses import dataclass, field

import pytest
import tomlkit

from nova_navigator.config.model import (
    ConfigLoadError,
    ConfigModel,
    computed,
    from_toml,
    key_field,
    list_from_toml,
    list_to_toml,
    to_toml,
    update_toml_doc,
)


# ── Fixtures ──────────────────────────────────────────────────────────────────


@dataclass
class Flat(ConfigModel):
    """A flat config with simple fields."""
    name: str = "default"
    count: int = 0
    active: bool = False
    label: str | None = None


@dataclass
class WithComputed(ConfigModel):
    value: str = "hello"
    upper: str = computed(lambda s: s.value.upper())


@dataclass
class WithKey(ConfigModel):
    section_name: str = key_field()
    mimetype: str | None = None
    icon: str | None = None


@dataclass
class Nested(ConfigModel):
    """Outer config."""
    inner: Flat = field(default_factory=Flat)
    title: str = "outer"


@dataclass
class WithList(ConfigModel):
    items: list[Flat] = field(default_factory=list)


# ── ConfigModel + computed ────────────────────────────────────────────────────


def test_computed_field_runs_after_init() -> None:
    obj = WithComputed(value="world")
    assert obj.upper == "WORLD"


def test_computed_field_uses_default() -> None:
    obj = WithComputed()
    assert obj.upper == "HELLO"


def test_computed_field_not_in_dataclass_init() -> None:
    # upper has init=False so passing it to the constructor raises TypeError
    with pytest.raises(TypeError):
        WithComputed(value="x", upper="ignored")  # type: ignore[call-arg]


def test_key_field_default_is_empty_string() -> None:
    obj = WithKey()
    assert obj.section_name == ""


# ── from_toml: scalars ────────────────────────────────────────────────────────


def test_from_toml_reads_scalar_fields() -> None:
    t = tomlkit.loads('name = "custom"\ncount = 42\nactive = true')
    obj = from_toml(Flat, t)
    assert obj.name == "custom"
    assert obj.count == 42
    assert obj.active is True


def test_from_toml_uses_dataclass_defaults_for_missing_fields() -> None:
    t = tomlkit.loads("")
    obj = from_toml(Flat, t)
    assert obj.name == "default"
    assert obj.count == 0


def test_from_toml_optional_field_absent_is_none() -> None:
    t = tomlkit.loads('name = "x"')
    obj = from_toml(Flat, t)
    assert obj.label is None


def test_from_toml_optional_field_present_is_read() -> None:
    t = tomlkit.loads('label = "hello"')
    obj = from_toml(Flat, t)
    assert obj.label == "hello"


def test_from_toml_missing_required_field_raises_config_load_error() -> None:
    @dataclass
    class Required(ConfigModel):
        must_have: int

    t = tomlkit.loads("")
    with pytest.raises(ConfigLoadError) as exc_info:
        from_toml(Required, t)
    assert "must_have" in str(exc_info.value)


def test_from_toml_unknown_field_in_toml_is_ignored() -> None:
    t = tomlkit.loads('name = "x"\nunknown_key = 99')
    obj = from_toml(Flat, t)  # must not raise
    assert obj.name == "x"


# ── from_toml: nested ConfigModel ─────────────────────────────────────────────


def test_from_toml_nested_config_model() -> None:
    t = tomlkit.loads('[inner]\nname = "inner_val"\ncount = 7')
    obj = from_toml(Nested, t)
    assert obj.inner.name == "inner_val"
    assert obj.inner.count == 7
    assert obj.title == "outer"


# ── from_toml: list[ConfigModel] ──────────────────────────────────────────────


def test_from_toml_list_of_config_model() -> None:
    t = tomlkit.loads("[[items]]\nname = 'a'\n\n[[items]]\nname = 'b'")
    obj = from_toml(WithList, t)
    assert len(obj.items) == 2
    assert obj.items[0].name == "a"
    assert obj.items[1].name == "b"


# ── from_toml: key_field ──────────────────────────────────────────────────────


def test_from_toml_key_field_set_from_key_argument() -> None:
    t = tomlkit.loads('icon = "video"')
    obj = from_toml(WithKey, t, key="videos")
    assert obj.section_name == "videos"
    assert obj.icon == "video"


# ── to_toml ───────────────────────────────────────────────────────────────────


def test_to_toml_produces_scalar_fields() -> None:
    obj = Flat(name="test", count=5, active=True)
    doc = to_toml(obj)
    assert doc["name"] == "test"
    assert doc["count"] == 5
    assert doc["active"] is True


def test_to_toml_skips_computed_fields() -> None:
    obj = WithComputed(value="abc")
    doc = to_toml(obj)
    assert "upper" not in doc
    assert "value" in doc


def test_to_toml_skips_key_fields() -> None:
    obj = WithKey(section_name="videos", icon="video")
    doc = to_toml(obj)
    assert "section_name" not in doc
    assert "icon" in doc


def test_to_toml_skips_none_optional_fields() -> None:
    obj = Flat(label=None)
    doc = to_toml(obj)
    assert "label" not in doc


def test_to_toml_includes_docstring_as_comment() -> None:
    obj = Flat()
    serialised = tomlkit.dumps(to_toml(obj))
    assert "# A flat config with simple fields." in serialised


def test_to_toml_nested_config_model_produces_subtable() -> None:
    obj = Nested(title="top")
    doc = to_toml(obj)
    assert "inner" in doc
    assert doc["inner"]["name"] == "default"


def test_to_toml_list_of_config_model_produces_aot() -> None:
    obj = WithList(items=[Flat(name="a"), Flat(name="b")])
    doc = to_toml(obj)
    assert "items" in doc
    serialised = tomlkit.dumps(doc)
    assert "[[items]]" in serialised


# ── round-trip ────────────────────────────────────────────────────────────────


def test_round_trip_scalar_fields() -> None:
    original = Flat(name="round", count=99, active=True, label="yes")
    doc = to_toml(original)
    restored = from_toml(Flat, doc)
    assert restored.name == "round"
    assert restored.count == 99
    assert restored.active is True
    assert restored.label == "yes"


def test_round_trip_nested() -> None:
    original = Nested(inner=Flat(name="deep"), title="up")
    doc = to_toml(original)
    restored = from_toml(Nested, doc)
    assert restored.inner.name == "deep"
    assert restored.title == "up"


# ── list_from_toml / list_to_toml ─────────────────────────────────────────────


def test_list_from_toml_creates_instances_with_key() -> None:
    doc = tomlkit.loads('[videos]\nicon = "video"\n\n[default]\nicon = "file"')
    items = list_from_toml(WithKey, doc)
    assert len(items) == 2
    assert items[0].section_name == "videos"
    assert items[0].icon == "video"
    assert items[1].section_name == "default"


def test_list_to_toml_uses_key_field_as_section_heading() -> None:
    items = [
        WithKey(section_name="videos", icon="video"),
        WithKey(section_name="default", icon="file"),
    ]
    doc = list_to_toml(items)
    serialised = tomlkit.dumps(doc)
    assert "[videos]" in serialised
    assert "[default]" in serialised
    assert "video" in serialised


def test_list_round_trip() -> None:
    original = [
        WithKey(section_name="videos", icon="video"),
        WithKey(section_name="default"),
    ]
    doc = list_to_toml(original)
    restored = list_from_toml(WithKey, doc)
    assert len(restored) == 2
    assert restored[0].section_name == "videos"
    assert restored[0].icon == "video"
    assert restored[1].section_name == "default"
    assert restored[1].icon is None


# ── update_toml_doc ────────────────────────────────────────────────────────────


def test_update_toml_doc_preserves_comments() -> None:
    original_toml = "# my comment\nname = 'old'\ncount = 0\n"
    doc = tomlkit.loads(original_toml)
    obj = Flat(name="new", count=5)
    update_toml_doc(doc, obj)
    serialised = tomlkit.dumps(doc)
    assert "# my comment" in serialised
    assert "new" in serialised
    assert "5" in serialised


def test_update_toml_doc_updates_nested_scalars() -> None:
    original_toml = "[inner]\nname = 'old'\ncount = 0\ntitle = 'outer'\n"
    doc = tomlkit.loads(original_toml)
    obj = Nested(inner=Flat(name="new"), title="updated")
    update_toml_doc(doc, obj)
    assert doc["inner"]["name"] == "new"
    assert doc["title"] == "updated"
```

- [ ] **Step 3: Run tests to confirm they fail**

```
uv run pytest tests/config/test_model.py -v
```
Expected: ImportError — `nova_navigator.config.model` does not exist yet.

- [ ] **Step 4: Create `src/nova_navigator/config/__init__.py`** (empty for now)

```python
```
(Empty file — fills in Task 6.)

- [ ] **Step 5: Implement `src/nova_navigator/config/model.py`**

```python
from __future__ import annotations

import dataclasses
import inspect
import types
from dataclasses import field
from typing import Any, Callable, TypeVar, get_args, get_origin, get_type_hints

import tomlkit
import tomlkit.items
from tomlkit import TOMLDocument
from tomlkit.items import Table as TOMLTable

__all__ = [
    "ConfigLoadError",
    "ConfigModel",
    "computed",
    "from_toml",
    "key_field",
    "list_from_toml",
    "list_to_toml",
    "to_toml",
    "update_toml_doc",
]

_T = TypeVar("_T", bound="ConfigModel")


class ConfigLoadError(Exception):
    """Raised when a required field is missing from a TOML config file."""

    def __init__(self, field_name: str, file_path: str = "") -> None:
        self.field_name = field_name
        self.file_path = file_path
        location = f" in {file_path}" if file_path else ""
        super().__init__(f"Required field '{field_name}' missing from config{location}")


class ConfigModel:
    """Base class for all config schema classes.

    Subclasses must be decorated with @dataclass.
    Computed fields (declared with computed()) are initialised after all regular
    fields, in declaration order.

    Note: only the immediate class's __annotations__ are processed by @dataclass.
    Two-level ConfigModel inheritance is not supported.
    """

    def __post_init__(self) -> None:
        for f in dataclasses.fields(self):  # type: ignore[arg-type]
            fn = f.metadata.get("self_factory")
            if fn is not None:
                object.__setattr__(self, f.name, fn(self))


def computed(factory: Callable[[Any], Any]) -> Any:
    """Declare a field computed from other fields after construction.

    Not read from TOML. Not written to TOML.
    The factory receives the partially-constructed instance and runs after all
    regular fields are set.
    """
    return field(init=False, default=None, metadata={"self_factory": factory})


def key_field() -> Any:
    """Mark the field that holds the TOML section name.

    Its value becomes the [section_heading] in the serialised output.
    Not written as a key-value pair.
    """
    return field(default="", metadata={"toml_key": True})


# ─── Type inspection helpers ──────────────────────────────────────────────────


def _is_optional(t: Any) -> bool:
    return get_origin(t) is types.UnionType and type(None) in get_args(t)


def _unwrap_optional(t: Any) -> Any:
    if not _is_optional(t):
        return t
    return next(a for a in get_args(t) if a is not type(None))


def _is_config_model_type(t: Any) -> bool:
    return isinstance(t, type) and issubclass(t, ConfigModel)


def _is_list_of_config_model(t: Any) -> bool:
    return get_origin(t) is list and bool(get_args(t)) and _is_config_model_type(get_args(t)[0])


def _coerce(value: Any, t: Any) -> Any:
    if t is bool:
        return bool(value)
    if t is int:
        return int(value)
    if t is str:
        return str(value)
    if t is float:
        return float(value)
    return value


# ─── from_toml ────────────────────────────────────────────────────────────────


def from_toml(
    cls: type[_T],
    table: TOMLTable | TOMLDocument,
    *,
    key: str = "",
    file_path: str = "",
) -> _T:
    """Deserialise a ConfigModel subclass from a TOML table.

    Args:
        cls: The ConfigModel subclass to instantiate.
        table: The TOML table to read from.
        key: Value for the key_field (section heading), if the class has one.
        file_path: Path shown in error messages for missing required fields.
    """
    hints = get_type_hints(cls)
    kwargs: dict[str, Any] = {}

    for f in dataclasses.fields(cls):  # type: ignore[arg-type]
        if not f.init:
            continue  # computed field; __post_init__ handles it

        field_name = f.name
        field_type = hints[field_name]

        if f.metadata.get("toml_key"):
            kwargs[field_name] = key
            continue

        actual_type = _unwrap_optional(field_type)
        is_optional = _is_optional(field_type)

        if field_name in table:
            raw = table[field_name]
            if _is_list_of_config_model(actual_type):
                elem_type = get_args(actual_type)[0]
                kwargs[field_name] = [
                    from_toml(elem_type, item, file_path=file_path) for item in raw
                ]
            elif _is_config_model_type(actual_type):
                kwargs[field_name] = from_toml(actual_type, raw, file_path=file_path)
            else:
                kwargs[field_name] = _coerce(raw, actual_type)
        else:
            if is_optional:
                kwargs[field_name] = None
            elif (
                f.default is not dataclasses.MISSING
                or f.default_factory is not dataclasses.MISSING  # type: ignore[misc]
            ):
                pass  # let dataclass constructor use its default
            else:
                raise ConfigLoadError(field_name, file_path)

    return cls(**kwargs)


# ─── to_toml ──────────────────────────────────────────────────────────────────


def to_toml(obj: ConfigModel) -> TOMLDocument:
    """Serialise a ConfigModel instance to a tomlkit TOMLDocument."""
    doc = tomlkit.document()
    _fill_table(doc, obj)
    return doc


def _fill_table(container: Any, obj: ConfigModel) -> None:
    cls = type(obj)
    hints = get_type_hints(cls)

    if cls.__doc__:
        for line in inspect.cleandoc(cls.__doc__).splitlines():
            container.add(tomlkit.comment(line))

    for f in dataclasses.fields(obj):  # type: ignore[arg-type]
        if "self_factory" in f.metadata or "toml_key" in f.metadata:
            continue

        field_name = f.name
        field_type = hints[field_name]
        value = getattr(obj, field_name)
        actual_type = _unwrap_optional(field_type)

        if value is None:
            continue

        if _is_list_of_config_model(actual_type):
            aot = tomlkit.aot()
            for item in value:
                item_table = tomlkit.table()
                _fill_table(item_table, item)
                aot.append(item_table)
            container.add(field_name, aot)
        elif _is_config_model_type(actual_type):
            sub = tomlkit.table()
            _fill_table(sub, value)
            container.add(field_name, sub)
        else:
            container.add(field_name, value)


# ─── list_from_toml / list_to_toml ───────────────────────────────────────────


def list_from_toml(
    cls: type[_T],
    doc: TOMLDocument,
    *,
    file_path: str = "",
) -> list[_T]:
    """Deserialise a list of ConfigModel instances from a top-level-keyed TOML document.

    Each top-level key becomes the key_field value of the corresponding instance.
    """
    return [
        from_toml(cls, section_table, key=section_name, file_path=file_path)
        for section_name, section_table in doc.items()
    ]


def list_to_toml(items: list[ConfigModel]) -> TOMLDocument:
    """Serialise a list of ConfigModel instances to a TOML document.

    Each item's key_field value is used as the top-level [section] heading.
    Items without a key_field are serialised with an empty string key.
    """
    doc = tomlkit.document()
    for obj in items:
        key = ""
        for f in dataclasses.fields(obj):  # type: ignore[arg-type]
            if f.metadata.get("toml_key"):
                key = getattr(obj, f.name)
                break
        section = tomlkit.table()
        _fill_table(section, obj)
        doc.add(key, section)
    return doc


# ─── update_toml_doc ──────────────────────────────────────────────────────────


def update_toml_doc(doc: TOMLDocument | TOMLTable, obj: ConfigModel) -> None:
    """Update a tomlkit document in-place with current field values.

    Scalar fields are updated by key assignment (preserving surrounding comments).
    Nested ConfigModel fields are recursed into.
    list[ConfigModel] fields are skipped (too complex for in-place update).
    """
    hints = get_type_hints(type(obj))

    for f in dataclasses.fields(obj):  # type: ignore[arg-type]
        if "self_factory" in f.metadata or "toml_key" in f.metadata:
            continue

        field_name = f.name
        field_type = hints[field_name]
        value = getattr(obj, field_name)
        actual_type = _unwrap_optional(field_type)

        if value is None:
            continue

        if _is_list_of_config_model(actual_type):
            continue  # in-place update of lists not supported; preserves user content
        elif _is_config_model_type(actual_type):
            if field_name in doc:
                update_toml_doc(doc[field_name], value)
            else:
                sub = tomlkit.table()
                _fill_table(sub, value)
                doc.add(field_name, sub)
        else:
            doc[field_name] = value  # type: ignore[index]
```

- [ ] **Step 6: Run tests — expect pass**

```
uv run pytest tests/config/test_model.py -v
```
Expected: all tests PASS.

- [ ] **Step 7: Coding-guideline follow-up checklist**

- [ ] Conventions file read: `docs/coding_conventions.md`
- [ ] All functions and methods have full type annotations
- [ ] `snake_case` for all functions/variables, `UpperCamelCase` for classes
- [ ] `X | None` used (not `Optional[X]`), builtin collections used (`list`, not `List`)
- [ ] `uv run ruff check src/nova_navigator/config/model.py` — zero errors
- [ ] `uv run ty check .` — zero new errors from this file

---

## Task 2: `loader.py` — File lifecycle

**Files:**
- Create: `src/nova_navigator/config/loader.py`
- Test: `tests/config/test_loader.py`

- [ ] **Step 1: Write failing tests**

Create `tests/config/test_loader.py`:

```python
from __future__ import annotations

from dataclasses import dataclass, field
from pathlib import Path

import pytest
import tomlkit

from nova_navigator.config.model import ConfigModel, computed, key_field


# ── Helpers — import loader under test after patching config dir ───────────────
# We patch _APP_CONFIG_DIR via monkeypatch on the loader module.


@dataclass
class SimpleSettings(ConfigModel):
    """Simple test settings."""
    name: str = "default"
    count: int = 0


@dataclass
class SectionItem(ConfigModel):
    section_name: str = key_field()
    value: str = "x"


# ── ModelConfig ───────────────────────────────────────────────────────────────


def test_model_config_creates_file_when_missing(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    from nova_navigator.config import loader
    monkeypatch.setattr(loader, "_APP_CONFIG_DIR", tmp_path)

    class TestConfig(SimpleSettings):
        CONFIG_NAME = "test_simple"

    # Patch ModelConfig to recognise TestConfig
    from nova_navigator.config.loader import ModelConfig

    class TConfig(SimpleSettings, ModelConfig):
        CONFIG_NAME = "test_simple"

    instance = TConfig.load()
    config_file = tmp_path / "test_simple.toml"
    assert config_file.exists()
    assert instance.name == "default"
    assert instance.count == 0


def test_model_config_reads_existing_file(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    from nova_navigator.config import loader
    monkeypatch.setattr(loader, "_APP_CONFIG_DIR", tmp_path)

    from nova_navigator.config.loader import ModelConfig

    class TConfig(SimpleSettings, ModelConfig):
        CONFIG_NAME = "test_simple2"

    config_file = tmp_path / "test_simple2.toml"
    config_file.write_text('name = "loaded"\ncount = 7\n')

    instance = TConfig.load()
    assert instance.name == "loaded"
    assert instance.count == 7


def test_model_config_save_updates_file(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    from nova_navigator.config import loader
    monkeypatch.setattr(loader, "_APP_CONFIG_DIR", tmp_path)

    from nova_navigator.config.loader import ModelConfig

    class TConfig(SimpleSettings, ModelConfig):
        CONFIG_NAME = "test_simple3"

    instance = TConfig.load()
    instance.name = "updated"
    instance.save()

    content = (tmp_path / "test_simple3.toml").read_text()
    assert "updated" in content


def test_model_config_save_preserves_comments(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    from nova_navigator.config import loader
    monkeypatch.setattr(loader, "_APP_CONFIG_DIR", tmp_path)

    from nova_navigator.config.loader import ModelConfig

    class TConfig(SimpleSettings, ModelConfig):
        CONFIG_NAME = "test_simple4"

    config_file = tmp_path / "test_simple4.toml"
    config_file.write_text("# user comment\nname = 'old'\ncount = 0\n")

    instance = TConfig.load()
    instance.name = "new"
    instance.save()

    content = (tmp_path / "test_simple4.toml").read_text()
    assert "# user comment" in content
    assert "new" in content


def test_model_config_creates_parent_directories(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    from nova_navigator.config import loader
    nested_dir = tmp_path / "a" / "b" / "c"
    monkeypatch.setattr(loader, "_APP_CONFIG_DIR", nested_dir)

    from nova_navigator.config.loader import ModelConfig

    class TConfig(SimpleSettings, ModelConfig):
        CONFIG_NAME = "test_nested"

    TConfig.load()
    assert (nested_dir / "test_nested.toml").exists()


# ── ListConfig ────────────────────────────────────────────────────────────────


def test_list_config_creates_file_from_defaults(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    from nova_navigator.config import loader
    monkeypatch.setattr(loader, "_APP_CONFIG_DIR", tmp_path)

    from nova_navigator.config.loader import ListConfig

    class TListConfig(ListConfig):
        CONFIG_NAME = "test_list"

        @classmethod
        def default_items(cls) -> list[ConfigModel]:
            return [SectionItem(section_name="first", value="aaa")]

    instance = TListConfig.load()
    assert (tmp_path / "test_list.toml").exists()
    assert len(instance._items) == 1
    assert instance._items[0].section_name == "first"


def test_list_config_reads_existing_file(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    from nova_navigator.config import loader
    monkeypatch.setattr(loader, "_APP_CONFIG_DIR", tmp_path)

    from nova_navigator.config.loader import ListConfig

    class TListConfig(ListConfig):
        CONFIG_NAME = "test_list2"

        @classmethod
        def default_items(cls) -> list[ConfigModel]:
            return []

    (tmp_path / "test_list2.toml").write_text('[mykey]\nvalue = "loaded"\n')
    instance = TListConfig.load()
    assert len(instance._items) == 1
    assert instance._items[0].section_name == "mykey"
    assert instance._items[0].value == "loaded"
```

- [ ] **Step 2: Run tests to confirm they fail**

```
uv run pytest tests/config/test_loader.py -v
```
Expected: ImportError — `nova_navigator.config.loader` does not exist yet.

- [ ] **Step 3: Implement `src/nova_navigator/config/loader.py`**

```python
from __future__ import annotations

import sys
from abc import ABC, abstractmethod
from pathlib import Path
from typing import Any, ClassVar, Self

import tomlkit

from .model import (
    ConfigModel,
    from_toml,
    list_from_toml,
    list_to_toml,
    to_toml,
    update_toml_doc,
)

__all__ = [
    "ConfigBase",
    "ListConfig",
    "ModelConfig",
    "get_config_file_path",
]


def _get_config_path(appname: str) -> Path:
    # Linux only (Ubuntu 24.04)
    return Path.home() / ".config" / appname


_APP_CONFIG_DIR: Path = _get_config_path("nova_navigator")
_DEFAULT_CONFIG_DIR: Path = Path(__file__).parent.parent.parent.parent / "config" / "default"


def get_config_file_path(config_filename: str) -> Path:
    """Return the path to a config file, falling back to the bundled default."""
    user_path = _APP_CONFIG_DIR / config_filename
    if user_path.exists():
        return user_path
    return _DEFAULT_CONFIG_DIR / config_filename


class ConfigBase(ABC):
    """Abstract base for file-backed config objects.

    Subclasses declare CONFIG_NAME and inherit either ModelConfig or ListConfig.
    """

    CONFIG_NAME: ClassVar[str]

    @classmethod
    @abstractmethod
    def load(cls) -> Self: ...

    @abstractmethod
    def save(self) -> None: ...

    def _config_file_path(self) -> Path:
        return _APP_CONFIG_DIR / f"{self.CONFIG_NAME}.toml"


class ModelConfig(ConfigBase):
    """File-backed config whose root is a single ConfigModel.

    The concrete class must also inherit ConfigModel and be decorated with @dataclass.
    """

    _toml_doc: tomlkit.TOMLDocument | None

    @classmethod
    def load(cls) -> Self:
        config_path = _APP_CONFIG_DIR / f"{cls.CONFIG_NAME}.toml"
        if config_path.exists():
            with open(config_path) as f:
                doc = tomlkit.load(f)
            instance = from_toml(cls, doc, file_path=str(config_path))  # type: ignore[arg-type]
        else:
            instance = cls()  # type: ignore[call-arg]
            doc = to_toml(instance)  # type: ignore[arg-type]
            _APP_CONFIG_DIR.mkdir(parents=True, exist_ok=True)
            with open(config_path, "w") as f:
                f.write(tomlkit.dumps(doc))
        object.__setattr__(instance, "_toml_doc", doc)
        return instance

    def save(self) -> None:
        doc: tomlkit.TOMLDocument | None = getattr(self, "_toml_doc", None)
        if doc is None:
            doc = to_toml(self)  # type: ignore[arg-type]
        else:
            update_toml_doc(doc, self)  # type: ignore[arg-type]
        _APP_CONFIG_DIR.mkdir(parents=True, exist_ok=True)
        with open(self._config_file_path(), "w") as f:
            f.write(tomlkit.dumps(doc))


class ListConfig(ConfigBase):
    """File-backed config that owns an open list of ConfigModel items.

    The concrete class must implement default_items() and declare the item type
    via _item_cls class variable.
    """

    _item_cls: ClassVar[type[ConfigModel]]
    _items: list[Any]
    _toml_doc: tomlkit.TOMLDocument | None

    @classmethod
    @abstractmethod
    def default_items(cls) -> list[ConfigModel]: ...

    @classmethod
    def load(cls) -> Self:
        config_path = _APP_CONFIG_DIR / f"{cls.CONFIG_NAME}.toml"
        if config_path.exists():
            with open(config_path) as f:
                doc = tomlkit.load(f)
            items = list_from_toml(cls._item_cls, doc, file_path=str(config_path))
        else:
            items = cls.default_items()
            doc = list_to_toml(items)
            _APP_CONFIG_DIR.mkdir(parents=True, exist_ok=True)
            with open(config_path, "w") as f:
                f.write(tomlkit.dumps(doc))

        instance = cls.__new__(cls)
        object.__setattr__(instance, "_items", items)
        object.__setattr__(instance, "_toml_doc", doc)
        return instance

    def save(self) -> None:
        doc = list_to_toml(self._items)
        _APP_CONFIG_DIR.mkdir(parents=True, exist_ok=True)
        with open(self._config_file_path(), "w") as f:
            f.write(tomlkit.dumps(doc))
```

- [ ] **Step 4: Run tests — expect pass**

```
uv run pytest tests/config/test_loader.py -v
```
Expected: all tests PASS.

- [ ] **Step 5: Coding-guideline follow-up checklist**

- [ ] Conventions file read: `docs/coding_conventions.md`
- [ ] All functions and methods have full type annotations
- [ ] `uv run ruff check src/nova_navigator/config/loader.py` — zero errors
- [ ] `uv run ty check .` — zero new errors from this file

---

## Task 3: `filetypes.py` — FileTypeConfig

**Files:**
- Create: `src/nova_navigator/config/filetypes.py`
- Test: `tests/config/test_filetypes.py`

- [ ] **Step 1: Write failing tests**

Create `tests/config/test_filetypes.py`:

```python
from __future__ import annotations

from pathlib import Path, PurePath

import pytest

from nova_navigator.config.filetypes import FileTypeConfig


def test_filetypes_default_construction_has_default_section(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    from nova_navigator.config import loader
    monkeypatch.setattr(loader, "_APP_CONFIG_DIR", tmp_path)

    cfg = FileTypeConfig.load()
    assert cfg._default_section is not None
    assert cfg._default_section.section_name == "default"


def test_filetypes_writes_file_on_first_load(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    from nova_navigator.config import loader
    monkeypatch.setattr(loader, "_APP_CONFIG_DIR", tmp_path)

    FileTypeConfig.load()
    assert (tmp_path / "filetypes.toml").exists()


def test_filetypes_get_icon_for_video(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    from nova_navigator.config import loader
    monkeypatch.setattr(loader, "_APP_CONFIG_DIR", tmp_path)

    cfg = FileTypeConfig.load()
    section = cfg._find_section_for_path(PurePath("movie.mp4"))
    assert section.icon == "video"


def test_filetypes_get_open_command_for_file(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    from nova_navigator.config import loader
    monkeypatch.setattr(loader, "_APP_CONFIG_DIR", tmp_path)

    cfg = FileTypeConfig.load()
    cmd = cfg.get_open_command_for_file_path(PurePath("/home/user/doc.pdf"))
    assert isinstance(cmd, list)
    assert len(cmd) > 0


def test_filetypes_get_colors_returns_tuple(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    from nova_navigator.config import loader
    monkeypatch.setattr(loader, "_APP_CONFIG_DIR", tmp_path)

    cfg = FileTypeConfig.load()
    color, bg = cfg.get_colors_for_filename("file.txt")
    # colors are optional; just verify the return type
    assert isinstance(color, str) or color is None
    assert isinstance(bg, str) or bg is None


def test_filetypes_unknown_extension_uses_default(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    from nova_navigator.config import loader
    monkeypatch.setattr(loader, "_APP_CONFIG_DIR", tmp_path)

    cfg = FileTypeConfig.load()
    section = cfg._find_section_for_path(PurePath("mystery.xyzzy"))
    assert section.section_name == "default"
```

- [ ] **Step 2: Run tests to confirm they fail**

```
uv run pytest tests/config/test_filetypes.py -v
```
Expected: ImportError.

- [ ] **Step 3: Implement `src/nova_navigator/config/filetypes.py`**

```python
from __future__ import annotations

import mimetypes
import re
from dataclasses import dataclass
from pathlib import PurePath
from typing import ClassVar

from nova_widgets import Icon

from ..icons import ICONS
from .loader import ListConfig
from .model import ConfigModel, computed, key_field

__all__ = ["FileTypeConfig"]


def _compile_pattern(pattern_str: str | None) -> re.Pattern[str] | None:
    return re.compile(pattern_str) if pattern_str else None


@dataclass
class Section(ConfigModel):
    """A filetype rule: maps mimetype/regex patterns to open command, icon, and colour."""

    section_name: str = key_field()
    mimetype: str | None = None
    mimetype_pattern: re.Pattern[str] | None = computed(
        lambda s: _compile_pattern(s.mimetype)
    )
    regex: str | None = None
    regex_pattern: re.Pattern[str] | None = computed(
        lambda s: _compile_pattern(s.regex)
    )
    open: str | None = None
    open_cmd: list[str] | None = computed(
        lambda s: s.open.split() if s.open else None
    )
    color: str | None = None
    icon: str | None = None
    background_color: str | None = None


class FileTypeConfig(ListConfig):
    """Maps file types to open commands, icons, and colours."""

    CONFIG_NAME: ClassVar[str] = "filetypes"
    _item_cls: ClassVar[type[ConfigModel]] = Section

    _sections: list[Section]
    _default_section: Section

    @classmethod
    def default_items(cls) -> list[ConfigModel]:
        return [
            Section(section_name="default", open="xdg-open %f"),
            Section(section_name="videos", mimetype="video/.*", open="xdg-open %f", icon="video"),
            Section(section_name="images", mimetype="image/.*", icon="image"),
            Section(section_name="python", mimetype="text/x-python", icon="python"),
            Section(
                section_name="archives",
                mimetype="application/zip|application/x-tar|application/x-gzip|application/x-bzip2|application/java-archive",
                icon="archive",
            ),
            Section(section_name="pdf", mimetype="application/pdf", icon="pdf"),
            Section(section_name="deb", mimetype="application/vnd.debian.binary-package", icon="deb"),
            Section(section_name="audio", mimetype="audio/.*", icon="audio"),
            Section(section_name="cpp", mimetype="text/x-c\\+\\+src", icon="cpp"),
            Section(section_name="text", mimetype="text/.*", icon="text"),
            Section(section_name="model", mimetype="model/.*", icon="model"),
        ]

    @classmethod
    def load(cls) -> FileTypeConfig:
        instance = super().load()
        instance._sections = instance._items  # type: ignore[attr-defined]
        default = next((s for s in instance._sections if s.section_name == "default"), None)
        if default is None:
            raise ValueError(
                f"Filetype config missing required [default] section in "
                f"{cls.CONFIG_NAME}.toml"
            )
        instance._default_section = default
        return instance  # type: ignore[return-value]

    def _replace_variables(self, cmd: list[str], path: PurePath) -> list[str]:
        cmd = [c.replace("%f", str(path)) for c in cmd]
        cmd = [c.replace("%d", str(path.parent)) for c in cmd]
        return cmd

    def _find_section_for_path(self, path: PurePath) -> Section:
        mimetype = mimetypes.guess_type(path.as_posix())[0]
        for section in self._sections:
            if section.mimetype and mimetype and section.mimetype_pattern and section.mimetype_pattern.search(mimetype):
                return section
            if section.regex and section.regex_pattern and section.regex_pattern.search(path.as_posix()):
                return section
        return self._default_section

    def get_open_command_for_file_path(self, path: PurePath) -> list[str]:
        section = self._find_section_for_path(path)
        open_cmd = section.open_cmd or self._default_section.open_cmd
        if open_cmd is None:
            raise RuntimeError(
                "No open command defined in filetype config (missing 'open' in [default] section)"
            )
        return self._replace_variables(open_cmd, path)

    def get_colors_for_filename(self, filename: str) -> tuple[str | None, str | None]:
        section = self._find_section_for_path(PurePath(filename))
        return section.color, section.background_color

    def get_icon_for_filename(self, filename: str, default: Icon | None = None) -> Icon:
        if default is None:
            default = Icon()
        section = self._find_section_for_path(PurePath(filename))
        if not section.icon:
            return default
        return ICONS.get_icon(section.icon, default=default)
```

- [ ] **Step 4: Run tests — expect pass**

```
uv run pytest tests/config/test_filetypes.py -v
```
Expected: all tests PASS.

- [ ] **Step 5: Coding-guideline follow-up checklist**

- [ ] All functions and methods have full type annotations
- [ ] `uv run ruff check src/nova_navigator/config/filetypes.py` — zero errors
- [ ] `uv run ty check .` — zero new errors

---

## Task 4: `bookmarks.py` — BookmarkConfig

**Files:**
- Create: `src/nova_navigator/config/bookmarks.py`
- Test: `tests/config/test_bookmarks.py`

- [ ] **Step 1: Write failing tests**

Create `tests/config/test_bookmarks.py`:

```python
from __future__ import annotations

from pathlib import Path

import pytest


def test_bookmarks_default_construction_has_groups(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    from nova_navigator.config import loader
    monkeypatch.setattr(loader, "_APP_CONFIG_DIR", tmp_path)

    from nova_navigator.config.bookmarks import BookmarkConfig

    cfg = BookmarkConfig.load()
    assert len(cfg.groups) > 0


def test_bookmarks_writes_file_on_first_load(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    from nova_navigator.config import loader
    monkeypatch.setattr(loader, "_APP_CONFIG_DIR", tmp_path)

    from nova_navigator.config.bookmarks import BookmarkConfig

    BookmarkConfig.load()
    assert (tmp_path / "bookmarks.toml").exists()


def test_bookmarks_groups_have_bookmarks(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    from nova_navigator.config import loader
    monkeypatch.setattr(loader, "_APP_CONFIG_DIR", tmp_path)

    from nova_navigator.config.bookmarks import BookmarkConfig

    cfg = BookmarkConfig.load()
    # at least one group has at least one bookmark
    assert any(len(g.bookmarks) > 0 for g in cfg.groups)


def test_bookmarks_round_trip(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    from nova_navigator.config import loader
    monkeypatch.setattr(loader, "_APP_CONFIG_DIR", tmp_path)

    from nova_navigator.config.bookmarks import BookmarkConfig

    first = BookmarkConfig.load()
    group_count = len(first.groups)

    second = BookmarkConfig.load()
    assert len(second.groups) == group_count
```

- [ ] **Step 2: Run tests to confirm they fail**

```
uv run pytest tests/config/test_bookmarks.py -v
```
Expected: ImportError.

- [ ] **Step 3: Implement `src/nova_navigator/config/bookmarks.py`**

```python
from __future__ import annotations

from dataclasses import dataclass, field
from typing import ClassVar

from .loader import ModelConfig
from .model import ConfigModel

__all__ = ["BookmarkConfig"]


@dataclass
class Bookmark(ConfigModel):
    """A single bookmark entry."""

    name: str = ""
    path: str = ""
    icon: str | None = None


@dataclass
class Group(ConfigModel):
    """A group of bookmarks."""

    name: str = ""
    icon: str | None = None
    bookmarks: list[Bookmark] = field(default_factory=list)


@dataclass
class BookmarkConfig(ConfigModel, ModelConfig):
    """Bookmark groups for quick navigation."""

    CONFIG_NAME: ClassVar[str] = "bookmarks"

    groups: list[Group] = field(
        default_factory=lambda: [
            Group(
                name="Computer",
                icon="computer",
                bookmarks=[
                    Bookmark(name="Home", path="~", icon="house"),
                    Bookmark(name="Documents", path="~/Documents", icon="file"),
                    Bookmark(name="Downloads", path="~/Downloads", icon="download"),
                    Bookmark(name="Filesystem", path="/", icon="open_folder"),
                ],
            ),
            Group(
                name="Bookmarks",
                icon="bookmark",
                bookmarks=[],
            ),
        ]
    )
```

- [ ] **Step 4: Run tests — expect pass**

```
uv run pytest tests/config/test_bookmarks.py -v
```
Expected: all tests PASS.

- [ ] **Step 5: Coding-guideline follow-up checklist**

- [ ] All functions and methods have full type annotations
- [ ] `uv run ruff check src/nova_navigator/config/bookmarks.py` — zero errors
- [ ] `uv run ty check .` — zero new errors

---

## Task 5: `settings.py` — Settings config

**Files:**
- Create: `src/nova_navigator/config/settings.py`
- Test: `tests/config/test_settings.py`

- [ ] **Step 1: Write failing tests**

Create `tests/config/test_settings.py`:

```python
from __future__ import annotations

from pathlib import Path

import pytest


def test_settings_default_construction() -> None:
    from nova_navigator.config.settings import Settings

    s = Settings()
    assert s.general.show_hidden_files is False
    assert s.general.confirm_delete is True
    assert s.network.ssh_timeout == 30
    assert s.network.proxy == ""


def test_settings_writes_file_on_first_load(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    from nova_navigator.config import loader
    monkeypatch.setattr(loader, "_APP_CONFIG_DIR", tmp_path)

    from nova_navigator.config.settings import Settings

    Settings.load()
    assert (tmp_path / "settings.toml").exists()


def test_settings_file_contains_section_comments(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    from nova_navigator.config import loader
    monkeypatch.setattr(loader, "_APP_CONFIG_DIR", tmp_path)

    from nova_navigator.config.settings import Settings

    Settings.load()
    content = (tmp_path / "settings.toml").read_text()
    assert "General application settings" in content
    assert "Network settings" in content


def test_settings_save_round_trip(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    from nova_navigator.config import loader
    monkeypatch.setattr(loader, "_APP_CONFIG_DIR", tmp_path)

    from nova_navigator.config.settings import Settings

    instance = Settings.load()
    instance.general.show_hidden_files = True
    instance.network.ssh_timeout = 60
    instance.save()

    reloaded = Settings.load()
    assert reloaded.general.show_hidden_files is True
    assert reloaded.network.ssh_timeout == 60


def test_settings_save_preserves_user_comment(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    from nova_navigator.config import loader
    monkeypatch.setattr(loader, "_APP_CONFIG_DIR", tmp_path)

    from nova_navigator.config.settings import Settings

    settings_file = tmp_path / "settings.toml"
    settings_file.write_text(
        "# user note\n[general]\nshow_hidden_files = false\nconfirm_delete = true\n"
        "[network]\nssh_timeout = 30\nproxy = ''\n"
    )

    instance = Settings.load()
    instance.general.show_hidden_files = True
    instance.save()

    content = settings_file.read_text()
    assert "# user note" in content
    assert "true" in content
```

- [ ] **Step 2: Run tests to confirm they fail**

```
uv run pytest tests/config/test_settings.py -v
```
Expected: ImportError.

- [ ] **Step 3: Implement `src/nova_navigator/config/settings.py`**

```python
from __future__ import annotations

from dataclasses import dataclass, field
from typing import ClassVar

from .loader import ModelConfig
from .model import ConfigModel

__all__ = ["Settings"]


@dataclass
class GeneralSettings(ConfigModel):
    """General application settings."""

    show_hidden_files: bool = False
    confirm_delete: bool = True


@dataclass
class NetworkSettings(ConfigModel):
    """Network settings."""

    ssh_timeout: int = 30
    proxy: str = ""


@dataclass
class Settings(ConfigModel, ModelConfig):
    """Application settings."""

    CONFIG_NAME: ClassVar[str] = "settings"

    general: GeneralSettings = field(default_factory=GeneralSettings)
    network: NetworkSettings = field(default_factory=NetworkSettings)
```

- [ ] **Step 4: Run tests — expect pass**

```
uv run pytest tests/config/test_settings.py -v
```
Expected: all tests PASS.

- [ ] **Step 5: Coding-guideline follow-up checklist**

- [ ] All functions and methods have full type annotations
- [ ] `uv run ruff check src/nova_navigator/config/settings.py` — zero errors
- [ ] `uv run ty check .` — zero new errors

---

## Task 6: `global_config.py` + `__init__.py`

**Files:**
- Create: `src/nova_navigator/config/global_config.py`
- Modify: `src/nova_navigator/config/__init__.py`

- [ ] **Step 1: Implement `src/nova_navigator/config/global_config.py`**

```python
from __future__ import annotations

from .bookmarks import BookmarkConfig
from .filetypes import FileTypeConfig
from .settings import Settings

__all__ = ["GlobalConfig", "conf_"]


class GlobalConfig:
    """Singleton holding all loaded config objects."""

    filetypes: FileTypeConfig
    bookmarks: BookmarkConfig
    settings: Settings

    def load_all_configs(self) -> None:
        for name, cls in GlobalConfig.__annotations__.items():
            setattr(self, name, cls.load())


conf_ = GlobalConfig()
```

- [ ] **Step 2: Implement `src/nova_navigator/config/__init__.py`**

```python
from .global_config import GlobalConfig, conf_
from .loader import get_config_file_path
from .model import ConfigModel, computed, key_field

__all__ = [
    "GlobalConfig",
    "ConfigModel",
    "computed",
    "conf_",
    "get_config_file_path",
    "key_field",
]
```

- [ ] **Step 3: Verify full test suite still passes**

```
uv run pytest tests/config/ -v
```
Expected: all tests PASS.

- [ ] **Step 4: Coding-guideline follow-up checklist**

- [ ] `uv run ruff check src/nova_navigator/config/` — zero errors
- [ ] `uv run ty check .` — zero new errors

---

## Task 7: Update call sites and migrate tests

**Files:**
- Modify: `src/nova_navigator/main.py`
- Modify: `src/nova_navigator/widgets/directory_browser.py`
- Modify: `src/nova_navigator/dialogs/bookmarks_dialog.py`
- Modify: `tests/widgets/conftest.py`
- Modify: `tests/test_toml_config.py` (delete content, add redirect notice)

- [ ] **Step 1: Update imports in `main.py`**

Find line:
```python
from nova_navigator.config import conf_, get_config_file_path
```
Replace with:
```python
from nova_navigator.config import conf_, get_config_file_path
```
(This import is already compatible — `__init__.py` exports both names. No change needed unless the old `config.py` is still present. After Task 8 removes it, this will resolve to the new package.)

- [ ] **Step 2: Update import in `widgets/directory_browser.py`**

Find:
```python
from ..config import conf_
```
(or `from nova_navigator.config import conf_`)

Verify it reads `from ..config import conf_` — this is already compatible with the new package.

- [ ] **Step 3: Update import in `dialogs/bookmarks_dialog.py`**

Find:
```python
from ..config import conf_
```
Verify this is already compatible.

- [ ] **Step 4: Verify `tests/widgets/conftest.py` import is already compatible**

The conftest imports:
```python
from nova_navigator.config import conf_, get_config_file_path
```
Both names are exported from the new `__init__.py`. No change needed.

- [ ] **Step 5: Run existing widget tests to confirm nothing is broken**

```
uv run pytest tests/widgets/ -v
```
Expected: all tests PASS.

- [ ] **Step 6: Coding-guideline follow-up checklist**

- [ ] `uv run ruff check src/nova_navigator/` — zero errors
- [ ] `uv run qa` — zero failures

---

## Task 8: Delete old files and final QA

**Files:**
- Delete: `src/nova_navigator/config.py`
- Delete: `src/nova_navigator/toml_config.py`
- Migrate/delete: `tests/test_toml_config.py`

- [ ] **Step 1: Confirm no remaining imports of old modules**

```
grep -r "from nova_navigator.toml_config\|from .toml_config\|from nova_navigator.config import.*Field\|from nova_navigator.config import.*TomlConfig" src/ tests/
```
Expected: zero matches.

- [ ] **Step 2: Delete old source files**

```
rm src/nova_navigator/config.py src/nova_navigator/toml_config.py
```

- [ ] **Step 3: Migrate `tests/test_toml_config.py`**

The tests in `test_toml_config.py` cover the old `TomlConfig`/`Field` ORM.
All equivalent behaviours are now covered in `tests/config/test_model.py`.
Delete the old file:

```
rm tests/test_toml_config.py
```

- [ ] **Step 4: Run full QA**

```
uv run qa
```
Expected: zero lint errors, zero type errors, zero test failures.

If `ty check` reports errors in call sites (e.g. `conf_.filetypes` attribute not found), add a `# type: ignore[attr-defined]` comment at the call site — `GlobalConfig` uses `__annotations__` for dynamic loading so the type checker cannot statically verify the attributes.

- [ ] **Step 5: Final coding-guideline follow-up checklist**

- [ ] `uv run qa` output reviewed — all checks pass
- [ ] No new `# type: ignore` comments beyond those noted above
- [ ] `config/default/bookmarks.toml` and `config/default/filetypes.toml` can be kept for reference but are no longer loaded by the app

---

## Self-Review Notes

- All tasks produce working, testable increments. Tasks 1–5 are independent of each other (except Task 2 depends on Task 1). Tasks 3–5 depend on Tasks 1–2.
- The `ListConfig.load()` in Task 2 uses `cls._item_cls` which must be set on concrete subclasses (`FileTypeConfig` sets it). Verify this is declared correctly in Task 3.
- `BookmarkConfig` uses `list[Bookmark]` inside `Group`, which requires `from_toml` to handle two-level `list[ConfigModel]` nesting. The generic `from_toml` handles this recursively — covered by `test_bookmarks_round_trip`.
- The `Settings.load()` / `save()` path exercises `update_toml_doc` for nested `ConfigModel` fields — covered by `test_settings_save_preserves_user_comment`.
