import csv
import re
from collections.abc import Iterator
from enum import Enum
from pathlib import Path
from typing import ClassVar, TextIO

from nova_widgets import Icon


def _parse_icon_glyph(glyph_str: str) -> str:
    # replace occurences of U+XXXX or \uXXXX with the corresponding unicode character
    def replace_match(match: re.Match[str]) -> str:
        codepoint = int(match.group(1), 16)
        return chr(codepoint)

    return re.sub(r"(?:U\+|\\u)([0-9A-Fa-f]{4,6})", replace_match, glyph_str)


class IconSet:
    class Variants(Enum):
        NERDFONT = 0
        UNICODE = 1

    _glyph_variant: ClassVar[Variants] = Variants.NERDFONT

    Glyphs = tuple[str, str]

    _icons: dict[str, Glyphs]

    def load_icons(self, f: TextIO | Path) -> None:
        if isinstance(f, Path):
            with f.open(encoding="utf-8") as file:
                self._load_icons(file)
        else:
            self._load_icons(f)

    def _load_icons(self, f: TextIO) -> None:
        reader = csv.reader(filter(lambda row: len(row.strip()) > 0 and row[0] != "#", f), delimiter=",", quotechar='"')
        icons = {
            row[0]: (
                _parse_icon_glyph(row[1]) + " ",  # nerdfont glyphs have size of 2 but take only 1 character
                _parse_icon_glyph(row[2]),
            )
            for row in reader
        }
        self._icons = icons

    @classmethod
    def set_variant(cls, variant: Variants) -> None:
        cls._glyph_variant = variant

    @classmethod
    def get_variant(cls) -> Variants:
        return cls._glyph_variant

    def get_icon(self, name: str | None, default: Icon | None = None, variant: Variants | None = None) -> Icon:
        if default is None:
            default = Icon()
        if name is None:
            return default
        if variant is None:
            variant = IconSet._glyph_variant
        glyphs = self._icons.get(name)
        if glyphs is None:
            return default
        return Icon(glyphs[variant.value])

    def __iter__(self) -> Iterator[tuple[str, Glyphs]]:
        return iter(self._icons.items())


ICONS = IconSet()


def ico_(name: str | None, default: Icon | None = None) -> Icon:
    return ICONS.get_icon(name, default)
