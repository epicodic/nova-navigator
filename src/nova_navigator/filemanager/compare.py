"""Directory comparison logic for the Compare Directories feature."""

from __future__ import annotations

from enum import Enum

from nova_navigator.vfs.vpath import VPath


class CompareMode(Enum):
    BY_SIZE = "size"
    BY_MODIFICATION_TIME = "mtime"


# TODO: make these colors configurable by using styles
LEFT_ONLY_COLOR = "yellow"
RIGHT_ONLY_COLOR = "cyan"
DIFFERENT_COLOR = "red"


def compare_directories(
    left_items: list[VPath],
    right_items: list[VPath],
    mode: CompareMode | None,
) -> tuple[dict[str, str], dict[str, str]]:
    """Compare two directory listings and return per-filename color dicts.

    Directories are compared by name only.
    Files are compared by name, and optionally by size or modification time.
    If a stat field required by *mode* is unknown (-1 / -1.0), falls back to
    name-presence comparison for that file pair.

    Args:
        left_items: VPath items from the left panel (UpPath excluded).
        right_items: VPath items from the right panel (UpPath excluded).
        mode: Comparison criterion for files, or None for name-presence only.

    Returns:
        ``(left_colors, right_colors)`` — each maps filename to a Rich color
        string.  Files with no special state are omitted.
    """
    left_by_name: dict[str, VPath] = {p.name: p for p in left_items}
    right_by_name: dict[str, VPath] = {p.name: p for p in right_items}

    left_colors: dict[str, str] = {}
    right_colors: dict[str, str] = {}

    all_names = set(left_by_name) | set(right_by_name)
    for name in all_names:
        left_item = left_by_name.get(name)
        right_item = right_by_name.get(name)

        if left_item is None:
            right_colors[name] = RIGHT_ONLY_COLOR
        elif right_item is None:
            left_colors[name] = LEFT_ONLY_COLOR
        elif mode is not None and not left_item.stat.is_directory and _files_differ(left_item, right_item, mode):
            left_colors[name] = DIFFERENT_COLOR
            right_colors[name] = DIFFERENT_COLOR

    return left_colors, right_colors


def _files_differ(left: VPath, right: VPath, mode: CompareMode) -> bool:
    """Return True if two files with the same name differ according to *mode*."""
    if mode is CompareMode.BY_SIZE:
        ls, rs = left.stat.size, right.stat.size
        if ls == -1 or rs == -1:
            return False
        return ls != rs
    # BY_MODIFICATION_TIME
    lm, rm = left.stat.modified, right.stat.modified
    if lm == -1.0 or rm == -1.0:
        return False
    return lm != rm


def _files_identical(left: VPath, right: VPath, mode: CompareMode) -> bool:
    """Return True if two files with the same name are known to be equal according to *mode*.

    Unknown values (-1 / -1.0) are never identical, the opposite default of ``_files_differ``.
    """
    if mode is CompareMode.BY_SIZE:
        ls, rs = left.stat.size, right.stat.size
        return ls != -1 and rs != -1 and ls == rs
    # BY_MODIFICATION_TIME
    lm, rm = left.stat.modified, right.stat.modified
    return lm != -1.0 and rm != -1.0 and lm == rm


def identical_file_names(
    left_items: list[VPath],
    right_items: list[VPath],
    mode: CompareMode | None,
) -> frozenset[str]:
    """Return the names of files that are identical in both listings.

    A name qualifies when it exists on both sides, neither side is a directory,
    and the files are equal according to *mode*.

    Args:
        left_items: VPath items from the left panel (UpPath excluded).
        right_items: VPath items from the right panel (UpPath excluded).
        mode: Comparison criterion, or None (nothing is identical).

    Returns:
        The set of identical file names; empty when *mode* is None.
    """
    if mode is None:
        return frozenset()
    right_by_name: dict[str, VPath] = {p.name: p for p in right_items}
    names: set[str] = set()
    for left_item in left_items:
        right_item = right_by_name.get(left_item.name)
        if right_item is None or left_item.stat.is_directory or right_item.stat.is_directory:
            continue
        if _files_identical(left_item, right_item, mode):
            names.add(left_item.name)
    return frozenset(names)
