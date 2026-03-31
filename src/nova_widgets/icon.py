from .unicode import ljust


class Icon(str):
    """A fixed-width terminal icon glyph.

    Subclasses :class:`str` so it can be used wherever a string is expected,
    but ``len()`` always returns :attr:`ICON_WIDTH` (2) regardless of the
    underlying codepoint count, ensuring consistent column alignment in the TUI.
    A ``None`` glyph produces a blank placeholder of the correct width.
    """

    ICON_WIDTH = 2

    __slots__ = ()

    def __new__(cls, glyph: str | None = None) -> "Icon":
        if glyph is None:
            text = " " * cls.ICON_WIDTH
        else:
            text = ljust(glyph, cls.ICON_WIDTH)
        return super().__new__(cls, text)

    def __len__(self) -> int:
        """Return the fixed display width of the icon."""
        return self.ICON_WIDTH
