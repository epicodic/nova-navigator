import abc
from typing import ClassVar


class Scheme(abc.ABC):
    NAMES: ClassVar[list[str]]
    ROOT_SCHEME: ClassVar[bool]


class LocalScheme(Scheme):
    NAMES: ClassVar[list[str]] = ["file", "local"]  # a missing scheme also defaults to LocalScheme
    ROOT_SCHEME: ClassVar[bool] = True


class SchemeRegistry:
    _schemes: dict[str, Scheme]

    def __init__(self) -> None:
        self._schemes = {}

    def register_scheme(self, scheme: Scheme) -> None:
        for name in scheme.NAMES:
            if name in self._schemes:
                raise ValueError(f"Scheme '{name}' is already registered.")
            self._schemes[name] = scheme

    def get_scheme(self, name: str) -> Scheme | None:
        return self._schemes.get(name)


registry = SchemeRegistry()
