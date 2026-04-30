from .unicode import ljust


class Icon(str):
    """A fixed-width terminal icon glyph.

    Subclasses :class:`str` so it can be used wherever a string is expected,
    but ``len()`` always returns :attr:`ICON_WIDTH` (2) regardless of the
    underlying codepoint count, ensuring consistent column alignment in the TUI.
    A ``None`` glyph produces a blank placeholder of the correct width.
    """

    ICON_WIDTH = 2

    __slots__ = ("_color",)

    _color: tuple[int, int, int] | None

    def __new__(cls, glyph: str | None = None, *, color: tuple[int, int, int] | None = None) -> "Icon":
        if glyph is None:
            text = " " * cls.ICON_WIDTH
        else:
            text = ljust(glyph, cls.ICON_WIDTH)
        instance = super().__new__(cls, text)
        instance._color = color
        return instance

    def __len__(self) -> int:
        """Return the fixed display width of the icon."""
        return self.ICON_WIDTH

    @property
    def markup(self) -> str:
        """Return the icon as a Rich markup string.

        Wraps the glyph in ``[rgb(r,g,b)]...[/]`` markup when a color was
        given, otherwise returns the plain padded glyph.
        """
        if self._color is not None:
            r, g, b = self._color
            return f"[rgb({r},{g},{b})]{self!s}[/]"
        return str(self)
