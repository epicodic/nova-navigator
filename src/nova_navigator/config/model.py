"""Core schema framework for config models.

Provides base classes and serialisation utilities for TOML-backed
configuration using Python dataclasses.
"""

import dataclasses
from dataclasses import fields as dc_fields
from types import UnionType
from typing import Any, get_args, get_origin, get_type_hints

import tomlkit
import tomlkit.items
from tomlkit import TOMLDocument


class ConfigLoadError(Exception):
    """Raised when a required TOML field is missing during deserialisation."""


class BaseModel:
    """Base class for dataclass-based config models.

    Subclasses must apply ``@dataclass``.
    Fields declared with :func:`computed` are evaluated in ``__post_init__``
    after the dataclass ``__init__`` runs.
    """

    def __post_init__(self) -> None:
        for f in dataclasses.fields(self):  # type: ignore
            if "self_factory" in f.metadata:
                factory = f.metadata["self_factory"]
                object.__setattr__(self, f.name, factory(self))


def computed(factory: Any) -> Any:
    """Declare a computed field evaluated after dataclass ``__init__``.

    The *factory* callable receives the model instance and returns the value.
    Computed fields are excluded from TOML serialisation and deserialisation.
    """
    return dataclasses.field(init=False, default=None, metadata={"self_factory": factory})


def key_field() -> Any:
    """Declare a key field that stores the TOML section name.

    Key fields default to ``""`` and are excluded from TOML value
    serialisation.
    They are populated from the section heading when using
    :func:`list_from_toml`.
    """
    return dataclasses.field(default="", metadata={"toml_key": True})


def field_comment(default: Any, comment: str) -> Any:
    """Declare a field with a default value and an inline TOML comment.

    The comment is written as a ``# ...`` line immediately before the field
    when the config is serialised to TOML for the first time.
    It is *not* part of the in-place update path — existing user comments in
    the file are never overwritten.

    Example::

        show_hidden_files: bool = field_comment(False, "Show hidden files in the browser.")
    """
    return dataclasses.field(default=default, metadata={"toml_comment": comment})


# ── Type-inspection helpers ────────────────────────────────────────────────────


def _is_optional(t: Any) -> bool:
    """Return True if *t* is ``T | None``.

    Note: only handles the ``X | Y`` union syntax (``types.UnionType``),
    not ``Optional[X]`` from ``typing``.
    """
    origin = get_origin(t)
    if origin is not UnionType:
        return False
    args = get_args(t)
    return len(args) == 2 and type(None) in args  # noqa: PLR2004


def _unwrap_optional(t: Any) -> tuple[bool, Any]:
    """Return ``(is_optional, inner_type)``.

    For non-optional types returns ``(False, t)``.
    """
    if _is_optional(t):
        args = get_args(t)
        inner = next(a for a in args if a is not type(None))
        return True, inner
    return False, t


def _is_config_model_type(t: Any) -> bool:
    """Return True if *t* is a subclass of :class:`ConfigModel`."""
    return isinstance(t, type) and issubclass(t, BaseModel)


def _is_list_of_config_model(t: Any) -> bool:
    """Return True if *t* is ``list[SomeConfigModel]``."""
    if get_origin(t) is not list:
        return False
    args = get_args(t)
    return bool(args) and _is_config_model_type(args[0])


def _get_list_elem_type(t: Any) -> type[BaseModel]:
    """Return the element type of a ``list[ConfigModel]`` annotation."""
    return get_args(t)[0]  # type: ignore[return-value]


# ── Deserialisation ────────────────────────────────────────────────────────────


def from_toml[T: BaseModel](cls: type[T], table: Any, *, key: str = "", file_path: str = "") -> T:
    """Deserialise a tomlkit table into a :class:`ConfigModel` instance.

    Args:
        cls: The ConfigModel subclass to instantiate.
        table: A tomlkit ``Table`` or ``TOMLDocument`` to read from.
        key: Value assigned to :func:`key_field` fields (the TOML section name).
        file_path: Source file path used in error messages.

    Raises:
        ConfigLoadError: If a required field is absent from *table*.
    """
    hints = get_type_hints(cls)
    kwargs: dict[str, Any] = {}

    for f in dc_fields(cls):
        if "self_factory" in f.metadata:
            continue
        if "toml_key" in f.metadata:
            kwargs[f.name] = key
            continue

        field_type = hints[f.name]
        _, inner_type = _unwrap_optional(field_type)

        if f.name in table:
            raw = table[f.name]
            if _is_list_of_config_model(inner_type):
                elem_type = _get_list_elem_type(inner_type)
                kwargs[f.name] = [from_toml(elem_type, item) for item in raw]
            elif _is_config_model_type(inner_type):
                kwargs[f.name] = from_toml(inner_type, raw)  # type: ignore[type-var]
            else:
                kwargs[f.name] = raw
        else:
            if f.default is not dataclasses.MISSING:
                kwargs[f.name] = f.default
            elif f.default_factory is not dataclasses.MISSING:  # type: ignore[misc]
                kwargs[f.name] = f.default_factory()  # type: ignore[misc]
            else:
                loc = f" in {file_path}" if file_path else ""
                raise ConfigLoadError(f"Missing required field '{f.name}'{loc}")

    return cls(**kwargs)  # type: ignore[return-value]


# ── Serialisation ──────────────────────────────────────────────────────────────


def _populate_container(container: Any, obj: BaseModel) -> None:
    """Populate a tomlkit container (Table or TOMLDocument) with fields from *obj*.

    Skips computed fields, key fields, and ``None`` values.
    Nested ``ConfigModel`` fields become subtables.
    ``list[ConfigModel]`` fields become array-of-tables.
    """
    hints = get_type_hints(type(obj))

    for f in dc_fields(obj):  # type: ignore
        if "self_factory" in f.metadata or "toml_key" in f.metadata:
            continue

        value = getattr(obj, f.name)
        if value is None:
            continue

        field_type = hints[f.name]
        _, inner_type = _unwrap_optional(field_type)

        if _is_config_model_type(inner_type) and isinstance(value, BaseModel):
            container.add(f.name, _obj_to_table(value))
        elif _is_list_of_config_model(inner_type) and isinstance(value, list):
            aot = tomlkit.aot()
            for item in value:
                aot.append(_obj_to_table(item))
            container.add(f.name, aot)
        else:
            if "toml_comment" in f.metadata:
                container.add(tomlkit.comment(f.metadata["toml_comment"]))
            container.add(f.name, value)


def _explicit_docstring(cls: type) -> str | None:
    """Return the first line of the class docstring only if it was explicitly written.

    Python auto-generates a docstring for every ``@dataclass`` of the form
    ``ClassName(field: type, ...)``.  We suppress that by checking whether the
    docstring starts with the class name followed by ``(``.
    """
    doc = cls.__doc__
    if not doc:
        return None
    first_line = doc.strip().splitlines()[0].strip()
    if first_line.startswith(cls.__name__ + "("):
        return None
    return first_line


def _obj_to_table(obj: BaseModel) -> tomlkit.items.Table:
    """Convert a :class:`ConfigModel` instance to a tomlkit ``Table``."""
    table = tomlkit.table()
    cls = type(obj)
    first_line = _explicit_docstring(cls)
    if first_line:
        table.add(tomlkit.comment(first_line))
    _populate_container(table, obj)
    return table


def to_toml(obj: BaseModel) -> TOMLDocument:
    """Serialise a :class:`ConfigModel` instance to a tomlkit ``TOMLDocument``.

    The first line of the class docstring is written as a comment before the
    table content.
    Computed fields, key fields, and ``None`` values are omitted.
    Nested ``ConfigModel`` fields become subtables.
    ``list[ConfigModel]`` fields become array-of-tables.
    """
    doc = tomlkit.document()
    cls = type(obj)

    first_line = _explicit_docstring(cls)
    if first_line:
        doc.add(tomlkit.comment(first_line))

    _populate_container(doc, obj)
    return doc


# ── List-keyed serialisation ───────────────────────────────────────────────────


def list_from_toml[T: BaseModel](cls: type[T], doc: TOMLDocument, *, file_path: str = "") -> list[T]:
    """Deserialise a list of instances from a top-level-keyed TOML document.

    Each top-level key in *doc* becomes a :class:`ConfigModel` instance with
    that key assigned to its :func:`key_field`.
    """
    items: list[T] = []
    for section_name, table in doc.items():
        items.append(from_toml(cls, table, key=section_name, file_path=file_path))
    return items


def list_to_toml[T: BaseModel](items: list[T]) -> TOMLDocument:
    """Serialise a list of instances to a top-level-keyed TOML document.

    Each item's :func:`key_field` value is used as the TOML section heading.
    """
    doc = tomlkit.document()
    for item in items:
        key = ""
        for f in dc_fields(item):  # type: ignore
            if "toml_key" in f.metadata:
                key = str(getattr(item, f.name))
                break
        if not key:
            raise ValueError(f"list_to_toml: {type(item).__name__} has no key_field — cannot determine section name")
        doc.add(key, _obj_to_table(item))
    return doc


# ── In-place update ────────────────────────────────────────────────────────────


def _update_toml_container(container: Any, obj: BaseModel) -> None:
    """Recursively update a tomlkit container in-place with values from *obj*."""
    hints = get_type_hints(type(obj))

    for f in dc_fields(obj):  # type: ignore
        if "self_factory" in f.metadata or "toml_key" in f.metadata:
            continue

        field_type = hints[f.name]
        _, inner_type = _unwrap_optional(field_type)
        value = getattr(obj, f.name)

        if _is_list_of_config_model(inner_type):
            continue

        if _is_config_model_type(inner_type) and isinstance(value, BaseModel):
            if f.name in container:
                _update_toml_container(container[f.name], value)
            # else: update-only semantics — new nested tables are not added
        elif value is not None:
            container[f.name] = value


def update_toml_doc(doc: TOMLDocument, obj: BaseModel) -> None:
    """In-place update of an existing tomlkit document, preserving comments.

    Scalars are updated by key assignment (tomlkit preserves surrounding
    comments).
    Nested :class:`ConfigModel` subtables are recursed into.
    ``list[ConfigModel]`` fields are skipped (too complex for in-place update).
    """
    _update_toml_container(doc, obj)
