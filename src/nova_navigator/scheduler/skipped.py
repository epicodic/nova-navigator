from typing import NamedTuple


class SkippedItem(NamedTuple):
    """An item a running task skipped, with the reason it was skipped."""

    item: str
    reason: str
