# Config Management Review

Review of `src/nova_navigator/config.py` and `src/nova_navigator/toml_config.py`.

## Architecture

The system has two layers.
`toml_config.py` is a mini TOML-to-Python ORM (`TomlConfig` + `Field` / `FieldInfo`).
`config.py` builds concrete config classes on top of it (`FileTypeConfig`, `BookmarkConfig`, `GlobalConfig`).

`TomlConfig` is well-structured with clear separation of field declaration, type coercion, optional/list unwrapping, and defaults.
Test coverage in `tests/test_toml_config.py` is thorough.

## Issues

### 1. Debug `print()` left in `BookmarkConfig.__init__`

`config.py` lines 171–172 contain bare `print(name)` / `print(item)` calls.
These must be removed.

### 2. `get_config_file_path` is broken

The body that would copy a default config into the user config directory is commented out.
The function always returns the *default* dir path, so users can never have a per-user `icons.csv`.
The function name is misleading given this behaviour.

### 3. `ConfigBase.load()` stores the wrong path on fallback

When the user config file does not exist, the default file is loaded but `TomlFile.file_path` is still set to `_APP_CONFIG_DIR / ...` (which does not exist).
If saving is ever implemented, it will attempt to write to a non-existent directory.

### 4. `write_all_configs` is `NotImplementedError`

`GlobalConfig.write_all_configs()` is declared but never implemented.
There is also no `save()` method on `ConfigBase`, so there is no end-to-end path for persisting user config changes.

### 5. `$HOME` is not expanded in bookmarks

`bookmarks.toml` uses `path = "$HOME"` as a literal string.
`BookmarkConfig` never calls `os.path.expandvars()` or `Path.expanduser()`, so consumers receive the raw `$HOME` string instead of the actual home directory path.

### 6. `conf_` is accessible before `load_all_configs()` is called

`conf_ = GlobalConfig()` is an empty object at import time.
Any module that accesses `conf_.filetypes` or `conf_.bookmarks` before `load_all_configs()` is called raises an `AttributeError` with no informative message.

### 7. Incorrect type annotations on computed pattern fields

`mimetype_pattern` and `regex_pattern` are annotated as `re.Pattern[str]` but `_compile_pattern` returns `re.Pattern[str] | None`.
The runtime guard (`if file_type.mimetype and file_type.mimetype_pattern.search(...)`) works, but the annotation is wrong and causes type-checker errors.

### 8. Unguarded `assert open_cmd is not None`

In `get_open_command_for_file_path`, if the `[default]` section has no `open` key (e.g. a user's custom config), both `section.open_cmd` and `_default_section.open_cmd` will be `None` and the assertion fires.
This should raise a `RuntimeError` or `ValueError` with a clear message.

### 9. `Config` class is dead code

`Config(ConfigBase, TomlConfig)` is defined but has no `CONFIG_NAME`, no concrete subclass, and is not referenced anywhere.
It should be removed.

### 10. `TomlConfig.__init__` only reads own-class annotations

`self.__annotations__` does not include inherited annotations (standard Python behaviour).
This is a silent footgun if anyone creates a two-level `TomlConfig` hierarchy.
A comment or docstring should document this limitation explicitly.

## Minor

- `BookmarkConfig` has a commented-out `bookmarks: list[Bookmark]` field that should be cleaned up.
- `_APP_CONFIG_DIR` includes Windows and macOS branches that are dead code given the Ubuntu-only platform constraint.
