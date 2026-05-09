"""Shared utility helpers for the widgets package."""

from __future__ import annotations


def _title_case(name: str) -> str:
    """Convert a snake_case name to Title Case words."""
    return " ".join(word.capitalize() for word in name.split("_"))
