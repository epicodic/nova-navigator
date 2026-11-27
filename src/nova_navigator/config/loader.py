"""File lifecycle management for config models.

Provides loaders that handle reading, writing, and creating TOML-backed
config files in the application config directory.
"""

from __future__ import annotations

import abc
import errno
import os
from pathlib import Path
from types import SimpleNamespace
from typing import Any, ClassVar, Self

import tomlkit

from nova_navigator.config.model import (
    BaseModel,
    from_toml,
    list_from_toml,
    list_to_toml,
    to_toml,
    update_toml_doc,
)
from nova_navigator.vfs import VPath
from nova_navigator.vfs.filesystems.local import LocalFilesystem

_APP_CONFIG_DIR: Path = Path.home() / ".config" / "nova-navigator"
_DEFAULT_CONFIG_DIR: Path = Path(__file__).parent.parent / "_default"


def _can_write_in_place(target: Path) -> bool:
    """Tell whether *target* can be rewritten directly: it is writable but its directory is not."""
    return os.access(target, os.W_OK) and not os.access(target.parent, os.W_OK | os.X_OK)


def _write_atomic(path: Path, data: bytes) -> None:
    """Replace ``path`` with ``data`` so that a failure leaves the previous file intact.

    A symlink is resolved first so the link stays in place and its target is updated.
    Raises ``FileNotFoundError`` when the parent directory is missing, as a plain write would.
    A symlink loop, which cannot be resolved, raises ``OSError`` (``ELOOP``).
    When the temporary file cannot be created because the directory is read-only but the
    existing file is writable, the file is rewritten in place instead.
    """
    target = Path(os.path.realpath(path))
    if not target.parent.is_dir():
        raise FileNotFoundError(f"No such directory: {target.parent}")
    if target.is_symlink():
        raise OSError(errno.ELOOP, os.strerror(errno.ELOOP), str(path))
    if os.path.lexists(target) and not os.access(target, os.W_OK):
        raise PermissionError(errno.EACCES, os.strerror(errno.EACCES), str(target))
    filesystem = LocalFilesystem.singleton()
    try:
        writer = filesystem.write_atomic(VPath(str(target), filesystem))
    except PermissionError:
        if not _can_write_in_place(target):
            raise
        target.write_bytes(data)
        return
    try:
        writer.write(data)
    except BaseException:
        writer.abort()
        raise
    writer.close()


def get_config_file_path(config_filename: str) -> Path:
    """Return user config path if it exists, otherwise fall back to the default config dir.

    Args:
        config_filename: The config filename (e.g. ``"bookmarks.toml"``).
    """
    user_path = _APP_CONFIG_DIR / config_filename
    if user_path.exists():
        return user_path
    return _DEFAULT_CONFIG_DIR / config_filename


def app_config_dir() -> Path:
    """Return the directory holding the user's config files."""
    return _APP_CONFIG_DIR


class ConfigBase(abc.ABC):
    """Abstract base class for config loaders."""

    CONFIG_NAME: ClassVar[str]

    @classmethod
    @abc.abstractmethod
    def load(cls) -> Self:
        """Load config from file, creating it with defaults if missing."""
        ...

    @abc.abstractmethod
    def save(self) -> None:
        """Persist current state back to the config file."""
        ...


class ModelConfig(ConfigBase):
    """Config loader for fixed-schema configs whose root is a ConfigModel.

    Mix into a ``ConfigModel`` dataclass subclass and set ``CONFIG_NAME``.
    The ``load()`` classmethod reads from ``_APP_CONFIG_DIR/{CONFIG_NAME}.toml``,
    creating the file from defaults if it does not exist.
    The loaded tomlkit document is stored as ``_toml_doc`` so that ``save()``
    can update it in-place and preserve user comments.
    """

    @classmethod
    def load(cls) -> Self:
        """Load the config, creating the file from defaults when absent."""
        config_dir: Path = _APP_CONFIG_DIR
        file_path = config_dir / f"{cls.CONFIG_NAME}.toml"

        if not file_path.exists():
            instance: Self = cls()
            doc = to_toml(instance)  # type: ignore
            config_dir.mkdir(parents=True, exist_ok=True)
            file_path.write_text(tomlkit.dumps(doc))
            # frozen-dataclass-safe: use object.__setattr__ in case the concrete class is frozen
            object.__setattr__(instance, "_toml_doc", doc)
            return instance

        text = file_path.read_text()
        doc = tomlkit.loads(text)
        instance = from_toml(cls, doc)  # type: ignore
        # frozen-dataclass-safe: use object.__setattr__ in case the concrete class is frozen
        object.__setattr__(instance, "_toml_doc", doc)
        return instance

    def save(self) -> None:
        """Write the current field values back to the config file."""
        config_dir: Path = _APP_CONFIG_DIR
        file_path = config_dir / f"{self.CONFIG_NAME}.toml"
        doc = getattr(self, "_toml_doc", None)
        if doc is None:
            doc = to_toml(self)  # type: ignore
        else:
            update_toml_doc(doc, self)  # type: ignore
        _write_atomic(file_path, tomlkit.dumps(doc).encode("utf-8"))


class ListConfig(ConfigBase):
    """Config loader for open-list configs keyed by TOML section name.

    Subclasses must set ``CONFIG_NAME`` and implement ``default_items()``.
    Optionally set ``_item_cls`` to specify the item type; if not set,
    the type is inferred from the first element returned by ``default_items()``.
    """

    _item_cls: ClassVar[type[BaseModel]]
    _items: list[Any]

    @classmethod
    @abc.abstractmethod
    def default_items(cls) -> list[BaseModel]:
        """Return the default list of items used when the config file is missing."""
        ...

    @classmethod
    def load(cls) -> Self:
        """Load items from file, writing defaults to a new file when absent."""
        config_dir: Path = _APP_CONFIG_DIR
        file_path = config_dir / f"{cls.CONFIG_NAME}.toml"

        item_cls: type[BaseModel] | None = getattr(cls, "_item_cls", None)
        defaults = cls.default_items()
        if item_cls is None and defaults:
            item_cls = type(defaults[0])

        if not file_path.exists():
            doc = list_to_toml(defaults)
            config_dir.mkdir(parents=True, exist_ok=True)
            file_path.write_text(tomlkit.dumps(doc))
            instance: Self = object.__new__(cls)
            instance._items = defaults
            return instance

        text = file_path.read_text()
        doc = tomlkit.loads(text)
        instance = object.__new__(cls)

        if item_cls is not None:
            instance._items = list_from_toml(item_cls, doc)
        else:
            # NOTE: items are SimpleNamespace objects because _item_cls could not be inferred.
            # save() will raise RuntimeError if called on this instance.
            items: list[Any] = []
            for key, table in doc.items():
                ns = SimpleNamespace(section_name=key, **dict(table.items()))
                items.append(ns)
            instance._items = items

        return instance

    def save(self) -> None:
        """Write current items back to the config file."""
        non_model_items = [i for i in self._items if not isinstance(i, BaseModel)]
        if non_model_items:
            raise RuntimeError(f"{type(self).__name__}.save(): cannot save — _items contains non-ConfigModel objects. Set _item_cls on the class so items can be properly deserialised.")
        doc = list_to_toml(self._items)
        _APP_CONFIG_DIR.mkdir(parents=True, exist_ok=True)
        file_path = _APP_CONFIG_DIR / f"{self.CONFIG_NAME}.toml"
        file_path.write_text(tomlkit.dumps(doc))


__all__ = ["ConfigBase", "ListConfig", "ModelConfig", "app_config_dir", "get_config_file_path"]
