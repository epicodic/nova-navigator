"""Errors of the document layer that the history catches (kept apart so `_history` and `_lazy_document` need not import each other)."""

from __future__ import annotations

__all__ = ["EditsLocked", "RowUnavailable"]


class RowUnavailable(RuntimeError):
    """The row exists but its byte range is not resolvable yet: the scan has not reached its end, or the read budget was exceeded."""


class EditsLocked(RowUnavailable):
    """The document refuses edits at the moment (`LazyDocument.lock_edits`); the message holds the reason."""
