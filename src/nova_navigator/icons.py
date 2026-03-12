import csv
import re
from collections.abc import Iterator
from enum import Enum
from pathlib import Path
from typing import ClassVar, TextIO

from nova_widgets import Icon

# Matches one grapheme cluster: base codepoint + optional variation selector / combining marks.
# Covers all glyphs in icons.csv. ZWJ sequences are not supported.
_GRAPHEME_RE = re.compile(r".\ufe0f?[\u0300-\u036f\ufe00-\ufe0f]*", re.DOTALL)

# Matches a single U+XXXX or \uXXXX codepoint token.
_CODEPOINT_RE = re.compile(r"(?:U\+|\\u)([0-9A-Fa-f]{4,6})")


def _parse_nerdfont_frames(cell: str) -> list[str]:
    """Return list of nerdfont glyph strings from a cell like ``U+ee06U+ee07``."""
    matches = _CODEPOINT_RE.findall(cell)
    # nerdfont glyphs take 2 columns but are 1 codepoint; pad with a trailing space
    return [chr(int(cp, 16)) + " " for cp in matches]


def _parse_unicode_frames(cell: str) -> list[str]:
    """Return list of grapheme-cluster strings from a cell like ``○◔◑◕●``."""

    # First expand any U+XXXX or \uXXXX escape sequences that may remain
    def _expand(m: re.Match[str]) -> str:
        return chr(int(m.group(1), 16))

    expanded = _CODEPOINT_RE.sub(_expand, cell)
    return _GRAPHEME_RE.findall(expanded)


class IconSet:
    class Variants(Enum):
        NERDFONT = 0
        UNICODE = 1

    _glyph_variant: ClassVar[Variants] = Variants.NERDFONT

    # Each entry stores (nerdfont_frames, unicode_frames)
    Glyphs = tuple[list[str], list[str]]

    _icons: dict[str, Glyphs]

    def load_icons(self, f: TextIO | Path) -> None:
        if isinstance(f, Path):
            with f.open(encoding="utf-8") as file:
                self._load_icons(file)
        else:
            self._load_icons(f)

    def _load_icons(self, f: TextIO) -> None:
        reader = csv.reader(
            filter(lambda row: len(row.strip()) > 0 and row[0] != "#", f),
            delimiter=",",
            quotechar='"',
        )
        icons: dict[str, IconSet.Glyphs] = {}
        for row in reader:
            name = row[0]
            nf_frames = _parse_nerdfont_frames(row[1])
            uni_frames = _parse_unicode_frames(row[2])
            icons[name] = (nf_frames, uni_frames)
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
        frames = glyphs[variant.value]
        if not frames:
            return default
        return Icon.from_glyphs(frames)

    def __iter__(self) -> Iterator[tuple[str, Glyphs]]:
        return iter(self._icons.items())


ICONS = IconSet()


def ico_(name: str | None, default: Icon | None = None) -> Icon:
    return ICONS.get_icon(name, default)
