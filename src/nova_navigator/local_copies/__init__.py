"""Registry of open local copies of non-local files, with their open and sync jobs."""

from __future__ import annotations

from .manager import CopyEntry, CopyStatus, LocalCopyManager

__all__ = ["CopyEntry", "CopyStatus", "LocalCopyManager"]
