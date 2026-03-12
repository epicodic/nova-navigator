from __future__ import annotations

from dataclasses import dataclass, field
from typing import ClassVar

from .unicode import ljust


@dataclass(frozen=True)
class Icon:
    """A fixed-width terminal icon, holding one or more display frames.

    Use :meth:`Icon.of` for a single-frame icon and :meth:`Icon.from_glyphs`
    for multi-frame (animated) icons.
    """

    ICON_WIDTH: ClassVar[int] = 2

    _frames: tuple[str, ...] = field(default_factory=tuple)
    color: tuple[int, int, int] | None = None

    @classmethod
    def of(cls, glyph: str | None = None, *, color: tuple[int, int, int] | None = None) -> Icon:
        """Single-frame constructor. ``None`` or omitted -> blank placeholder."""
        if glyph is None or glyph == "":
            return cls(_frames=(), color=color)
        return cls(_frames=(ljust(glyph, cls.ICON_WIDTH),), color=color)

    @classmethod
    def from_glyphs(cls, glyphs: list[str], *, color: tuple[int, int, int] | None = None) -> Icon:
        """Multi-frame constructor. Empty list -> blank placeholder."""
        return cls(_frames=tuple(ljust(g, cls.ICON_WIDTH) for g in glyphs), color=color)

    @property
    def glyph(self) -> str:
        """First frame string, padded to ICON_WIDTH. Two spaces when empty."""
        return self._frames[0] if self._frames else " " * self.ICON_WIDTH

    @property
    def markup(self) -> str:
        """Glyph wrapped in Rich [rgb(r,g,b)]...[/] markup when color is set."""
        if self.color is not None:
            r, g, b = self.color
            return f"[rgb({r},{g},{b})]{self.glyph}[/]"
        return self.glyph

    @property
    def frames(self) -> list[Icon]:
        """Each animation frame as a single-frame Icon carrying the same color."""
        if not self._frames:
            return [self]
        if len(self._frames) == 1:
            return [self]
        return [Icon(_frames=(f,), color=self.color) for f in self._frames]

    @property
    def is_animated(self) -> bool:
        """True when there are two or more frames."""
        return len(self._frames) > 1
