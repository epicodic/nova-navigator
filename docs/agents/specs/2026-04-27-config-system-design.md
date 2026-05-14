# Config System Redesign

## Overview

Replace `config.py` and `toml_config.py` with a new `config/` subpackage under `nova_navigator/`.
The new system uses Python `dataclasses` as the config schema language, a generic `from_toml`/`to_toml` framework, and a file-lifecycle layer that auto-creates config files from code-defined defaults on first run.

## Requirements

- Config objects must be fully default-constructable with no external default files.
- If a config file does not exist, it is created from the default values.
- TOML comments are preserved on round-trip saves.
- Class/field docstrings are written as `#` comment blocks on first write.
- Serialisation and deserialisation are handled generically by the framework; per-config hand-written code is minimised.
- The `conf_` singleton is kept as-is for now (to be replaced by app-level DI later).
- A new `Settings` config type is introduced for user preferences and keybindings.

## Non-Goals

- Replacing the `conf_` singleton with dependency injection (deferred).
- Live reload of config at runtime.
- Merging user config with defaults on a per-key basis.

---

## Package Structure

```
src/nova_navigator/config/
    __init__.py          # re-exports: conf_, ConfigModel, computed, key_field
    model.py             # ConfigModel, computed(), key_field(), from_toml(), to_toml()
    loader.py            # ConfigBase, ModelConfig, ListConfig — file lifecycle
    filetypes.py         # FileTypeConfig (ListConfig) and Section (ConfigModel)
    bookmarks.py         # BookmarkConfig (ModelConfig), Group, Bookmark
    settings.py          # Settings (ModelConfig), GeneralSettings, NetworkSettings
    global_config.py     # GlobalConfig, conf_
```

The old `config.py` and `toml_config.py` are deleted.
Call sites change only their import path; the `conf_` name and the public methods on `FileTypeConfig` and `BookmarkConfig` are unchanged.
The `config/default/` directory becomes unused for TOML configs (but `icons.csv` stays).

---

## `model.py` — Schema language

### `ConfigModel`

All config schema classes inherit from `ConfigModel` and are decorated with `@dataclass`.
`ConfigModel.__post_init__` iterates `dataclasses.fields(self)` and runs any `"self_factory"` callables in declaration order.

```python
@dataclass
class ConfigModel:
    def __post_init__(self) -> None:
        for f in dataclasses.fields(self):
            fn = f.metadata.get("self_factory")
            if fn is not None:
                object.__setattr__(self, f.name, fn(self))
```

### `computed(factory)`

Marks a field as derived — computed from sibling fields after construction.
Not read from TOML, not written to TOML.

```python
def computed(factory: Callable[[Any], Any]) -> Any:
    return field(init=False, default=None, metadata={"self_factory": factory})
```

### `key_field()`

Marks the field that holds the TOML section name (e.g. `[videos]` in `filetypes.toml`).
Not written as a key-value pair — its value becomes the `[heading]`.

```python
def key_field() -> Any:
    return field(default="", metadata={"toml_key": True})
```

### `from_toml(cls, table, *, key=None)`

Generic recursive deserialiser driven by `dataclasses.fields(cls)`.
`key` is the section name, passed when the caller iterates a list of sections.

Field dispatch:

| Field characteristic | Action |
|---|---|
| `"self_factory"` in metadata | Skip — computed after construction |
| `"toml_key"` in metadata | Set from `key` argument; not read from table |
| Type is `ConfigModel` subclass | Recurse into subtable |
| Type is `list[ConfigModel subclass]` | Iterate items; recurse each |
| Scalar (`str`, `int`, `bool`, `str \| None`) | Coerce from TOML value |

Type coercion handles `T | None` (unwrap to inner type, allow `None` if key absent).

### `to_toml(obj)`

Generic recursive serialiser.
Builds a `tomlkit.TOMLDocument` (or `tomlkit.table()` for subtables).
Field dispatch mirrors `from_toml`.
For each table/subtable, prepends a `#` comment block derived from `cls.__doc__` (stripped, one sentence per line).

### Round-trip save

On `save()`, the existing tomlkit document (loaded at read time and stored on the config instance) is mutated in-place — only scalar leaf values are updated via the tomlkit key-assignment API.
This preserves all user-added comments and whitespace formatting.

---

## `loader.py` — File lifecycle

### `ConfigBase` (abstract)

```python
class ConfigBase(ABC):
    CONFIG_NAME: str      # declared by subclass

    @classmethod
    def load(cls) -> Self: ...

    def save(self) -> None: ...
```

Config files are located at `~/.config/nova_navigator/<CONFIG_NAME>.toml`.
On first write, `_APP_CONFIG_DIR.mkdir(parents=True, exist_ok=True)` is called.

### `ModelConfig(ConfigBase)`

For fixed-schema configs whose root is a single `ConfigModel` (e.g. `Settings`, `BookmarkConfig`).

`load()` algorithm:
1. Try to open the user config file.
2. If missing: call `cls()` (zero-arg) → `to_toml(instance)` → write file → return instance.
3. If present: `from_toml(cls, doc)` → return instance.

The loaded `tomlkit.TOMLDocument` is kept on the instance in a `_toml_doc` attribute (set by `ConfigBase`, not a dataclass field) for use by `save()`.

### `ListConfig(ConfigBase)`

For open-list configs (`FileTypeConfig`).
The config class owns a `list[SomeConfigModel]` and implements `default_items()`.

```python
class ListConfig(ConfigBase):
    @classmethod
    def default_items(cls) -> list[ConfigModel]: ...
```

`load()` algorithm is the same, but step 2 serialises `cls.default_items()` instead of a single root object.

---

## Config classes

### `Settings` (`ModelConfig`)

```python
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
class Settings(ConfigModel):
    """Application settings."""
    CONFIG_NAME = "settings"
    general: GeneralSettings = field(default_factory=GeneralSettings)
    network: NetworkSettings = field(default_factory=NetworkSettings)
```

`Settings()` is valid with no arguments.
`to_toml(Settings())` produces a commented, hierarchical TOML file on first run.

### `FileTypeConfig` (`ListConfig`)

`Section` is a `ConfigModel` with `key_field()` for the section name and `computed()` fields for the compiled patterns and split open command.
`FileTypeConfig.default_items()` returns the built-in list of video/image/python/archive/… sections.
The TOML file is an open list — users can add/remove/rename sections freely.

### `BookmarkConfig` (`ModelConfig`)

`Bookmark` and `Group` are `ConfigModel` subclasses.
`Group` contains a `list[Bookmark]`.
`BookmarkConfig` is the root with a `list[Group]` field.
Default value includes the standard Computer/Documents/Downloads/Filesystem bookmarks.

---

## Error handling

| Situation | Behaviour |
|---|---|
| Required field missing from TOML | Raise `ConfigLoadError(field_name, file_path)` |
| Unknown field in TOML | Ignore silently (forward/backward compat) |
| Config directory missing on first write | `mkdir(parents=True, exist_ok=True)` |
| TOML parse error | Propagate `tomlkit.exceptions.ParseError` with file path added to message |

No silent substitution of defaults for missing required fields — the user should know their file is malformed.
Optional fields (`str | None`) default to `None` if absent.

---

## Testing

Unit tests for `model.py` (`from_toml`, `to_toml`, `computed`, round-trip) replace `tests/test_toml_config.py`.
Tests for `loader.py` use `tmp_path` to avoid touching the real config directory.
`FileTypeConfig`, `BookmarkConfig`, and `Settings` each get integration tests covering default construction, file write, and reload.
Existing `test_toml_config.py` tests are migrated/replaced; no tests are deleted without a replacement.

---

## Migration

1. Implement `config/model.py` and `config/loader.py` with full tests.
2. Migrate `FileTypeConfig` to `config/filetypes.py`.
3. Migrate `BookmarkConfig` to `config/bookmarks.py`.
4. Implement `Settings` in `config/settings.py`.
5. Implement `config/global_config.py`; update `GlobalConfig` to include `settings`.
6. Update `config/__init__.py` re-exports.
7. Update all call-site imports (`main.py`, `directory_browser.py`, `bookmarks_dialog.py`).
8. Delete `config.py`, `toml_config.py`, and `config/default/*.toml`.
9. Run `uv run qa` — zero failures required before completion.
