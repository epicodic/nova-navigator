from dataclasses import dataclass
from types import UnionType
from typing import Any, get_args, get_origin, overload

import tomlkit
import tomlkit.items
from tomlkit import TOMLDocument
from tomlkit.items import Table as TOMLTable


@dataclass
class FieldInfo:
    default: Any = None
    default_factory: Any = None
    exclude: bool = False


@overload
def Field(*, default_factory: Any = None, exclude: bool = False) -> Any: ...


@overload
def Field[T](*, default: T, default_factory: Any = None, exclude: bool = False) -> T: ...


def Field(*, default: Any = None, default_factory: Any = None, exclude: bool = False) -> Any:
    return FieldInfo(default, default_factory, exclude)


def _is_list(annotation_type: Any) -> bool:
    """Return True if the annotation represents a list (List[T] or list[T])."""
    origin = get_origin(annotation_type)
    return origin in [list]


def _get_list_element_type(annotation_type: Any) -> Any:
    """Return the element type of a list annotation (List[T] or list[T])."""
    if not _is_list(annotation_type):
        return None

    args = get_args(annotation_type)
    if args:
        return args[0]
    return None


def _is_optional(annotation_type: Any) -> bool:
    """Return True if the annotation represents an Optional[T]."""
    origin = get_origin(annotation_type)
    if origin is not UnionType:
        return False
    args = get_args(annotation_type)
    return len(args) == 2 and type(None) in args  # noqa: PLR2004


def _get_optional_type(annotation_type: Any) -> Any:
    """Return the inner type of an Optional[T]."""
    if not _is_optional(annotation_type):
        return None

    args = get_args(annotation_type)
    return next(arg for arg in args if arg is not type(None))


class TomlConfig:
    def __init__(self, toml: TOMLTable | TOMLDocument, **kwargs: Any) -> None:
        cls = self.__class__

        # process fields from kwargs first
        for key, value in kwargs.items():
            if key in cls.__annotations__:
                setattr(self, key, value)
            else:
                raise ValueError(f"Unknown field '{key}' for class '{cls.__name__}'")

        for field_name, field_type in self.__annotations__.items():
            if not hasattr(cls, field_name):
                field_info = FieldInfo()  # no Field provided, use default
            else:
                field_info = getattr(cls, field_name)

            if not isinstance(field_info, FieldInfo):
                raise TypeError(f"Field '{field_name}' is not a FieldInfo instance")

            # anaylse field type
            is_list = False
            is_optional = False
            inner_type = field_type

            if _is_optional(inner_type):
                is_optional = True
                inner_type = _get_optional_type(inner_type)

            if _is_list(inner_type):
                is_list = True
                inner_type = _get_list_element_type(inner_type)
            # TODO: optional list element type fails here

            # fmt: off
            #print(f"FIELD name:'{field_name}', type:'{field_type}', is_list:{is_list}, is_optional:{is_optional}, inner_type:'{inner_type}'")  # noqa
            # fmt: on

            if field_info.exclude:
                # do not read from TOML, use default only
                self._init_default(toml, field_name, field_info, is_optional)
                continue

            if field_name in toml:
                value = toml[field_name]

                if is_list:
                    if not isinstance(value, tomlkit.items.Array) and not isinstance(value, tomlkit.items.AoT):
                        raise TypeError(f"Field '{field_name}' is expected to be a TOML array")
                    setattr(self, field_name, [inner_type(item) for item in value])
                else:
                    setattr(self, field_name, inner_type(value))
            else:  # noqa: PLR5501
                # try default initialization
                if not self._init_default(toml, field_name, field_info, is_optional):
                    raise ValueError(f"Missing field '{field_name}' in TOML (class='{cls.__name__}')", toml)

    def _init_default(
        self, toml: TOMLTable | TOMLDocument, field_name: str, field_info: FieldInfo, is_optional: bool
    ) -> bool:
        if field_info.default is not None:
            setattr(self, field_name, field_info.default)
        elif field_info.default_factory is not None:
            setattr(self, field_name, field_info.default_factory(self))
        elif is_optional:
            setattr(self, field_name, None)
        else:
            return False  # no default available
        return True
