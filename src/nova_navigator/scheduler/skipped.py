from collections import Counter
from collections.abc import Sequence
from typing import NamedTuple

MAX_LISTED_REASONS = 3


class SkippedItem(NamedTuple):
    """An item a running task skipped, with the reason it was skipped."""

    item: str
    reason: str


def skipped_summary(skipped: Sequence[SkippedItem]) -> str | None:
    """Summarise skipped items for a completion notification.

    Args:
        skipped: The items a task reported as skipped.

    Returns:
        None when nothing was skipped; otherwise text such as
        ``"Skipped 3 items: broken link (2), link loop (1)"``. Reasons are grouped in first-seen order,
        empty reasons are left out, and at most three reasons are listed before ``…``.
    """
    if not skipped:
        return None
    total = len(skipped)
    text = f"Skipped {total} item{'' if total == 1 else 's'}"
    counts = Counter(entry.reason for entry in skipped if entry.reason)
    if not counts:
        return text
    parts = [f"{reason} ({count})" for reason, count in list(counts.items())[:MAX_LISTED_REASONS]]
    if len(counts) > MAX_LISTED_REASONS:
        parts.append("…")
    return f"{text}: {', '.join(parts)}"
