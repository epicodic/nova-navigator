"""Mapping from the saved file back to the pieces it was written from (design section 8.2; completed by a later task)."""

from __future__ import annotations

from array import array


class SaveLayout:
    """Records, for every written part, the source it came from, its range there and its offset in the new file."""

    def __init__(self) -> None:
        self._src = array("L")
        self._a = array("Q")
        self._b = array("Q")
        self._out = array("Q")

    def add(self, src: int, a: int, b: int, out: int) -> None:
        """Append the part `[a, b)` of source `src` written at offset `out` of the new file."""
        self._src.append(src)
        self._a.append(a)
        self._b.append(b)
        self._out.append(out)

    def __len__(self) -> int:
        return len(self._src)
