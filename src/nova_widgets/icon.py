from typing import Literal

from .unicode import ljust


class Icon(str):
    ICON_WIDTH = 2

    __slots__ = ()

    def __new__(cls, glyph: str | None = None) -> "Icon":
        if glyph is None:
            text = " " * cls.ICON_WIDTH
        else:
            text = ljust(glyph, cls.ICON_WIDTH)
        return super().__new__(cls, text)

    def __len__(self) -> int:
        return self.ICON_WIDTH
