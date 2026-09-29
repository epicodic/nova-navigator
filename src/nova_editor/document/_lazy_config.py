"""Tunable limits of the lazy document (ACT3 design 4.1)."""

from __future__ import annotations

from dataclasses import dataclass


@dataclass(frozen=True)
class LazyConfig:
    """Thresholds and cache sizes of `LazyDocument`.

    Row classes by content byte length: short up to `word_wrap_limit`, medium up to `long_row_threshold`, long above.
    """

    stride: int = 64
    index_long_line_threshold: int = 16_384  # LineIndex side-table threshold (DEC-14)
    long_row_threshold: int = 1_048_576  # rows above this are never decoded whole
    word_wrap_limit: int = 65_536  # rows above this use grid wrap
    checkpoint_chars: int = 65_536
    tab_width: int = 4
    yield_seconds: float = 0.0
    max_long_indexes: int = 8
    text_cache_rows: int = 256
