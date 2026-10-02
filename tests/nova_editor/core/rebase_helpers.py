"""Helpers shared by the rebase tests (and by the history tests of Task 12)."""

from __future__ import annotations

from collections.abc import Iterator

from nova_editor.core import Content, Piece


def walk_pieces(content: Content) -> Iterator[Piece]:
    """Yield every piece reference of `content`."""
    yield from content.pieces
